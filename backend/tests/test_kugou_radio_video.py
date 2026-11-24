import io
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import cm_kugou as kg
import cm_kugou_catalog as catalog
import cm_kugou_library as library
import test_music_service as harness
from test_kugou import CONFIG, ID, ORIGIN


VIDEO = "b" * 32
UPSTREAM = "http://fsmvpc.kugou.com/fixture.mp4"


class KugouRadioAndVideoTests(unittest.TestCase):
    setUp = harness.MusicServiceTests.setUp
    _cleanup = harness.MusicServiceTests._cleanup
    account = harness.MusicServiceTests.account
    call = harness.MusicServiceTests.call
    operation = harness.MusicServiceTests.operation

    def test_radio_uses_account_history_and_persistent_exclusion(self):
        uid, bearer = self.account("openid music.library.read music.library.write music.history.write")
        _, other = self.account("openid music.library.read", title="another")
        skipped = "c" * 32 + "-42"
        with patch.dict(os.environ, CONFIG), patch.object(kg, "tracks", return_value=[
            kg._track(ID, "Fixture", "Artist", "", "", 100000),
        ]), patch.object(catalog, "radio", return_value={"items": [], "nextOffset": None}) as radio:
            self.assertEqual(self.operation("roaming.songs", provider="kugou")[0], 401)
            self.assertEqual(self.operation("history.record", {"id": ID}, bearer, True, "kugou")[0], 200)
            self.assertEqual(self.operation("roaming.skip", {"id": skipped}, bearer, True, "kugou")[0], 200)
            self.assertEqual(self.operation("roaming.songs", {"limit": 7}, bearer, provider="kugou")[0], 200)
            radio.assert_called_with(7, 0, ["123"], {ID, skipped}, "")
            self.assertEqual(library.roaming_exclusions(uid), {skipped})
            self.assertEqual(self.operation("roaming.songs", bearer=other, provider="kugou")[0], 200)
            radio.assert_called_with(20, 0, [], set(), "")
            self.assertEqual(self.operation("roaming.songs", {"station": "12_bad"}, bearer, provider="kugou")[0], 400)

    def test_comment_threads_use_the_returned_upstream_child_identifier(self):
        root = {'id': 12345, 'content': 'Root', 'special_id': 3011, 'special_child_id': 'child_7', 'reply_num': 3}
        reply = {'id': 98765, 'content': 'Reply', 'addtime': '2026-11-20 10:00:00'}
        with (patch.dict(os.environ, CONFIG), patch.object(kg, 'track'),
              patch.object(catalog, '_read', side_effect=[
                  {'list': [root], 'count': 1, 'maxPage': 1},
                  {'list': [reply], 'tid': 12345, 'childrenid': 'child_7', 'err_code': 0, 'comments_num': 3},
                  {'list': [reply], 'tid': 12345, 'childrenid': 'other', 'err_code': 0},
              ]) as upstream):
            status, comments = self.operation('track.comments', {'id': ID}, provider='kugou')
            self.assertEqual((status, comments['comments'][0]['replyThread']), (200, 'child_7'))
            request = {'id': ID, 'commentId': '12345', 'replyThread': 'child_7', 'page': 2, 'limit': 1}
            status, data = self.operation('track.comment.replies', request, provider='kugou')
            self.assertEqual((status, data['comments'][0]['id'], data['more']), (200, '98765', True))
            upstream.assert_called_with('/comment/floor', {'mixsongid': '123', 'tid': '12345', 'special_id': 'child_7', 'page': 2, 'pagesize': 1})
            self.assertEqual(self.operation('track.comment.replies', request, provider='kugou')[0], 502)

    def test_video_gated_by_playback_permission(self):
        _, bearer = self.account("openid music.play")
        with patch.dict(os.environ, CONFIG), patch.object(kg, "resolve_video", return_value={"format": "mp4"}):
            self.assertEqual(self.operation("playback.mv", {"id": VIDEO}, provider="kugou")[0], 401)
            self.assertEqual(self.operation("playback.mv", {"id": VIDEO}, bearer, provider="kugou")[0], 200)
            with patch.dict(os.environ, {"CM_KUGOU_PLAYBACK": "0"}):
                self.assertEqual(self.operation("playback.mv", {"id": VIDEO}, bearer, provider="kugou")[0], 404)

    def test_video_resolve_requires_explicit_privilege_valid_source_and_probe(self):
        reply = {"status": 1, "data": {VIDEO: {"downurl": UPSTREAM}},
                 "privileges": {VIDEO: 0}}
        with patch.object(kg, "_catalog_cookie", return_value="fixture"),                 patch.object(kg, "_api", return_value=reply),                 patch.object(kg, "_verify_video", return_value=True) as verify,                 patch.object(kg, "_issue", return_value="fixture-ticket") as issue:
            result = kg.resolve_video(VIDEO, 1, ORIGIN)
            self.assertEqual(result, {"url": ORIGIN + "/cm/kugou/media/fixture-ticket", "format": "mp4", "expiresIn": 480})
            issue.assert_called_once_with(UPSTREAM, 1, "video/mp4", video=True)
            reply["privileges"][VIDEO] = 1
            with self.assertRaises(kg.Error):
                kg.resolve_video(VIDEO, 1, ORIGIN)
            reply["privileges"][VIDEO] = 0
            reply["data"][VIDEO]["downurl"] = "http://127.0.0.1/fixture.mp4"
            with self.assertRaises(kg.Error):
                kg.resolve_video(VIDEO, 1, ORIGIN)
            verify.assert_called_once_with(UPSTREAM)

    def test_mp4_probe_and_range_relay(self):
        with patch.object(kg._opener, "open") as request, patch.dict(os.environ, CONFIG):
            response = io.BytesIO(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00")
            response.status = 206
            response.headers = {"Content-Range": "bytes 0-15/100", "Content-Length": "16"}
            request.return_value = response
            self.assertTrue(kg._verify_video(UPSTREAM))
            self.assertEqual(request.call_args.args[0].headers, {"Range": "bytes=0-15"})
            media = io.BytesIO(b"example")
            media.status = 206
            media.headers = {"Content-Length": "7", "Content-Range": "bytes 0-6/100"}
            request.return_value = media
            handler = SimpleNamespace(headers={"Range": "bytes=0-6"}, wfile=io.BytesIO(), send_response=Mock(),
                                      send_header=Mock(), end_headers=Mock())
            token = kg._issue(UPSTREAM, 2, "video/mp4", video=True)
            kg.stream(handler, token, lambda uid: uid == 2)
            handler.send_response.assert_called_once_with(206)
            handler.send_header.assert_any_call("Content-Type", "video/mp4")
            self.assertEqual(handler.wfile.getvalue(), b"example")
