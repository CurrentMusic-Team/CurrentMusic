"""OAuth2 for personal Outlook.com SMTP accounts."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import smtplib
import tempfile
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

SMTP_HOST = "smtp-mail.outlook.com"
OAUTH_BASE = "https://login.microsoftonline.com/consumers/oauth2/v2.0"
SCOPE = "https://outlook.office.com/SMTP.Send offline_access"
_cached_token = None


class OutlookOAuthError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def request_oauth(endpoint, fields):
    request = Request(f"{OAUTH_BASE}/{endpoint}", data=urlencode(fields).encode("ascii"))
    try:
        with urlopen(request, timeout=10) as response:
            return json.load(response)
    except HTTPError as exc:
        with exc:
            try:
                code = json.load(exc).get("error", "")
            except (ValueError, AttributeError):
                code = ""
        raise OutlookOAuthError(code) from None


@contextmanager
def _token_lock(path):
    lock_fd = os.open(path.with_suffix(path.suffix + ".lock"), os.O_WRONLY | os.O_CREAT, 0o600)
    with os.fdopen(lock_fd, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _write_credentials(path, client_id, refresh_token):
    fd, filename = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    tmp = Path(filename)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump({"client_id": client_id, "refresh_token": refresh_token}, out)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def save_credentials(path, client_id, refresh_token):
    with _token_lock(path):
        _write_credentials(path, client_id, refresh_token)


def _access_token(path):
    global _cached_token
    with _token_lock(path):
        credentials = json.loads(path.read_text(encoding="utf-8"))
        client_id = credentials["client_id"]
        refresh_token = credentials["refresh_token"]
        key = (path, client_id, refresh_token)
        now = time.monotonic()
        if _cached_token and _cached_token[0] == key and _cached_token[2] > now:
            return _cached_token[1]

        response = request_oauth("token", {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": refresh_token,
            "scope": SCOPE,
        })
        token = response["access_token"]
        if not isinstance(token, str) or not token or not token.isascii():
            raise OutlookOAuthError("invalid_access_token")
        expires = now + max(0, int(response["expires_in"]) - 60)
        rotated_token = response.get("refresh_token") or refresh_token
        if rotated_token != refresh_token:
            _write_credentials(path, client_id, rotated_token)
        _cached_token = ((path, client_id, rotated_token), token, expires)
        return token


def authenticate(smtp, user, filename):
    try:
        token = _access_token(Path(filename))
    except OutlookOAuthError as exc:
        if exc.code in {"invalid_grant", "interaction_required"}:
            raise smtplib.SMTPException("Outlook SMTP 授权已失效，需要重新授权") from None
        raise smtplib.SMTPException("Outlook OAuth 令牌请求失败") from None
    except (OSError, ValueError, KeyError, TypeError):
        raise smtplib.SMTPException("无法读取或更新 Outlook SMTP 授权") from None

    def auth_response(challenge=None):
        if challenge is None:
            return f"user={user}\x01auth=Bearer {token}\x01\x01"
        return ""

    smtp.ehlo_or_helo_if_needed()
    smtp.auth("XOAUTH2", auth_response)
