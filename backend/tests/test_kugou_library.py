import os
import unittest
from contextlib import closing, ExitStack
from unittest.mock import patch

import cm_db
import cm_kugou
from test_kugou import CONFIG, ID
import test_music_service as harness


SECOND = "b" * 32 + "-456"
SCOPES = "openid music.library.read music.library.write music.history.read music.history.write"


class KugouPersonalLibraryTests(unittest.TestCase):
    setUp = harness.MusicServiceTests.setUp
    _cleanup = harness.MusicServiceTests._cleanup
    account = harness.MusicServiceTests.account
    call = harness.MusicServiceTests.call
    operation = harness.MusicServiceTests.operation

    def configure(self):
        uid, token = self.account(SCOPES)
        outsider, other_token = self.account(SCOPES, title="other-listener")
        _, readonly = self.account("openid music.library.read", uid)
        stack = self.enterContext(ExitStack())
        stack.enter_context(patch.dict(os.environ, CONFIG))
        stack.enter_context(patch.object(cm_kugou, "server_cookie", side_effect=AssertionError("library touched the VIP cookie")))
        self.tracks = stack.enter_context(patch.object(cm_kugou, "tracks", side_effect=lambda ids: [
            cm_kugou._track(ident, "Test track", "Test singer", "Test album", "", 200000) for ident in ids
        ]))
        return (uid, token), (outsider, other_token), readonly

    def kg(self, op, body=None, token="", confirm=False):
        return self.operation(op, body, token, confirm, "kugou")

    def test_create_batch_edit_delete_and_ownership(self):
        (_, owner), (_, outsider), reader = self.configure()
        create = {"name": "Test playlist", "description": "First description", "trackIds": [ID, SECOND, ID]}
        self.assertEqual(self.kg("library.playlist.create", create, reader, True)[0], 403)
        self.assertEqual(self.kg("library.playlist.create", create, owner)[1]["code"], "confirmation_required")
        status, body = self.kg("library.playlist.create", create, owner, True)
        self.assertEqual((status, body["resource"]["trackCount"]), (200, 2))
        self.tracks.assert_called_once_with([ID, SECOND])
        pid = body["resource"]["id"]
        self.assertEqual(self.kg("library.playlists", token=outsider)[1]["items"], [])
        self.assertEqual(self.kg("library.playlist", {"id": pid}, outsider)[0], 404)
        self.assertEqual(self.kg("library.playlist.edit", {"id": pid, "name": "Foreign"}, outsider, True)[0], 404)
        changed = self.kg("library.playlist.edit", {"id": pid, "name": "Renamed"}, owner, True)[1]["resource"]
        self.assertEqual((changed["name"], changed["description"]), ("Renamed", "First description"))
        page = self.kg("library.playlist", {"id": pid, "limit": 1}, owner)[1]
        self.assertEqual(([t["id"] for t in page["tracks"]], page["nextOffset"]), ([ID], 1))
        page = self.kg("library.playlist", {"id": pid, "limit": 1, "offset": 1}, owner)[1]
        self.assertEqual(([t["id"] for t in page["tracks"]], page["nextOffset"]), ([SECOND], None))
        for _ in range(2):
            self.assertEqual(self.kg("library.playlist.tracks", {"id": pid, "trackIds": [ID], "selected": False}, owner, True)[0], 200)
        self.assertEqual(self.kg("library.playlist.delete", {"id": pid}, owner, True)[0], 200)
        with closing(cm_db.connect()) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM kugou_playlist_tracks").fetchone()[0], 0)

    def test_favorites_are_account_scoped_for_web_and_external_access(self):
        (uid, owner), (_, outsider), _ = self.configure()
        self.assertEqual(self.kg("library.favorites")[0], 401)
        web = cm_db.issue_token(uid)
        args = {"id": ID, "selected": True}
        path = "/kugou/v1/library.favorite.set"
        self.assertEqual(self.call("POST", path, args, web)[1]["code"], "confirmation_required")
        self.assertEqual(self.call("POST", path, {**args, "confirm": True}, web)[0], 200)
        self.assertEqual(self.kg("library.favorite.status", {"ids": [ID]}, owner)[1]["selectedIds"], [ID])
        self.assertEqual(self.kg("library.favorites", token=owner)[1]["total"], 1)
        self.assertEqual(self.kg("library.favorites", token=outsider)[1]["items"], [])
        self.assertEqual(self.operation("library.favorites", bearer=owner)[1]["items"], [])
        self.kg("library.favorite.set", {"id": ID, "selected": False}, owner, True)
        self.assertEqual(self.kg("library.favorites", token=owner)[1]["items"], [])

    def test_history_progress_is_monotonic_and_owned(self):
        (_, owner), (_, outsider), _ = self.configure()
        play = self.kg("history.record", {"id": ID}, owner, True)[1]["playId"]
        data = {"id": ID, "playId": play, "elapsedMs": 12345}
        self.assertEqual(self.kg("history.record", data, outsider, True)[0], 404)
        self.assertEqual(self.kg("history.record", {**data, "id": SECOND}, owner, True)[0], 404)
        self.assertEqual(self.kg("history.record", data, owner, True)[0], 200)
        self.assertEqual(self.kg("history.record", {**data, "elapsedMs": 500}, owner, True)[0], 200)
        self.kg("history.record", {"id": SECOND}, owner, True)
        self.kg("history.record", {"id": ID}, owner, True)
        self.assertEqual([t["id"] for t in self.kg("history.recent", token=owner)[1]["items"]], [ID, SECOND])
        self.assertEqual(self.kg("history.recent", token=outsider)[1]["items"], [])
        with closing(cm_db.connect()) as conn:
            self.assertEqual(conn.execute("SELECT elapsed_ms FROM kugou_plays WHERE id=?", (play,)).fetchone()[0], 12345)

    def test_unknown_recording_rolls_back_batch_create(self):
        (_, owner), _, _ = self.configure()
        self.tracks.side_effect = cm_kugou.Error(404, "not_found", "unknown")
        result = self.kg("library.playlist.create", {"name": "Should not persist", "trackIds": [ID, SECOND]}, owner, True)
        self.assertEqual(result[0], 404)
        self.assertEqual(self.kg("library.playlists", token=owner)[1]["items"], [])
