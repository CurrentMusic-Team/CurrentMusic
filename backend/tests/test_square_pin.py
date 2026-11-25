#!/usr/bin/env python3
"""发现页用户置顶 + 管理台用户排序回归测试。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cm_db  # noqa: E402


class TestSquarePinAndAdminSort(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = cm_db.DB_PATH
        cm_db.DB_PATH = os.path.join(self.tmp.name, "test.db")
        cm_db.init()

    def tearDown(self):
        cm_db.DB_PATH = self.old
        self.tmp.cleanup()

    def test_configured_user_always_first_for_every_sort(self):
        pinned = cm_db.create_user("pinned_fixture", "password123", "置顶显示名")
        other = cm_db.create_user("owner_fixture", "password123", "另一位站长")
        cm_db.set_owner(other, True)
        self.enterContext(patch.dict(os.environ, {"CM_SQUARE_PINNED_USER_ID": str(pinned)}))

        for sort in ("composite", "active", "level", "reg", "reg_asc", "name", "days", "listen", "likes"):
            d = cm_db.square_users(sort=sort, limit=10, offset=0)
            self.assertEqual(d["users"][0]["id"], pinned, sort)
            self.assertEqual(d["users"][0]["nickname"], "置顶显示名", sort)

        # 搜索不匹配时不强行插入置顶用户；过滤条件优先于置顶
        d = cm_db.square_users(query="另一位站长", limit=10, offset=0)
        self.assertNotIn(pinned, [u["id"] for u in d["users"]])
        # 不在听歌时，仅显示在听歌的过滤也不会强行塞入
        d = cm_db.square_users(listening_only=True, limit=10, offset=0)
        self.assertNotIn(pinned, [u["id"] for u in d["users"]])

    def test_admin_user_sort_whitelist(self):
        a = cm_db.create_user("aaa_admin_test", "password123", "A 用户")
        b = cm_db.create_user("bbb_admin_test", "password123", "B 用户")
        cm_db.toggle_like(b, 1)
        cm_db.toggle_like(b, 2)

        d = cm_db.admin_list_users("", 20, 0, "likes")
        self.assertEqual(d["users"][0]["id"], b)
        d = cm_db.admin_list_users("", 20, 0, "reg_asc")
        self.assertEqual(d["users"][0]["id"], a)
        d = cm_db.admin_list_users("", 20, 0, "name")
        self.assertEqual(d["users"][0]["id"], a)
        d = cm_db.admin_list_users("", 20, 0, "not-a-sort")
        self.assertEqual(d["users"][0]["id"], b)   # 非法排序回落到注册最新

    def test_public_profile_and_search_use_display_name(self):
        uid = cm_db.create_user("private_account", "password123", "ＰＲＩＶＡＴＥ＿ＡＣＣＯＵＮＴ")
        other = cm_db.create_user("separate_login", "password123", "2025550100", phone="2025550100")
        self.assertEqual(cm_db.square_users("private_account")["total"], 0)
        self.assertEqual(cm_db.square_users("2025550100")["total"], 0)
        for account in (uid, other):
            nickname = f"用户#{account}"
            self.assertEqual(cm_db.square_users(nickname)["users"][0]["nickname"], nickname)
            user = cm_db.public_profile(account)["user"]
            self.assertEqual(user["nickname"], nickname)
            self.assertNotIn("username", user)


if __name__ == "__main__":
    unittest.main(verbosity=2)
