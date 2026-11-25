"""Service status mail template, confirmation route and opt-out coverage."""
from contextlib import closing
from email import policy
from email.parser import BytesParser
import http.client
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlencode, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cm_db
import cm_server
import cm_status_mail


class StatusMailTest(unittest.TestCase):
    def test_message_and_idempotent_unsubscribe(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(cm_db, "DB_PATH", str(Path(directory) / "cm.db")))
        self.enterContext(patch.dict(os.environ, {"CM_STATUS_MAIL_ORIGIN": "https://music.example.test/"}))
        cm_db.init()
        sender = "sender@example.test"
        recipient = "listener@example.test"
        notice = {"subject": "服务状态变更", "title": "试听 <服务>", "detail": "测试内容",
                  "link": "https://music.example.test/status/"}
        smtp = Mock()
        self.assertEqual(cm_status_mail.send_notice(smtp, sender, recipient, notice), "sent")
        raw = smtp.send_message.call_args.args[0].as_bytes()
        message = BytesParser(policy=policy.default).parsebytes(raw)
        link = message["List-Unsubscribe"].strip().strip("<>")
        parsed = urlsplit(link)
        self.assertEqual(parsed.netloc, "music.example.test")
        self.assertNotIn(recipient, link)
        token = parse_qs(parsed.query)["token"][0]
        route = parsed.path.removeprefix("/cm")
        html = message.get_body(preferencelist=("html",)).get_content()
        self.assertIn(link, html)
        self.assertIn(link, message.get_body(preferencelist=("plain",)).get_content())
        self.assertIn("退订服务状态邮件", html)
        self.assertIn("&lt;服务&gt;", html)
        self.assertIn('src="cid:currentmusic-engine-mark"', html)
        image = next(part for part in message.walk() if part.get_content_type() == "image/png")
        self.assertEqual(image["Content-ID"], "<currentmusic-engine-mark>")
        self.assertTrue(image.get_content().startswith(b"\x89PNG\r\n\x1a\n"))

        with cm_server.ThreadingHTTPServer(("127.0.0.1", 0), cm_server.Handler) as server:
            runner = threading.Thread(target=server.serve_forever, daemon=True)
            runner.start()

            def request(method, path, params=None):
                with closing(http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)) as connection:
                    body = None if params is None else urlencode(params)
                    connection.request(method, path, body, {"Content-Type": "application/x-www-form-urlencoded"})
                    response = connection.getresponse()
                    return response.status, response.read().decode(), response.getheader("Cache-Control")

            try:
                status, page, cache = request("GET", f"{route}?{parsed.query}")
                self.assertEqual((status, cache), (200, "no-store"))
                self.assertIn("确认退订", page)
                self.assertIsNone(cm_status_mail.preference(recipient)["unsubscribed_at"])
                self.assertEqual(request("POST", route, {"token": "invalid"})[0], 404)
                for _ in range(2):
                    status, page, cache = request("POST", route, {"token": token})
                    self.assertEqual((status, cache), (200, "no-store"))
                    self.assertIn("已退订", page)
            finally:
                server.shutdown()
                runner.join(timeout=5)

        self.assertEqual(cm_status_mail.send_notice(smtp, sender, recipient.upper(), notice), "unsubscribed")
        self.assertEqual(smtp.send_message.call_count, 1)
