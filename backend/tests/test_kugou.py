from base64 import b64encode
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import test_music_service as harness
import cm_kugou as kg
import cm_kugou_catalog as catalog
import cm_music_service as service


ID = "a" * 32 + "-123"
ORIGIN = "https://provider.example.test"
CDN = "http://fs.youthandroid2.kugou.com/protected-test"
CONFIG = {"CM_KUGOU_BASE": "http://127.0.0.1:3201", "CM_KUGOU_PLAYBACK": "1",
          "CM_KUGOU_COOKIE_FILE": "dummy", "CM_MUSIC_SERVICE_MEDIA": ""}


class KugouGatewayTests(unittest.TestCase):
    setUp = harness.MusicServiceTests.setUp
    _cleanup = harness.MusicServiceTests._cleanup
    call = harness.MusicServiceTests.call
    account = harness.MusicServiceTests.account
    operation = harness.MusicServiceTests.operation

    def test_separate_capability_and_authentication(self):
        uid, bearer = self.account("openid music.play")
        with patch.dict(os.environ, CONFIG), patch.object(kg, "resolve", return_value={"quality": "standard"}) as resolve:
            _, descriptor, _ = self.call("GET", "/music-service/descriptor")
            self.assertIn("playback.resolve", {op["id"] for op in descriptor["platforms"]["kugou"]})
            self.assertNotIn("playback.resolve", {op["id"] for op in descriptor["platforms"]["netease"]})
            self.assertEqual(self.operation("playback.resolve", {"id": ID}, provider="kugou")[0], 401)
            status, result = self.operation("playback.resolve", {"id": ID}, bearer=bearer, provider="kugou")
            self.assertEqual((status, result["quality"]), (200, "standard"))
            resolve.assert_called_once_with(ID, "auto", uid, ORIGIN)
            with patch.object(kg, "authorized_user", return_value=False):
                self.assertEqual(self.operation("playback.resolve", {"id": ID}, bearer=bearer, provider="kugou")[0], 403)

    def test_web_and_external_catalog_use_the_same_operations(self):
        with patch.dict(os.environ, CONFIG), patch.object(kg, "search", return_value={"items": []}) as search:
            status, result, headers = self.call("POST", "/kugou/v1/catalog.search", {"query": "fixture"})
            self.assertEqual((status, result["items"], headers["Cache-Control"]), (200, [], "no-store"))
            search.assert_called_once_with("fixture", 0, 30)
            self.assertEqual(self.call("POST", "/kugou/v1/playback.resolve", {"id": ID})[0], 401)


    def test_catalogue_and_recommendation_authorization(self):
        _, bearer = self.account("openid music.library.read")
        with patch.dict(os.environ, {**CONFIG, "CM_KUGOU_PLAYBACK": "0"}), \
                patch.object(catalog, "recommendations", return_value={"items": []}), \
                patch.object(catalog, "lyrics", return_value={"lines": []}):
            _, descriptor, _ = self.call("GET", "/music-service/descriptor")
            entries = {op["id"]: op for op in descriptor["platforms"]["kugou"]}
            self.assertEqual(set(entries["catalog.search"]["kinds"]), set(catalog.SEARCH_TYPES))
            self.assertNotIn("playback.resolve", entries)
            self.assertEqual(self.operation("track.lyrics", {"id": ID}, provider="kugou")[0], 200)
            self.assertEqual(self.operation("discover.recommendations", {}, provider="kugou")[0], 401)
            self.assertEqual(self.operation("discover.recommendations", {}, provider="kugou", bearer=bearer)[0], 200)


