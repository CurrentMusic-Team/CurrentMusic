import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cm_db
import cm_kugou as kg
import cm_kugou_account as account
import test_music_service as harness
from test_kugou import CONFIG, ID


FIXTURE_JAR = {"userid": "123", "token": "fixture-account-cookie", "dfid": "fixture-device"}
MOBILE = "19900000000"
SCOPES = "openid music.cloud.read music.comment.write"


class KugouAccountTests(unittest.TestCase):
    def setUp(self):
        harness.MusicServiceTests.setUp(self)
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.dict(os.environ, {**CONFIG, "CM_KUGOU_ACCOUNT_DIR": str(self.directory)}))
        self.enterContext(patch.dict(account._pending, clear=True))
        self.enterContext(patch.dict(account._sms_usage, clear=True))

    _cleanup = harness.MusicServiceTests._cleanup
    call = harness.MusicServiceTests.call
    operation = harness.MusicServiceTests.operation
    account = harness.MusicServiceTests.account

    def test_authenticated_binding_owns_challenge_and_session(self):
        uid, external = self.account(SCOPES)
        second, _ = self.account(SCOPES, title="other-user")
        token = cm_db.issue_token(uid)
        other = cm_db.issue_token(second)
        route = "/kugou/account"

        def upstream(path, **kwargs):
            if path == "/captcha/sent":
                self.assertEqual(kwargs["payload"], {"mobile": MOBILE})
                kwargs["jar"]["KUGOU_API_GUID"] = "fixture-guid"
                return {"status": 1}
            if path == "/login/cellphone":
                self.assertEqual(kwargs["payload"], {"mobile": MOBILE, "code": "123456"})
                kwargs["jar"].update(FIXTURE_JAR)
                return {"status": 1}
            if path == "/user/verify":
                self.assertIn("token=" + FIXTURE_JAR["token"], kwargs["cookie"])
                return {"status": 1, "data": {"auth": "fixture-auth"}}
            raise AssertionError(path)

        with patch.object(kg, "_api", side_effect=upstream) as api:
            self.assertEqual(self.call("POST", route + "/code", {"mobile": MOBILE}, external)[0], 401)
            self.assertEqual(self.call("POST", route + "/code", {"mobile": MOBILE}, token)[0], 200)
            self.assertEqual(self.call("POST", route + "/code", {"mobile": MOBILE}, other)[0], 429)
            self.assertEqual(self.call("POST", route + "/verify", {"code": "123456"}, other)[0], 400)
            status, result, headers = self.call("POST", route + "/verify", {"code": "123456"}, token)
            self.assertEqual((status, result, headers["Cache-Control"]), (200, {"bound": True, "enabled": True}, "no-store"))
            self.assertEqual(api.call_count, 3)
            self.assertEqual((self.directory / f"{uid}.cookie").stat().st_mode & 0o777, 0o600)
            self.assertFalse(self.call("GET", route, bearer=other)[1]["bound"])
            self.assertEqual(self.call("POST", route + "/verify", {"code": "123456"}, token)[0], 400)
            self.assertEqual(self.call("DELETE", route, bearer=other)[0], 200)
            self.assertTrue(self.call("GET", route, bearer=token)[1]["bound"])
            self.assertEqual(self.call("DELETE", route, bearer=token)[1]["bound"], False)

    def test_cloud_uses_only_personal_session_and_paginates_string_data(self):
        uid, token = self.account(SCOPES)
        _, other = self.account(SCOPES, title="second")
        account._save(uid, FIXTURE_JAR)

        def upstream(path, params=None, *, cookie="", payload=None, playback=False):
            self.assertEqual(path, "/user/cloud")
            self.assertEqual(cookie, account._header(FIXTURE_JAR))
            first = 1 + (params["page"] - 1) * 50
            return {"status": 1, "data": {
                "list": json.dumps([{"kv_id": i, "filename": str(i), "hash_std": "a" * 32,
                                     "album_audio_id": 123, "file_size": 100} for i in range(first, min(first + 50, 53))]),
                "list_count": 52, "used_size": 5200, "max_size": 10000,
            }}

        with patch.object(kg, "server_cookie", side_effect=AssertionError("shared playback session used")),                 patch.object(kg, "_api", side_effect=upstream) as api:
            status, first = self.operation("library.cloud", {"offset": 48, "limit": 3}, token, provider="kugou")
            self.assertEqual((status, first["nextOffset"], first["total"], first["usedBytes"]), (200, 51, 52, 5200))
            self.assertEqual([item["id"] for item in first["items"]], ["kgc_49", "kgc_50", "kgc_51"])
            self.assertEqual(first["items"][0]["catalogId"], ID)
            self.assertNotIn(FIXTURE_JAR["token"], json.dumps(first))
            self.assertEqual(self.operation("library.cloud", bearer=other, provider="kugou")[1]["code"], "binding_required")
            self.assertEqual(api.call_count, 2)

    def test_comment_checks_scope_confirmation_and_upstream_result(self):
        uid, token = self.account(SCOPES)
        _, no_write = self.account("openid music.cloud.read", uid)
        account._save(uid, FIXTURE_JAR)
        data = {"id": ID, "text": "fixture comment"}
        with patch.object(kg, "track", return_value=({"name": "Test song"}, {}, {})),                 patch.object(kg, "server_cookie", side_effect=AssertionError("shared session used")),                 patch.object(kg, "_api", return_value={"status": 1, "data": {"id": 1234}}) as mock:
            self.assertEqual(self.operation("track.comment.add", data, no_write, True, "kugou")[0], 403)
            self.assertEqual(self.operation("track.comment.add", data, token, False, "kugou")[0], 400)
            status, body = self.operation("track.comment.add", data, token, True, "kugou")
            self.assertEqual((status, body), (200, {"ok": True, "commentId": "1234"}))
            self.assertEqual(mock.call_args.args[0], "/comment/music/send")
            self.assertEqual(mock.call_args.kwargs["cookie"], account._header(FIXTURE_JAR))
            self.assertEqual(mock.call_args.kwargs["payload"]["content"], data["text"])
            mock.return_value = {"status": 0, "err_code": 100}
            self.assertEqual(self.operation("track.comment.add", data, token, True, "kugou")[1]["code"], "comment_unavailable")

    def test_unprotected_file_or_symlink_is_rejected(self):
        uid, token = self.account(SCOPES)
        account._save(uid, FIXTURE_JAR)
        path = self.directory / f"{uid}.cookie"
        path.chmod(0o644)
        self.assertEqual(self.operation("library.cloud", bearer=token, provider="kugou")[1]["code"], "binding_required")
        target = self.directory / "symlink-target"
        path.rename(target)
        target.chmod(0o600)
        path.symlink_to(target)
        self.assertFalse(account.status(uid)["bound"])
        self.directory.chmod(0o755)
        self.assertEqual(self.operation("library.cloud", bearer=token, provider="kugou")[0], 404)
