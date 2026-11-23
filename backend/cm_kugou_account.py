"""Server-held personal KuGou sessions and account-scoped upstream operations."""

from collections import defaultdict, deque
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
import time

import cm_kugou as kg


_cookie_names = {"token", "userid", "dfid", "uuid", "t1", "t2", "vip_type", "vip_token",
                 "KUGOU_API_PLATFORM", "KUGOU_API_MID", "KUGOU_API_GUID", "KUGOU_API_DEV",
                 "KUGOU_API_MAC", "KUGOU_API_WEBGL"}
_safe_value = re.compile(r"[\x21-\x3a\x3c-\x7e]+")
_mobile = re.compile(r"1[0-9]{10}")
_code = re.compile(r"[0-9]{4,8}")
_num_id = re.compile(r"[1-9][0-9]{0,24}")
_lock = threading.Lock()
_pending = {}
_sms_usage = defaultdict(deque)
_ttl = 300


@dataclass
class Challenge:
    mobile: str
    jar: dict
    expires: float
    tries: int = 0
    busy: bool = True


def _directory():
    value = os.environ.get("CM_KUGOU_ACCOUNT_DIR", "")
    if not kg.base() or not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        return None
    try:
        mode = path.lstat()
        if stat.S_ISDIR(mode.st_mode) and mode.st_uid == os.geteuid() and not mode.st_mode & 0o077:
            return path
    except OSError:
        pass
    return None


def enabled():
    return _directory() is not None


def _path(uid):
    directory = _directory()
    if directory is None:
        raise kg.Error(503, "kugou_account_unavailable", "此服务暂未开启酷狗个人账号")
    if type(uid) is not int or uid < 1:
        raise kg.Error(401, "authorization_required", "请先登录 CurrentMusic")
    return directory / f"{uid}.cookie"


def _header(jar):
    return "; ".join(f"{key}={value}" for key, value in sorted(jar.items())
                     if key in _cookie_names and isinstance(value, str) and _safe_value.fullmatch(value))


def _load(uid):
    try:
        fd = os.open(_path(uid), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, encoding="ascii") as source:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or st.st_mode & 0o077:
                raise ValueError("unprotected personal session")
            value = source.read(8193).strip()
        if len(value) <= 8192:
            jar = dict(segment.strip().split("=", 1) for segment in value.split(";"))
            if jar.get("token") and _num_id.fullmatch(jar.get("userid", "")) and jar.get("dfid"):
                if _header(jar) == value:
                    return value
    except (OSError, ValueError):
        pass
    raise kg.Error(403, "binding_required", "请先绑定或重新连接个人酷狗账号")


def status(uid):
    if not enabled():
        return {"enabled": False, "bound": False}
    try:
        _load(uid)
    except kg.Error:
        return {"enabled": True, "bound": False}
    return {"enabled": True, "bound": True}


