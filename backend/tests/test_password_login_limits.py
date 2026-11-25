"""Regression tests for password admission and persistent account limits."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from test_password_reset import _Handler
import cm_db
import cm_server


class PasswordLoginLimitsTest(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        self.enterContext(patch.object(cm_db, "DB_PATH", str(Path(directory) / "auth.db")))
        self.enterContext(patch.object(cm_db.time, "time", return_value=1_800_000_000))
        cm_db.init()

    def test_linked_phone_and_username_share_persistent_limit(self):
        uid = cm_db.create_user("fixture_login", "correct password", "昵称", phone="2025550100")
        for name in ("fixture_login", "2025550100", "fixture_login", "2025550100", "fixture_login"):
            self.assertIsNone(cm_db.verify_login(name, "incorrect"))

        cm_db.init()
        handler = _Handler("POST", "/auth/login", {"username": "2025550100", "password": "correct password"},
                           ip="192.0.2.74")
        headers = {}
        handler.send_header = headers.__setitem__
        cm_server.route(handler, "POST")
        self.assertEqual(handler.status, 429)
        self.assertEqual(headers["Retry-After"], "900")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(set(json.loads(handler.wfile.getvalue())), {"error"})

        cm_db.set_password(uid, "reset password")
        self.assertEqual(cm_db.verify_login("fixture_login", "reset password")["id"], uid)

    def test_concurrent_reservations_and_sliding_expiry(self):
        def reserve(_):
            try:
                return cm_db.reserve_password_attempt("fixture:concurrent")
            except cm_db.PasswordLoginLimited:
                return None

        with ThreadPoolExecutor(max_workers=8) as workers:
            accepted = [attempt for attempt in workers.map(reserve, range(8)) if attempt is not None]
        self.assertEqual(len(set(accepted)), 5)

        cm_db.finish_password_attempt(accepted[0])
        with patch.object(cm_db.time, "time", return_value=1_800_000_020):
            self.assertIsNotNone(reserve(None))
        with patch.object(cm_db.time, "time", return_value=1_800_000_900):
            for _ in range(4):
                self.assertIsNotNone(reserve(None))
            self.assertIsNone(reserve(None))

    def test_successful_password_checks_release_reservations(self):
        uid = cm_db.create_user("regular_login", "correct password", "昵称")
        for _ in range(6):
            self.assertEqual(cm_db.verify_login("regular_login", "correct password")["id"], uid)
