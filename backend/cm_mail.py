"""通过带 TLS 的 SMTP 发送邮件。

凭据经 systemd 环境文件或受保护的 Outlook OAuth 文件注入，
仓库内不落任何密钥。
"""
from contextlib import contextmanager
import logging
import os
import smtplib
import ssl
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid
from html import escape
from pathlib import Path

import cm_mail_outlook

log = logging.getLogger("cm")

SMTP_HOST = "smtp.qq.com"
ENGINE_URL = "https://music.20110208.xyz/home/engine/"
ENGINE_MARK = Path(__file__).with_name("assets") / "engine-badge-mark.png"
ENGINE_MARK_CID = "currentmusic-engine-mark"
ENGINE_BADGE = f"""<a href="{ENGINE_URL}" style="display:inline-block;padding:5px 11px;border:1px solid #a6c9ad;
            border-radius:999px;background-color:#f3faee;box-shadow:0 2px 0 #a6c9ad;color:#4e6956;
            font-size:12px;line-height:18px;text-decoration:none">由 <strong style="color:#28513e">流声引擎</strong> 强力驱动</a>"""
ENGINE_BADGE_DARK = """<style>
@media (prefers-color-scheme: dark) {
  .cm-engine-badge { background-color:#24382e!important;border-color:#577865!important;
    box-shadow:0 2px 0 #13251d!important;color:#def3e1!important }
  .cm-engine-badge span { color:#bcd6c1!important }
}
</style>"""
ENGINE_BADGE_FULL = f"""<a href="{ENGINE_URL}" class="cm-engine-badge" style="display:inline-block;
          padding:3px 12px 3px 6px;border:1px solid #a6c9ad;border-radius:999px;background-color:#f3faee;
          box-shadow:0 2px 0 #a6c9ad;color:#28513e;font-size:12px;line-height:28px;
          text-decoration:none;white-space:nowrap">
          <img src="cid:{ENGINE_MARK_CID}" width="28" height="28" alt=""
            style="border:0;vertical-align:middle;margin-right:5px">
          <span style="color:#4e6956;font-size:10.5px;vertical-align:middle">由</span>
          <strong style="vertical-align:middle">流声引擎</strong>
          <span style="color:#4e6956;font-size:10.5px;vertical-align:middle">强力驱动</span>
        </a>"""


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


def _message(sender, to_email, subject, text, html, message_id=None, *, include_engine_mark=False):
    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = formataddr(("CurrentMusic Engine  流声引擎", sender), charset="utf-8")
    message["To"] = to_email
    message["Date"] = formatdate(usegmt=True)
    message["Message-ID"] = message_id or make_msgid(domain=sender.rpartition("@")[2] or None)
    message.attach(MIMEText(text, "plain", "utf-8"))
    html_part = MIMEText(html, "html", "utf-8")
    if include_engine_mark:
        related = MIMEMultipart("related", type="text/html")
        related.attach(html_part)
        image = MIMEImage(ENGINE_MARK.read_bytes(), _subtype="png")
        image["Content-ID"] = f"<{ENGINE_MARK_CID}>"
        image.add_header("Content-Disposition", "inline", filename=ENGINE_MARK.name)
        related.attach(image)
        html_part = related
    message.attach(html_part)
    return message


def _html(title, intro, panel, notice, *, footer_action="", full_badge=False):
    badge = ENGINE_BADGE_FULL if full_badge else ENGINE_BADGE
    return f"""\
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>CurrentMusic {escape(title)}</title>
  {ENGINE_BADGE_DARK if full_badge else ""}
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
          <p style="margin:0 0 18px;color:#4e6956;font-size:14px;line-height:22px">{escape(intro)}</p>
          {panel}
          <p style="margin:18px 0 0;color:#4e6956;font-size:13px;line-height:22px">{escape(notice)}</p>
          {footer_action}
        </td></tr>
        <tr><td align="center" style="padding:16px 24px 20px;border-top:1px solid #e2ebe2">
          {badge}
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


def send_code_email(to_email, code, ttl_min=5, purpose="注册"):
    title = f"{purpose}验证码"
    expires = f"{ttl_min} 分钟内有效"
    notice = "请勿向他人透露验证码。如果不是你本人发起的操作，可以忽略此邮件。"
    text = f"你的 CurrentMusic {title}是：{code}\n{expires}。{notice}\n\n由 流声引擎 强力驱动\n{ENGINE_URL}"
    panel = f"""
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
            <tr><td align="center" bgcolor="#f3faee" style="padding:18px 8px;border:1px solid #a6c9ad;border-radius:12px">
              <p style="margin:0 0 6px;color:#28513e;font-family:Consolas,'Courier New',monospace;
                font-size:36px;font-weight:700;line-height:44px;letter-spacing:6px;white-space:nowrap">{escape(str(code))}</p>
              <p style="margin:0;color:#4e6956;font-size:13px;line-height:20px">{escape(expires)}</p>
            </td></tr>
          </table>"""
    html = _html(title, "请在 CurrentMusic 中输入下方验证码。", panel, notice)
    with smtp_connection() as (smtp, sender):
        message = _message(sender, to_email, f"CurrentMusic {title}：{code}", text, html)
        smtp.send_message(message, from_addr=sender, to_addrs=[to_email])
    log.info("验证码邮件已发送 to=%s", to_email)


def status_message(sender, recipient, notice, unsubscribe_url, message_id=None):
    subject, title, detail, link = (notice[key] for key in ("subject", "title", "detail", "link"))
    intro = "CurrentMusic 服务状态有新动态。"
    footer = "你收到此邮件，是因为你在 CurrentMusic 注册并绑定了此邮箱。"
    content = escape(detail).replace("\n", "<br>")
    panel = f"""
      <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
        <tr><td bgcolor="#f3faee" style="padding:16px;border:1px solid #a6c9ad;border-radius:12px">
          <p style="margin:0 0 8px;font-size:18px;color:#28513e;font-weight:700">{escape(title)}</p>
          <p style="margin:0;font-size:14px;line-height:1.65;color:#334e3d;overflow-wrap:anywhere">{content}</p>
        </td></tr>
      </table>
      <p style="margin:16px 0 0">
        <a href="{escape(link, quote=True)}" style="display:inline-block;background-color:#28513e;color:#ffffff;
          text-decoration:none;border-radius:8px;padding:9px 16px;font-size:14px;font-weight:700">查看服务状态</a>
      </p>"""
    unsubscribe_button = f"""
      <p style="margin:12px 0 0">
        <a href="{escape(unsubscribe_url, quote=True)}" style="display:inline-block;border:1px solid #a6c9ad;
          border-radius:8px;padding:7px 13px;color:#28513e;text-decoration:none;
          font-size:13px;line-height:20px;font-weight:700">退订服务状态邮件</a>
      </p>"""
    text = (f"{intro}\n\n{title}\n{detail}\n\n查看服务状态：{link}\n\n{footer}\n"
            f"退订服务状态邮件：{unsubscribe_url}\n\n{ENGINE_URL}")
    html = _html(subject, intro, panel, footer, footer_action=unsubscribe_button, full_badge=True)
    message = _message(sender, recipient, "CurrentMusic · " + subject, text, html, message_id,
                       include_engine_mark=True)
    message["List-Unsubscribe"] = f"<{unsubscribe_url}>"
    return message
