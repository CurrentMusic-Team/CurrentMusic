"""Exercise SMTP configuration without sending email."""
from email.utils import parseaddr
import json
import os
from pathlib import Path
import ssl
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cm_mail
import cm_mail_outlook


class TestMail(unittest.TestCase):
    def setUp(self):
        self.credentials = {"CM_SMTP_USER": "account@example.test", "CM_SMTP_PASS": "example"}
        self.env = self.enterContext(mock.patch.dict(os.environ, self.credentials, clear=True))

    def test_qq_defaults_use_verified_tls(self):
        with mock.patch.object(cm_mail.smtplib, "SMTP_SSL") as connect:
            with cm_mail.smtp_connection() as (smtp, sender):
                self.assertEqual(sender, self.credentials["CM_SMTP_USER"])
                smtp.login.assert_called_once_with("account@example.test", "example")

        self.assertEqual(connect.call_args.args, ("smtp.qq.com", 465))
        tls = connect.call_args.kwargs["context"]
        self.assertTrue(tls.check_hostname)
        self.assertEqual(tls.verify_mode, ssl.CERT_REQUIRED)

    def test_starttls_relay_uses_verified_sender_and_separate_login(self):
        self.env.update({
            "CM_SMTP_HOST": "smtp.example.test",
            "CM_SMTP_SECURITY": "starttls",
            "CM_SMTP_PORT": "2587",
            "CM_SMTP_FROM": "verification@example.test",
        })
        with mock.patch.object(cm_mail.smtplib, "SMTP") as connect:
            cm_mail.send_code_email("recipient@example.test", "012345")

        connect.assert_called_once_with("smtp.example.test", 2587, timeout=15)
        smtp = connect.return_value
        self.assertEqual([call[0] for call in smtp.method_calls], ["starttls", "login", "send_message"])
        smtp.login.assert_called_once_with("account@example.test", "example")
        tls = smtp.starttls.call_args.kwargs["context"]
        self.assertTrue(tls.check_hostname)
        self.assertEqual(tls.verify_mode, ssl.CERT_REQUIRED)
        sent = smtp.send_message.call_args
        self.assertEqual(parseaddr(sent.args[0]["From"])[1], "verification@example.test")
        self.assertEqual(sent.kwargs, {
            "from_addr": "verification@example.test",
            "to_addrs": ["recipient@example.test"],
        })

    def test_failed_starttls_does_not_send_credentials(self):
        self.env["CM_SMTP_SECURITY"] = "starttls"
        with mock.patch.object(cm_mail.smtplib, "SMTP") as connect:
            smtp = connect.return_value
            smtp.starttls.side_effect = cm_mail.smtplib.SMTPNotSupportedError
            with self.assertRaises(cm_mail.smtplib.SMTPNotSupportedError):
                cm_mail.send_code_email("recipient@example.test", "012345")

        connect.assert_called_once_with("smtp.qq.com", 587, timeout=15)
        smtp.login.assert_not_called()
        smtp.send_message.assert_not_called()

    def test_outlook_uses_xoauth2_after_starttls_and_rotates_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "outlook.json"
            self.env["CM_SMTP_OUTLOOK_TOKEN_FILE"] = str(path)
            self.env.pop("CM_SMTP_PASS")
            self.env["CM_SMTP_HOST"] = "unrelated.example.test"
            cm_mail_outlook.save_credentials(path, "client-id", "old-refresh")
            with mock.patch.object(cm_mail.smtplib, "SMTP") as connect, \
                    mock.patch.object(cm_mail_outlook, "request_oauth", return_value={
                        "access_token": "test-access-token",
                        "refresh_token": "new-refresh",
                        "expires_in": 3600,
                    }) as exchange:
                self.assertTrue(cm_mail.smtp_ready())
                cm_mail.send_code_email("recipient@example.test", "012345")
                smtp = connect.return_value
                self.assertEqual([call[0] for call in smtp.method_calls],
                                 ["starttls", "ehlo_or_helo_if_needed", "auth", "send_message"])
                connect.assert_called_once_with("smtp-mail.outlook.com", 587, timeout=15)
                self.assertEqual(smtp.auth.call_args.args[0], "XOAUTH2")
                encode = smtp.auth.call_args.args[1]
                self.assertEqual(encode(), "user=account@example.test\x01auth=Bearer test-access-token\x01\x01")
                self.assertEqual(encode(b"failed"), "")
                cm_mail.send_code_email("recipient@example.test", "543210")
                exchange.assert_called_once_with("token", {
                    "client_id": "client-id",
                    "grant_type": "refresh_token",
                    "refresh_token": "old-refresh",
                    "scope": cm_mail_outlook.SCOPE,
                })
                smtp.login.assert_not_called()

            self.assertEqual(json.loads(path.read_text())["refresh_token"], "new-refresh")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
