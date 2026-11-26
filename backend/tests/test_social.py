"""HTTP regression tests for social authorization and legacy world chat."""
import base64
from contextlib import closing
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cm_chat
import cm_db
import cm_server
import cm_social


class SocialTest(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(cm_db, "DB_PATH", str(Path(folder) / "social.db")))
        self.enterContext(patch.object(cm_chat, "CHAT_DIR", str(Path(folder) / "images")))
        self.enterContext(patch.object(cm_chat, "_last_act", {}))
        self.enterContext(patch.object(cm_chat, "throttled_send", return_value=False))
        self.enterContext(patch.object(cm_server.cm_region, "note"))
        self.enterContext(patch.object(cm_server, "rate_limited", return_value=False))
        self.enterContext(patch.object(cm_server.cm_ai, "should_reply", return_value=False))
        cm_db.init()
        with closing(cm_db.connect()) as db:
            cm_chat.init_chat_tables(db)
        self.owner = self.account("Rcst20", "群主")
        self.admin = self.account("bileizhen", "群管理员")
        self.alice = self.account("alice", "爱丽丝")
        self.bob = self.account("bob", "小鲍")
        self.staff = self.account("staff", "站点管理员")
        cm_db.set_admin(self.staff["id"], True)
        self.public_message = cm_chat.send(cm_db.get_user(self.alice["id"]), "旧聊天室历史", "", [])
        with closing(cm_db.connect()) as db:
            cm_social.init_tables(db)
        cm_social.seed_world_roles("Rcst20", "bileizhen")
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), cm_server.Handler)
        self.addCleanup(self.http.server_close)
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.addCleanup(self.http.shutdown)

    def account(self, name, nickname):
        uid = cm_db.create_user(name, "fixture only", nickname)
        return {"id": uid, "token": cm_db.issue_token(uid)}

    def expect(self, status, method, path, actor=None, body=None):
        with closing(HTTPConnection("127.0.0.1", self.http.server_port, timeout=5)) as conn:
            headers = {"Content-Type": "application/json"}
            if actor:
                headers["Authorization"] = "Bearer " + actor["token"]
            conn.request(method, path, json.dumps(body) if body is not None else None, headers)
            response = conn.getresponse()
            raw = response.read()
            content_type = response.getheader("Content-Type", "")
            data = json.loads(raw) if content_type.startswith("application/json") else raw
        self.assertEqual(response.status, status, (method, path, data))
        if path.startswith(("/social/", "/admin/social/")):
            self.assertIn("no-store", response.getheader("Cache-Control", ""))
        return data

    def request_friend(self, actor, other, action, status=200):
        return self.expect(status, "PUT", f"/social/contacts/{other['id']}", actor, {"action": action})

    def friends(self, a, b):
        self.request_friend(a, b, "request")
        self.request_friend(b, a, "accept")

    def new_room(self, actor, **fields):
        return self.expect(200, "POST", "/social/conversations", actor, fields)["conversation"]

    def test_world_upgrade_and_legacy_moderation(self):
        world = self.expect(200, "GET", "/social/conversations")["conversations"][0]
        self.assertEqual((world["name"], world["description"]), ("世界聊天", "流声音乐官方公共聊天室"))
        self.assertFalse(world["canSend"])
        self.assertEqual(world["last"]["id"], self.public_message["id"])
        self.assertEqual(self.expect(200, "GET", "/chat/messages")["messages"][0]["content"], "旧聊天室历史")
        cm_social.seed_world_roles("Rcst20", "bileizhen")
        for account, role in ((self.owner, "owner"), (self.admin, "admin"), (self.account("later", "新注册用户"), "member")):
            info = self.expect(200, "GET", "/social/conversations/1", account)["conversation"]
            self.assertEqual(info["role"], role)
            self.assertTrue(info["canSend"])
        self.expect(403, "POST", "/chat/messages", self.alice, {"content": "@全体成员", "mentionAll": True})
        notice = self.expect(200, "POST", "/chat/messages", self.admin, {"content": "@全体成员", "mentionAll": True})
        self.assertTrue(notice["mentionAll"])
        self.expect(200, "DELETE", f"/chat/messages/{self.public_message['id']}", self.admin)

        alice = self.alice["id"]
        member = f"/social/conversations/1/members/{alice}"
        self.expect(403, "PUT", f"/social/conversations/1/members/{self.owner['id']}", self.admin, {"action": "remove"})
        self.expect(200, "PUT", member, self.admin, {"action": "mute", "seconds": 60})
        self.expect(403, "POST", "/chat/messages", self.alice, {"content": "禁言"})
        self.expect(403, "POST", "/chat/image", self.alice, {"file": ""})
        self.expect(200, "PUT", member, self.admin, {"action": "unmute"})
        self.expect(200, "POST", "/chat/messages", self.alice, {"content": "解除禁言"})
        self.expect(200, "PUT", member, self.admin, {"action": "remove"})
        self.expect(403, "POST", "/chat/messages", self.alice, {"content": "已移出"})
        self.assertTrue(self.expect(200, "GET", "/chat/messages", self.alice)["messages"])
        self.assertFalse(any(user["id"] == alice for user in self.expect(200, "GET", "/chat/members")["members"]))
        self.expect(200, "POST", "/social/conversations/1/invites", self.admin, {"userId": alice})
        self.expect(200, "PUT", "/social/conversations/1/invites", self.alice, {"status": "accepted"})
        self.expect(200, "POST", "/chat/messages", self.alice, {"content": "重新加入"})
        self.expect(200, "PUT", f"/social/conversations/1/members/{self.admin['id']}", self.owner, {"action": "demote"})
        with self.assertRaises(cm_social.Error):
            cm_social.seed_world_roles("Rcst20", "bileizhen")

    def test_friendship_private_history_and_reports(self):
        self.expect(403, "POST", "/social/conversations", self.alice, {"kind": "direct", "userId": self.bob["id"]})
        self.request_friend(self.alice, self.bob, "request")
        self.request_friend(self.alice, self.bob, "accept", 409)
        self.assertEqual(self.expect(200, "GET", "/social/conversations", self.bob)["pending"], 1)
        self.request_friend(self.bob, self.alice, "accept")
        room = self.new_room(self.alice, kind="direct", userId=self.bob["id"])
        self.assertEqual(room["id"], self.new_room(self.bob, kind="direct", userId=self.alice["id"])["id"])
        endpoint = f"/social/conversations/{room['id']}"
        sent = self.expect(200, "POST", endpoint + "/messages", self.alice,
                           {"content": "私聊", "mentions": [self.bob["id"], self.owner["id"]], "clientId": "test-1234"})
        repeated = self.expect(200, "POST", endpoint + "/messages", self.alice,
                               {"content": "私聊", "clientId": "test-1234"})
        self.assertEqual(sent["id"], repeated["id"])
        self.assertEqual([p["id"] for p in sent["mentions"]], [self.bob["id"]])
        info = self.expect(200, "GET", endpoint, self.bob)["conversation"]
        self.assertTrue(info["mentioned"])
        self.assertEqual(info["unread"], 1)
        self.expect(200, "PUT", endpoint + "/read", self.bob, {"messageId": sent["id"]})
        self.expect(200, "PUT", endpoint + "/read", self.bob, {"messageId": 0})
        self.assertEqual(self.expect(200, "GET", endpoint, self.bob)["conversation"]["unread"], 0)
        self.request_friend(self.bob, self.alice, "remove")
        self.expect(403, "POST", endpoint + "/messages", self.alice, {"content": "无法发送"})
        self.assertEqual(self.expect(200, "GET", endpoint + "/messages", self.alice)["messages"][0]["content"], "私聊")
        self.expect(404, "GET", endpoint + "/messages", self.staff)
        self.expect(401, "GET", endpoint + "/messages?token=" + self.alice["token"])
        reports = f"/social/contacts/{self.alice['id']}/report"
        report = self.expect(200, "POST", reports, self.bob, {"reason": "测试举报"})
        self.assertEqual(report, self.expect(200, "POST", reports, self.bob, {"reason": "重复提交"}))
        self.expect(403, "GET", "/admin/social/reports", self.admin)
        self.assertEqual(self.expect(200, "GET", "/admin/social/reports", self.staff)["reports"][0]["reason"], "测试举报")
        self.expect(200, "PUT", f"/admin/social/reports/{report['id']}", self.staff, {"status": "resolved"})
        self.assertEqual(self.expect(200, "GET", "/admin/social/reports", self.staff)["reports"], [])
        self.friends(self.alice, self.bob)
        self.expect(200, "POST", endpoint + "/messages", self.alice, {"content": "恢复私聊"})

    def test_private_groups_isolate_media_quotes_and_events(self):
        self.friends(self.alice, self.bob)
        group = self.new_room(self.alice, kind="group", name="群组")
        cid = group["id"]
        endpoint = f"/social/conversations/{cid}"
        self.expect(200, "POST", endpoint + "/invites", self.alice, {"userId": self.bob["id"]})
        self.expect(404, "PUT", endpoint + "/invites", self.owner, {"status": "accepted"})
        self.expect(404, "GET", endpoint + "/messages", self.bob)
        self.expect(200, "PUT", endpoint + "/invites", self.bob, {"status": "accepted"})
        self.expect(403, "POST", endpoint + "/messages", self.bob, {"content": "@全体成员", "mentionAll": True})
        info = self.expect(200, "PUT", endpoint, self.alice, {"description": "简介", "announcement": "公告"})
        self.assertEqual(info["conversation"]["announcement"], "公告")
        blob = b"\x89PNG\r\n\x1a\nfixture"
        image = self.expect(200, "POST", endpoint + "/images", self.alice,
                            {"file": base64.b64encode(blob).decode()})["image"]
        photo = endpoint + "/images/" + image
        self.assertEqual(self.expect(200, "GET", photo, self.alice), blob)
        self.expect(404, "GET", photo, self.bob)
        message = self.expect(200, "POST", endpoint + "/messages", self.alice, {"image": image})
        self.assertEqual(self.expect(200, "GET", photo, self.bob), blob)
        self.expect(404, "GET", "/chat/image/" + image)
        direct = self.new_room(self.alice, kind="direct", userId=self.bob["id"])
        direct_path = f"/social/conversations/{direct['id']}"
        self.expect(404, "GET", direct_path + "/images/" + image, self.alice)
        self.expect(400, "POST", direct_path + "/messages", self.alice, {"content": "跨对话", "quoteId": message["id"]})
        self.expect(400, "POST", direct_path + "/messages", self.alice, {"image": image})
        self.assertEqual(self.expect(200, "GET", direct_path + "/events", self.bob)["events"], [])
        self.assertEqual(self.expect(200, "GET", endpoint + "/events", self.bob)["events"][0]["payload"]["id"], message["id"])
        bob = endpoint + f"/members/{self.bob['id']}"
        self.expect(200, "PUT", bob, self.alice, {"action": "mute", "seconds": 60})
        self.expect(403, "POST", endpoint + "/messages", self.bob, {"content": "禁言"})
        self.expect(200, "PUT", bob, self.alice, {"action": "remove"})
        for path in (endpoint, endpoint + "/messages", endpoint + "/events", photo, endpoint + "/members"):
            self.expect(404, "GET", path, self.bob)
        self.expect(200, "DELETE", endpoint + f"/messages/{message['id']}", self.alice)
        self.expect(404, "GET", photo, self.alice)

    def test_public_discovery_hides_private_accounts(self):
        with cm_social.transaction(write=True) as db:
            db.execute("UPDATE users SET public_square=0 WHERE id=?", (self.bob["id"],))
        users = self.expect(200, "GET", "/social/users?q=%E7%88%B1", self.bob)["users"]
        self.assertEqual([u["id"] for u in users], [self.alice["id"]])
        self.assertNotIn("username", users[0])
        self.assertEqual(self.expect(200, "GET", "/social/users?q=%E9%B2%8D", self.alice)["users"], [])
        self.request_friend(self.alice, self.bob, "request", 404)


if __name__ == "__main__":
    unittest.main()
