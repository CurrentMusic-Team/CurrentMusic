"""通过带 TLS 的 SMTP 发送邮件。

凭据经 systemd 环境文件或受保护的 Outlook OAuth 文件注入，
仓库内不落任何密钥。
"""
from contextlib import contextmanager
import logging
import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid
from html import escape

import cm_mail_outlook

log = logging.getLogger("cm")

SMTP_HOST = "smtp.qq.com"
ENGINE_URL = "https://music.20110208.xyz/home/engine/"


def _cfg():
    user = os.environ.get("CM_SMTP_USER", "")
    pwd = os.environ.get("CM_SMTP_PASS", "")
    outlook_token_file = os.environ.get("CM_SMTP_OUTLOOK_TOKEN_FILE", "")
    return user, pwd, outlook_token_file


def smtp_ready():
    user, password, outlook_token_file = _cfg()
    return bool(user and (password or outlook_token_file))


@contextmanager
def smtp_connection():
    user, password, outlook_token_file = _cfg()
    if not user or not (password or outlook_token_file):
        raise RuntimeError("SMTP 未配置")

    if outlook_token_file:
        host, security, port = cm_mail_outlook.SMTP_HOST, "starttls", 587
        ca_file = None
    else:
        host = os.environ.get("CM_SMTP_HOST") or SMTP_HOST
        security = os.environ.get("CM_SMTP_SECURITY") or "ssl"
        if security not in ("ssl", "starttls"):
            raise smtplib.SMTPException("CM_SMTP_SECURITY 必须为 ssl 或 starttls")
        try:
            port = int(os.environ.get("CM_SMTP_PORT") or (465 if security == "ssl" else 587))
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            raise smtplib.SMTPException("CM_SMTP_PORT 必须为 1 至 65535 的整数") from None
        ca_file = os.environ.get("CM_SMTP_CA_FILE") or None
    sender = os.environ.get("CM_SMTP_FROM") or user
    context = ssl.create_default_context(cafile=ca_file)
    if security == "ssl":
        smtp = smtplib.SMTP_SSL(host, port, timeout=15, context=context)
    else:
        smtp = smtplib.SMTP(host, port, timeout=15)
    with smtp:
        if security == "starttls":
            smtp.starttls(context=context)
        if outlook_token_file:
            cm_mail_outlook.authenticate(smtp, user, outlook_token_file)
        else:
            smtp.login(user, password)
        yield smtp, sender


def send_code_email(to_email, code, ttl_min=5, purpose="注册"):
    title = f"{purpose}验证码"
    expires = f"{ttl_min} 分钟内有效"
    notice = "请勿向他人透露验证码。如果不是你本人发起的操作，可以忽略此邮件。"
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"CurrentMusic {title}：{code}"
    msg["To"] = to_email
    msg["Date"] = formatdate(usegmt=True)
    text = (
        f"你的 CurrentMusic {title}是：{code}\n"
        f"{expires}。{notice}\n\n"
        f"由 流声引擎 强力驱动\n{ENGINE_URL}"
    )
    html = f"""\
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>CurrentMusic {escape(title)}</title>
</head>
<body style="margin:0;padding:0;background-color:#f2f6f2">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" bgcolor="#f2f6f2">
    <tr><td align="center" style="padding:28px 12px">
      <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" bgcolor="#ffffff"
        style="max-width:520px;border:1px solid #d7e5d9;border-radius:18px;color:#20342a;
          font-family:'PingFang SC','Microsoft YaHei',Arial,sans-serif">
        <tr><td style="padding:28px 24px 24px">
          <p style="margin:0 0 16px;color:#28513e;font-size:16px;line-height:24px;font-weight:700">CurrentMusic</p>
          <h1 style="margin:0 0 8px;font-size:24px;line-height:32px">{escape(title)}</h1>
          <p style="margin:0 0 18px;color:#4e6956;font-size:14px;line-height:22px">请在 CurrentMusic 中输入下方验证码。</p>
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
            <tr><td align="center" bgcolor="#f3faee" style="padding:18px 8px;border:1px solid #a6c9ad;border-radius:12px">
              <p style="margin:0 0 6px;color:#28513e;font-family:Consolas,'Courier New',monospace;
                font-size:36px;font-weight:700;line-height:44px;letter-spacing:6px;white-space:nowrap">{escape(str(code))}</p>
              <p style="margin:0;color:#4e6956;font-size:13px;line-height:20px">{escape(expires)}</p>
            </td></tr>
          </table>
          <p style="margin:18px 0 0;color:#4e6956;font-size:13px;line-height:22px">{notice}</p>
        </td></tr>
        <tr><td align="center" style="padding:16px 24px 20px;border-top:1px solid #e2ebe2">
          <a href="{ENGINE_URL}" style="display:inline-block;padding:5px 11px;border:1px solid #a6c9ad;
            border-radius:999px;background-color:#f3faee;box-shadow:0 2px 0 #a6c9ad;color:#4e6956;
            font-size:12px;line-height:18px;text-decoration:none">由 <strong style="color:#28513e">流声引擎</strong> 强力驱动</a>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    with smtp_connection() as (smtp, sender):
        msg["From"] = formataddr(("CurrentMusic Engine  流声引擎", sender), charset="utf-8")
        msg["Message-ID"] = make_msgid(domain=sender.rpartition("@")[2] or None)
        smtp.send_message(msg, from_addr=sender, to_addrs=[to_email])
    log.info("验证码邮件已发送 to=%s", to_email)
