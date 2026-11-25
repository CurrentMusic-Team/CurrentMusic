"""Exercise SMTP configuration without sending email."""
from email.utils import parseaddr
import os
import ssl
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cm_mail


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