class KugouAdapterTests(unittest.TestCase):
    def test_private_cookie_file_can_be_rotated(self):
        with tempfile.TemporaryDirectory() as path, patch.dict(os.environ, {"CM_KUGOU_COOKIE_FILE": path + "/cookie"}):
            cookie = Path(path) / "cookie"
            for token in ("initial", "rotated"):
                replacement = Path(path) / "new"
                replacement.write_text(f"token={token}; userid=123; dfid=device\n", encoding="ascii")
                replacement.chmod(0o600)
                replacement.replace(cookie)
                self.assertIn(f"token={token}", kg.server_cookie())
            cookie.chmod(0o644)
            with self.assertRaises(kg.Error) as err:
                kg.server_cookie()
            self.assertEqual(err.exception.status, 503)

    def test_playback_uses_private_cookie_and_rejects_preview_and_bad_cdn(self):
        cookie = "fixture-only"

        def api(path, params=None, **kwargs):
            self.assertEqual(kwargs["cookie"], cookie)
            if path == "/user/verify":
                return {"data": {"auth": "fixture"}}
            if path == "/song/auth":
                return {"data": {"auth": "fixture", "open_time": 123}}
            if params["quality"] == "flac":
                return {"status": 1, "url": ["http://127.0.0.1/invalid"]}
            if params["quality"] == "320":
                return {"status": 1, "url": [CDN], "is_free_part": 1}
            return {"status": 1, "url": [CDN]}

        with patch.object(kg, "server_cookie", return_value=cookie), \
                patch.object(kg, "track", return_value=({"durationMs": 200000}, {}, {})), \
                patch.object(kg, "_variants", return_value={level: ["a" * 32] for level in kg._levels}), \
                patch.object(kg, "_api", side_effect=api), patch.object(kg, "_probe", return_value=("vorbis", 200500, 90000)) as probe, \
                patch.object(kg, "_issue", return_value="fixture-ticket"):
            result = kg.resolve(ID, "auto", 1, ORIGIN)
            self.assertEqual(result["url"], ORIGIN + "/cm/kugou/media/fixture-ticket")
            self.assertEqual((result["quality"], result["format"], result["bitrate"]), ("standard", "ogg", 90000))
            self.assertNotIn(cookie, json.dumps(result))
            probe.assert_called_once_with(CDN)
            with self.assertRaises(kg.Error) as err:
                kg.resolve(ID, "exhigh", 1, ORIGIN)
            self.assertEqual(err.exception.code, "playback_unavailable")

    def test_range_response_and_ticket_expiry(self):
        response = io.BytesIO(b"audio")
        response.status = 206
        response.headers = {"Content-Length": "5", "Content-Range": "bytes 5-9/30"}
        handler = SimpleNamespace(headers={"Range": "bytes=5-9"}, wfile=io.BytesIO(), send_response=Mock(),
                                  send_header=Mock(), end_headers=Mock())
        with patch.dict(kg._tickets, clear=True), patch.dict(os.environ, CONFIG), \
                patch.object(kg._opener, "open", return_value=response) as request:
            expired = kg._issue(CDN, 7, ttl=0)
            with self.assertRaises(kg.Error) as err:
                kg.stream(handler, expired, lambda _: True)
            self.assertEqual(err.exception.status, 410)
            ticket = kg._issue(CDN, 7, "audio/ogg")
            with self.assertRaises(kg.Error) as err:
                kg.stream(handler, ticket, lambda _: False)
            self.assertEqual(err.exception.status, 403)
            kg.stream(handler, ticket, lambda uid: uid == 7)
            handler.send_response.assert_called_once_with(206)
            handler.send_header.assert_any_call("Content-Range", "bytes 5-9/30")
            self.assertEqual(request.call_args.args[0].headers, {"Range": "bytes=5-9"})
            self.assertEqual(handler.wfile.getvalue(), b"audio")


class KugouCatalogueTests(unittest.TestCase):
    def test_album_search_preserves_offset_across_upstream_page_boundaries(self):
        def upstream(path, params):
            self.assertEqual(path, "/search")
            self.assertEqual(params["type"], "album")
            size, page = params["pagesize"], params["page"]
            first = (page - 1) * size + 1
            rows = [{"albumid": i, "albumname": f"Album {i}"} for i in range(first, min(first + size, 33))]
            return {"data": {"lists": rows, "total": 32}}

        with patch("cm_kugou_catalog._read", side_effect=upstream):
            result = catalog.search("fixture", "album", 28, 3)
            self.assertEqual([item["id"] for item in result["items"]], ["29", "30", "31"])
            self.assertEqual(result["nextOffset"], 31)
            result = catalog.search("fixture", "album", 31, 3)
            self.assertEqual([item["id"] for item in result["items"]], ["32"])
            self.assertIsNone(result["nextOffset"])

    def test_krc_uses_absolute_word_timestamps_and_hides_access_key(self):
        replies = [
            {"candidates": [{"id": "123", "accesskey": "fixture-key"}]},
            {"decodeContent": "[1000,350]<0,100,0>甲<100,250,0>乙"},
        ]
        with patch.object(kg, "track", return_value=({"durationMs": 2000}, {}, {})), \
                patch("cm_kugou_catalog._read", side_effect=replies):
            result = catalog.lyrics(ID)
        self.assertEqual(result["source"], "kugou:krc")
        self.assertTrue(result["wordLevel"])
        self.assertEqual(result["lines"][0]["w"], [[1000, "甲"], [1100, "乙"]])
        self.assertNotIn("fixture-key", json.dumps(result))


    def test_krc_translation_stays_on_original_line_across_blank_timestamps(self):
        aux = {"content": [
            {"type": 1, "language": 0, "lyricContent": [["早安"], [], ["晚上好"]]},
            {"type": 0, "language": 0, "lyricContent": [["o", "ha", "yo"], [], ["kon", "ban", "wa"]]},
        ]}
        tag = b64encode(json.dumps(aux).encode()).decode()
        raw = f"[language:{tag}]\r\n[1000,350]<0,100,0>おはよう\r\n[2000,300]\r\n[3000,450]<0,200,0>こんばんは"
        lines = catalog._krc_lines(raw)
        self.assertEqual([(line["t"], line["trans"], line["roma"]) for line in lines],
                         [(1000, "早安", "o ha yo"), (3000, "晚上好", "kon ban wa")])
        self.assertEqual(lines[0]["w"], [[1000, "おはよう"]])
        self.assertEqual(catalog._krc_lines("[language:@bad]\n[1000,350]<0,100,0>Hi")[0]["trans"], "")

    def test_private_collection_is_excluded_before_loading_tracks(self):
        collection = "collection_3_100_1_0"
        with patch.object(catalog, "_read", return_value={
            "data": [{"global_collection_id": collection, "name": "Private fixture", "is_pri": 1}],
        }) as read:
            with self.assertRaises(kg.Error) as error:
                catalog.details("playlist", collection, 0, 20)
        self.assertEqual(error.exception.status, 404)
        read.assert_called_once_with("/playlist/detail", {"ids": collection})
