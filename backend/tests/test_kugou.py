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