def _save(uid, jar):
    path = _path(uid)
    value = _header(jar)
    if not (jar.get("token") and _num_id.fullmatch(jar.get("userid", "")) and jar.get("dfid") and len(value) <= 8192):
        raise kg.Error(502, "kugou_session_unavailable", "酷狗没有返回可用的登录会话")
    try:
        fd, staging = tempfile.mkstemp(prefix=".cookie-", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="ascii") as file:
                file.write(value + "\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(staging, path)
        finally:
            Path(staging).unlink(missing_ok=True)
    except OSError:
        raise kg.Error(503, "kugou_account_unavailable", "暂时无法保存酷狗会话") from None


def remove(uid):
    path = _path(uid)
    with _lock:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            raise kg.Error(503, "kugou_account_unavailable", "暂时无法解除酷狗绑定") from None
        _pending.pop(uid, None)
    return status(uid)


def send(uid, mobile):
    _path(uid)
    if not isinstance(mobile, str) or not _mobile.fullmatch(mobile):
        raise kg.Error(400, "invalid_input", "请填写有效的中国大陆手机号")
    now = time.monotonic()
    entry = Challenge(mobile, {}, now + _ttl)
    with _lock:
        for user in [user for user, value in _pending.items() if value.expires <= now]:
            del _pending[user]
        for key, events in list(_sms_usage.items()):
            while events and events[0] <= now - 86400:
                events.popleft()
            if not events:
                del _sms_usage[key]
        previous = _pending.get(uid)
        if previous and previous.busy:
            raise kg.Error(429, "rate_limited", "请等待当前酷狗请求完成")
        user_events = _sms_usage[("uid", uid)]
        phone_events = _sms_usage[("phone", mobile)]
        if any(events and (len(events) >= 5 or now - events[-1] < 60) for events in (user_events, phone_events)):
            raise kg.Error(429, "rate_limited", "发送过于频繁，请稍后再试")
        user_events.append(now)
        phone_events.append(now)
        _pending[uid] = entry
    try:
        result = kg._api("/captcha/sent", jar=entry.jar, payload={"mobile": mobile}, cookie_names=_cookie_names)
        if result.get("status") != 1:
            raise kg.Error(502, "kugou_upstream_error", "酷狗未确认发送验证码")
    except kg.Error:
        with _lock:
            if _pending.get(uid) is entry:
                _pending.pop(uid)
        raise
    with _lock:
        entry.busy = False
    return {"sent": True, "ttl": _ttl, "retryAfter": 60}


def verify(uid, code):
    _path(uid)
    if not isinstance(code, str) or not _code.fullmatch(code):
        raise kg.Error(400, "invalid_input", "请填写短信验证码")
    with _lock:
        entry = _pending.get(uid)
        if not entry or entry.expires <= time.monotonic() or entry.tries >= 5:
            raise kg.Error(400, "kugou_code_expired", "请重新获取酷狗验证码")
        if entry.busy:
            raise kg.Error(429, "rate_limited", "请等待当前酷狗请求完成")
        entry.busy = True
        entry.tries += 1
    jar = entry.jar.copy()
    try:
        try:
            result = kg._api("/login/cellphone", cookie=_header(jar), jar=jar, cookie_names=_cookie_names,
                             payload={"mobile": entry.mobile, "code": code})
        except kg.Error:
            raise kg.Error(400, "kugou_code_rejected", "酷狗未接受验证码，请核对后重试") from None
        if result.get("status") != 1 or not jar.get("token") or not _num_id.fullmatch(jar.get("userid", "")):
            raise kg.Error(400, "kugou_code_rejected", "酷狗未接受验证码，请核对后重试")
        for _ in range(3):
            if jar.get("dfid"):
                break
            kg._api("/register/dev", cookie=_header(jar), jar=jar, cookie_names=_cookie_names)
        check = kg._api("/user/verify", cookie=_header(jar), playback=True)
        if not (check.get("data") or {}).get("auth"):
            raise kg.Error(403, "binding_required", "酷狗未通过账号验证，请重新连接")
        with _lock:
            if _pending.get(uid) is not entry or entry.expires <= time.monotonic():
                raise kg.Error(400, "kugou_code_expired", "请重新获取酷狗验证码")
            _save(uid, jar)
            _pending.pop(uid)
    finally:
        with _lock:
            entry.busy = False
    return {"enabled": True, "bound": True}


def _account_request(cookie, path, params=None, payload=None):
    try:
        return kg._api(path, params, cookie=cookie, payload=payload, playback=True)
    except kg.Error as error:
        if error.code == "kugou_session_unavailable":
            raise kg.Error(403, "binding_required", "酷狗个人会话已失效，请重新绑定") from None
        raise


def _cloud_rows(data):
    rows = data.get("list")
    if isinstance(rows, str):
        try:
            rows = json.loads(rows) if rows.strip() else []
        except ValueError:
            raise kg.Error(502, "kugou_upstream_error", "酷狗云盘返回无法读取的列表") from None
    if not isinstance(rows, list):
        raise kg.Error(502, "kugou_upstream_error", "酷狗云盘返回无效列表")
    return rows


def _cloud_item(row):
    fid = str(row.get("kv_id") or row.get("fileid") or row.get("file_id") or "")
    if not _num_id.fullmatch(fid):
        return None
    name = row.get("songname") or row.get("track_name") or row.get("filename") or row.get("file_name") or row.get("name")
    artist = row.get("author_name") or row.get("singername") or row.get("artist")
    duration = row.get("timelength") or row.get("duration")
    ident = f"{str(row.get('hash_std') or row.get('hash') or '').lower()}-{row.get('album_audio_id')}"
    item = kg._track("kgc_" + fid, name, artist, row.get("album_name"), row.get("cover"), duration)
    if kg._record.fullmatch(ident):
        item["catalogId"] = ident
    size = row.get("filesize") or row.get("file_size") or 0
    item["size"] = size if type(size) is int and size > 0 else 0
    return item


def cloud(uid, offset, limit):
    cookie = _load(uid)
    size = 50
    index, skip = divmod(offset, size)
    items = []
    consumed = 0
    total = used = maximum = None
    more = False
    while consumed < limit:
        reply = _account_request(cookie, "/user/cloud", {"page": index + 1, "pagesize": size})
        data = reply.get("data")
        if reply.get("status") != 1 or not isinstance(data, dict):
            raise kg.Error(502, "kugou_upstream_error", "酷狗云盘暂不可用")
        rows = _cloud_rows(data)
        total, used, maximum = data.get("list_count"), data.get("used_size"), data.get("max_size")
        stop = min(len(rows), skip + limit - consumed)
        batch = rows[skip:stop]
        items.extend(item for row in batch if isinstance(row, dict) and (item := _cloud_item(row)))
        consumed += len(batch)
        more = stop < len(rows) or (stop == size and (type(total) is not int or index * size + stop < total))
        if not more:
            break
        index += 1
        skip = 0
    return {"items": items, "nextOffset": offset + consumed if consumed and more else None,
            "total": total if type(total) is int and total >= 0 else None,
            "usedBytes": used if type(used) is int and used >= 0 else None,
            "capacityBytes": maximum if type(maximum) is int and maximum >= 0 else None}


def comment(uid, ident, content):
    # Require the account session before resolving or forwarding a write.
    cookie = _load(uid)
    song, _, _ = kg.track(ident)
    reply = _account_request(cookie, "/comment/music/send",
                             payload={"mixsongid": ident.split("-")[1], "name": song["name"], "content": content})
    if reply.get("status") != 1 or any(reply.get(name) not in (None, 0, "0") for name in ("errcode", "err_code", "error_code")):
        raise kg.Error(403, "comment_unavailable", "酷狗未接受这条评论")
    data = reply.get("data") or {}
    cid = str(data.get("id") or data.get("comment_id") or reply.get("id") or "") if isinstance(data, dict) else ""
    result = {"ok": True}
    if _num_id.fullmatch(cid):
        result["commentId"] = cid
    return result
