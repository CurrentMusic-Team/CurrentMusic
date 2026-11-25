"""Status email links, recipient preferences and the unsubscribe confirmation."""
from contextlib import closing
from html import escape
import os
import re
import secrets
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import cm_db
import cm_mail

UNSUBSCRIBE_PATH = "/cm/status/email/unsubscribe"
TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")


def preference(recipient):
    email = recipient.strip().lower()
    with closing(cm_db.connect()) as db, db:
        row = db.execute("SELECT token, unsubscribed_at FROM status_mail_preferences WHERE email=?",
                         (email,)).fetchone()
        if row is not None:
            return row
        db.execute("INSERT INTO status_mail_preferences(email,token) VALUES(?,?) ON CONFLICT(email) DO NOTHING",
                   (email, secrets.token_urlsafe(32)))
        return db.execute("SELECT token, unsubscribed_at FROM status_mail_preferences WHERE email=?",
                          (email,)).fetchone()


def unsubscribe_url(token):
    origin = (os.environ.get("CM_STATUS_MAIL_ORIGIN") or "https://music.20110208.xyz").rstrip("/")
    parsed = urlsplit(origin)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.path or parsed.query or parsed.fragment:
        raise ValueError("CM_STATUS_MAIL_ORIGIN 须为 HTTPS origin")
    return origin + UNSUBSCRIBE_PATH + "?" + urlencode({"token": token})


def send_notice(smtp, sender, recipient, notice, message_id=None):
    row = preference(recipient)
    if row["unsubscribed_at"] is not None:
        return "unsubscribed"
    message = cm_mail.status_message(sender, recipient, notice, unsubscribe_url(row["token"]), message_id)
    smtp.send_message(message, from_addr=sender, to_addrs=[recipient])
    return "sent"


def _page(title, message, token=None):
    form = ""
    if token:
        form = f"""<form method="post" action="{UNSUBSCRIBE_PATH}">
          <input type="hidden" name="token" value="{escape(token, quote=True)}">
          <button type="submit">确认退订</button>
        </form>"""
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="referrer" content="no-referrer">
  <title>{escape(title)} · CurrentMusic</title>
  <style>
    body {{ margin:0;padding:24px;background:#f2f6f2;color:#20342a;font:16px/1.6 system-ui,sans-serif }}
    main {{ max-width:32rem;margin:8vh auto;padding:24px;background:#fff;border:1px solid #d7e5d9;border-radius:18px }}
    h1 {{ margin:0;font-size:24px }}
    button {{ padding:9px 16px;color:#fff;background:#28513e;border:0;border-radius:8px;font:inherit;cursor:pointer }}
  </style>
</head>
<body>
  <main><h1>{escape(title)}</h1><p>{escape(message)}</p>{form}</main>
</body>
</html>"""


def confirmation(method, token):
    if token and TOKEN.fullmatch(token):
        with closing(cm_db.connect()) as db, db:
            if method == "POST":
                updated = db.execute(
                    "UPDATE status_mail_preferences SET unsubscribed_at=COALESCE(unsubscribed_at,?) WHERE token=?",
                    (int(time.time()), token)).rowcount
                if updated:
                    return 200, _page("已退订", "此邮箱已停止接收 CurrentMusic 服务状态邮件。")
            else:
                row = db.execute("SELECT unsubscribed_at FROM status_mail_preferences WHERE token=?",
                                 (token,)).fetchone()
                if row is not None:
                    if row["unsubscribed_at"] is not None:
                        return 200, _page("已退订", "此邮箱已停止接收 CurrentMusic 服务状态邮件。")
                    return 200, _page("退订服务状态邮件", "确认后，此邮箱将停止接收 CurrentMusic 服务状态邮件。", token)
    return 404, _page("退订链接无效", "请打开收到的 CurrentMusic 服务状态邮件中的退订链接。")


def form_token(handler):
    if handler.headers.get_content_type() != "application/x-www-form-urlencoded":
        return None
    length = handler.headers.get("Content-Length", "")
    if not re.fullmatch(r"[1-9][0-9]{0,2}", length) or int(length) > 256:
        return None
    try:
        fields = parse_qs(handler.rfile.read(int(length)).decode("utf-8"), max_num_fields=2)
    except ValueError:
        return None
    values = fields.get("token", [])
    return values[0] if len(values) == 1 else None
