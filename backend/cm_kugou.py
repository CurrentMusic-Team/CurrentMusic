"""KuGou concept catalogue and scoped playback through the local upstream."""

from dataclasses import dataclass
from http.cookies import CookieError, SimpleCookie
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class Error(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code = status, code


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_opener = build_opener(ProxyHandler({}), _NoRedirect())
_record = re.compile(r"([a-f0-9]{32})-([1-9][0-9]{0,14})")
_hash = re.compile(r"[a-f0-9]{32}")
_range = re.compile(r"bytes=(?:[0-9]{1,12}-[0-9]{0,12}|-[0-9]{1,12})")
_content_range = re.compile(r"bytes (?:[0-9]{1,12}-[0-9]{1,12}/[0-9]{1,12}|\*/[0-9]{1,12})")
_levels = {"lossless": "flac", "exhigh": "320", "standard": "128"}
_cdn_default = "fs.youthandroid2.kugou.com,fs.youthandroid.kugou.com"
_formats = {"vorbis": ("ogg", "audio/ogg"), "opus": ("ogg", "audio/ogg"), "flac": ("flac", "audio/flac"),
            "mp3": ("mp3", "audio/mpeg"), "aac": ("m4a", "audio/mp4")}
_ticket_ttl = 480
_max_size = 128 * 1024 * 1024
_max_video_size = 512 * 1024 * 1024
_video_hosts = {"fsmvpc.kugou.com", "fsmvpc.tx.kugou.com"}
_device_lock = threading.Lock()
_public_sessions = {}
_probe_slots = threading.BoundedSemaphore(3)


def base():
    value = os.environ.get("CM_KUGOU_BASE", "").strip().rstrip("/")
    try:
        parsed = urlsplit(value)
        if (parsed.scheme == "http" and parsed.hostname == "127.0.0.1" and parsed.port
                and not (parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password)):
            return value
    except ValueError:
        pass
    return ""


def playback_enabled():
    return bool(base() and os.environ.get("CM_KUGOU_PLAYBACK") == "1" and os.environ.get("CM_KUGOU_COOKIE_FILE"))


def authorized_user(user, capability="access.playback"):
    if not user or user.get("disabled"):
        return False
    try:
        import cm_permissions
    except ModuleNotFoundError as exc:
        if exc.name != "cm_permissions":
            raise
        return True  # Older deployments gate playback through the authenticated session alone.
    return not capability or cm_permissions.allowed(user, capability)


def server_cookie():
    try:
        path = Path(os.environ.get("CM_KUGOU_COOKIE_FILE", ""))
        with path.open(encoding="ascii") as source:
            if os.fstat(source.fileno()).st_mode & 0o077:
                raise ValueError("unprotected cookie file")
            value = source.read(8193).strip()
        entries = dict(part.strip().split("=", 1) for part in value.split(";"))
        if (len(value) <= 8192 and re.fullmatch(r"[\x20-\x7e]+", value) and entries.get("token")
                and entries.get("userid", "").isdigit() and entries.get("dfid")):
            return value
    except (OSError, ValueError):
        pass
    raise Error(503, "kugou_session_unavailable", "酷狗服务端会话暂不可用")


def _api(path, params=None, *, cookie="", jar=None, playback=False, payload=None, cookie_names=()):
    url = base()
    if not url:
        raise Error(503, "kugou_unavailable", "酷狗服务暂不可用")
    if params:
        path += "?" + urlencode(params)
    headers = {"Cookie": cookie, "Accept": "application/json"}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload, ensure_ascii=False).encode()
    req = Request(url + path, headers=headers, data=data)
    try:
        try:
            response = _opener.open(req, timeout=7)
        except HTTPError as exc:
            response = exc
        with response:
            result = json.loads(response.read(2 * 1024 * 1024))
            status = response.status
            if jar is not None:
                for line in response.headers.get_all("Set-Cookie", []):
                    for key, item in SimpleCookie(line).items():
                        if ((key in cookie_names or key == "dfid" or key.startswith("KUGOU_API_"))
                                and item.value not in ("", "undefined", "-", "null")):
                            jar[key] = item.value
    except (OSError, ValueError, CookieError):
        raise Error(502, "kugou_upstream_error", "酷狗上游暂不可用") from None
    if not isinstance(result, dict):
        raise Error(502, "kugou_upstream_error", "酷狗上游暂不可用")
    if playback and (status in (401, 403) or result.get("error_code") in (101, 20028)):
        raise Error(503, "kugou_session_unavailable", "酷狗服务端会话需要重新验证")
    if status != 200 or result.get("error_code"):
        raise Error(502, "kugou_upstream_error", "酷狗上游暂不可用")
    return result


def _catalog_cookie():
    upstream = base()
    with _device_lock:
        jar = _public_sessions.setdefault(upstream, {})
        if not jar.get("dfid"):
            _api("/register/dev", jar=jar)
        return "; ".join(f"{key}={value}" for key, value in jar.items())


def _image(value):
    if not isinstance(value, str):
        return ""
    try:
        parsed = urlsplit(value.replace("{size}", "240"))
        if (parsed.scheme in ("http", "https") and parsed.hostname and parsed.hostname.endswith(('.kugou.com', '.kgimg.com'))
                and not (parsed.username or parsed.password or parsed.port)):
            return urlunsplit(parsed._replace(scheme="https"))
    except ValueError:
        pass
    return ""


def _duration(value):
    return value if type(value) is int and 0 < value <= 24 * 60 * 60 * 1000 else 0


def video_hash(value):
    value = str(value or "").lower()
    return value if _hash.fullmatch(value) else ""


def _track(ident, name, artist, album, artwork, duration, mv=None):
    return {"id": ident, "kind": "song", "name": str(name or "")[:300],
            "artists": str(artist or "")[:300], "album": str(album or "")[:300],
            "artwork": _image(artwork), "durationMs": _duration(duration), "videoId": video_hash(mv)}


def search(query, offset, limit):
    cookie = _catalog_cookie()
    page_size = 50
    page = 1 + offset // page_size
    skip = offset % page_size
    rows = []
    has_more = False
    while len(rows) < limit:
        data = _api("/search", {"keywords": query, "type": "song", "page": page, "pagesize": page_size},
                    cookie=cookie).get("data") or {}
        batch = data.get("lists") or []
        size = len(batch)
        take = min(size, skip + limit - len(rows))
        rows.extend(batch[skip:take])
        total = data.get("total")
        has_more = take < size or (size == page_size and (type(total) is not int or page * page_size < total))
        if not has_more or size < page_size:
            break
        page += 1
        skip = 0
    items = []
    for row in rows:
        ident = f"{str(row.get('FileHash') or '').lower()}-{row.get('MixSongID')}"
        if _record.fullmatch(ident):
            duration = row.get("Duration")
            items.append(_track(ident, row.get("SongName"), row.get("SingerName"), row.get("AlbumName"),
                                row.get("Image"), duration * 1000 if type(duration) is int else 0, row.get("MvHash")))
    return {"items": items, "nextOffset": offset + len(rows) if rows and has_more else None}


def _track_parts(row):
    metadata, audio = row.get("base") or {}, row.get("audio_info") or {}
    ident = f"{str(audio.get('hash') or '').lower()}-{metadata.get('album_audio_id')}"
    if not _record.fullmatch(ident):
        return None
    resource = _track(ident, metadata.get("songname"), metadata.get("author_name"), metadata.get("album_name"),
                      (row.get("album_info") or {}).get("sizable_cover"), audio.get("timelength"))
    return resource, metadata, audio


def _details(mix_ids):
    payload = _api("/krm/audio", {"album_audio_id": ",".join(mix_ids), "fields": "base,audio_info,album_info"},
                   cookie=_catalog_cookie())
    result = {}
    for row in payload.get("data") or []:
        if isinstance(row, dict) and (parts := _track_parts(row)):
            result[parts[0]["id"]] = parts
    return result


def track(ident):
    match = _record.fullmatch(ident)
    if not match:
        raise Error(400, "invalid_input", "酷狗曲目 ID 格式错误")
    result = _details([match[2]])
    if ident in result:
        return result[ident]
    raise Error(404, "not_found", "未找到该酷狗录音")


def _lx_track(ident, cookie):
    match = _record.fullmatch(ident)
    if match:
        try:
            return track(ident)
        except Error as exc:
            if exc.status != 404:
                raise
        digest = match[1]
    elif _hash.fullmatch(ident):
        digest = ident
    else:
        raise Error(400, "invalid_input", "酷狗录音哈希或专辑音频 ID 格式错误")
    # 部分落雪歌单只提供哈希，或使用 audio_group_id 作为 albumAudioId。
    reply = _api("/privilege/lite", {"hash": digest, "qualities": "128"}, cookie=cookie, playback=True)
    for row in reply.get("data") or []:
        if not isinstance(row, dict) or str(row.get("hash") or "").lower() != digest:
            continue
        candidate = f"{digest}-{row.get('album_audio_id')}"
        if candidate != ident and _record.fullmatch(candidate):
            return track(candidate)
    raise Error(404, "not_found", "未找到该酷狗录音")


def tracks(ids):
    mix_ids = list(dict.fromkeys(ident.split("-")[1] for ident in ids))
    result = {}
    for index in range(0, len(mix_ids), 20):
        result.update(_details(mix_ids[index:index + 20]))
    if any(ident not in result for ident in ids):
        raise Error(404, "not_found", "未找到所选酷狗录音")
    return [result[ident][0] for ident in ids]


def _variants(ident, metadata, audio, cookie):
    digest, mix_id = ident.split("-")
    info = _api("/privilege/lite", {"hash": digest, "album_audio_id": mix_id,
                                   "album_id": metadata.get("album_id") or 0, "qualities": "128,320,flac"},
                cookie=cookie, playback=True)
    listed = ((info.get("quality_vip") or [{}])[0]).get("qualities") or []
    found = {str(row.get("quality")): str(row.get("hash") or "").lower() for row in listed if row.get("exists")}
    default = {"128": digest, "320": str(audio.get("hash_320") or "").lower(),
               "flac": str(audio.get("hash_flac") or "").lower()}
    return {level: list(dict.fromkeys(h for h in (found.get(key, ""), default[key]) if _hash.fullmatch(h)))
            for level, key in _levels.items()}


def qualities(ident):
    _, metadata, audio = track(ident)
    variants = _variants(ident, metadata, audio, server_cookie())
    return {"levels": [level for level in _levels if variants[level]]}


def _cdn_url(value, *, video=False):
    allowed = _video_hosts if video else {host.strip() for host in os.environ.get("CM_KUGOU_CDN_HOSTS", _cdn_default).split(",")}
    try:
        url = urlsplit(value)
        return (url.scheme in ("http", "https") and url.hostname in allowed and url.hostname.endswith(".kugou.com")
                and not (url.username or url.password or url.fragment) and url.port is None)
    except (ValueError, TypeError, AttributeError):
        return False


@dataclass
class Ticket:
    url: str
    uid: int
    mime: str
    expires: float
    budget: int = 0
    video: bool = False
    room_template: bool = False
    room: tuple | None = None


_tickets = {}
_ticket_lock = threading.Lock()


def _issue(url, uid, mime="", ttl=_ticket_ttl, *, video=False, room_template=False, room=None):
    token = secrets.token_urlsafe(24)
    now = time.monotonic()
    with _ticket_lock:
        for key in [key for key, ticket in _tickets.items() if ticket.expires <= now]:
            del _tickets[key]
        if room:
            for existing_token, ticket in _tickets.items():
                if (ticket.room == room and ticket.uid == uid and ticket.url == url
                        and abs(ticket.expires - (now + ttl)) < 2):
                    return existing_token
        if len(_tickets) >= 2048:
            raise Error(503, "kugou_busy", "酷狗播放暂时繁忙")
        _tickets[token] = Ticket(url, uid, mime, now + ttl, 2 * 1024 * 1024 if not uid else 0,
                                 video, room_template, room)
    return token



def _room_template(value):
    token = value.removeprefix('kugou:') if isinstance(value, str) and value.startswith('kugou:') else ''
    with _ticket_lock:
        ticket = _tickets.get(token)
    if ticket and ticket.room_template and ticket.expires > time.monotonic():
        return ticket
    return None


def room_template_available(value):
    return _room_template(value) is not None


def room_member_media(value, uid, room_id, revision, media_epoch, origin):
    _https_origin(origin)
    template = _room_template(value)
    if not template:
        raise Error(410, 'kugou_media_expired', '房间音源已过期')
    ttl = int(template.expires - time.monotonic())
    if ttl <= 0:
        raise Error(410, 'kugou_media_expired', '房间音源已过期')
    token = _issue(template.url, uid, template.mime, ttl, room=(room_id, revision, media_epoch))
    return {'url': origin + '/cm/kugou/media/' + token, 'expiresIn': ttl}

def _probe(url):
    if not _probe_slots.acquire(blocking=False):
        raise Error(503, "kugou_busy", "酷狗音质探测暂时繁忙")
    token = None
    try:
        token = _issue(url, 0, ttl=12)
        port = int(os.environ.get("CM_PORT", "3010"))
        command = [os.environ.get("CM_FFPROBE", "ffprobe"), "-v", "error", "-probesize", "131072",
                   "-analyzeduration", "2000000", "-select_streams", "a:0", "-show_entries",
                   "stream=codec_name:format=duration,bit_rate,size", "-of", "json",
                   f"http://127.0.0.1:{port}/kugou/media/{token}"]
        proc = subprocess.run(command, capture_output=True, check=True, timeout=8)
        result = json.loads(proc.stdout)
        codec = result["streams"][0]["codec_name"]
        fmt = result["format"]
        duration = round(float(fmt["duration"]) * 1000)
        bitrate = int(fmt.get("bit_rate") or 0)
        if not bitrate and duration:
            bitrate = round(int(fmt.get("size") or 0) * 8000 / duration)
        return codec, duration, bitrate
    except (OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError):
        return "", 0, 0
    finally:
        if token:
            with _ticket_lock:
                _tickets.pop(token, None)
        _probe_slots.release()


def _https_origin(origin):
    url = urlsplit(origin)
    if url.scheme != "https" or not url.hostname or (url.username or url.password or url.path or url.query or url.fragment):
        raise Error(503, "kugou_https_required", "酷狗播放需要 HTTPS 服务地址")


def resolve(ident, quality, uid, origin, *, room_template=False, allow_hash_lookup=False):
    if quality not in ("auto", *_levels):
        raise Error(400, "invalid_input", "不支持的酷狗音质")
    _https_origin(origin)
    cookie = server_cookie()
    if allow_hash_lookup:
        resource, metadata, audio = _lx_track(ident, cookie)
        ident = resource["id"]
    else:
        resource, metadata, audio = track(ident)
    if not resource["durationMs"]:
        raise Error(403, "playback_unavailable", "缺少完整曲目时长")
    variants = _variants(ident, metadata, audio, cookie)
    auth = (_api("/user/verify", cookie=cookie, playback=True).get("data") or {}).get("auth")
    if not auth:
        raise Error(503, "kugou_session_unavailable", "酷狗服务端会话需要重新验证")
    mix_id = ident.split("-")[1]
    for level in (_levels if quality == "auto" else (quality,)):
        for digest in variants[level]:
            params = {"hash": digest, "album_audio_id": mix_id}
            grant = (_api("/song/auth", {**params, "auth": auth}, cookie=cookie, playback=True).get("data") or {})
            if not grant.get("auth") or not grant.get("open_time"):
                continue
            reply = _api("/song/url/auth", {**params, "auth": grant["auth"], "open_time": grant["open_time"],
                                           "quality": _levels[level]}, cookie=cookie, playback=True)
            media = reply.get("data") or reply
            if reply.get("status") != 1 or any(media.get(key) in (True, 1, "1") for key in ("is_free_part", "isFreePart", "is_preview")):
                continue
            urls = []
            for key in ("url", "play_url", "playUrl", "backupUrl", "backup_url"):
                values = media.get(key)
                urls.extend(values if isinstance(values, list) else [values])
            candidates = list(dict.fromkeys(value for value in urls if isinstance(value, str) and _cdn_url(value)))
            for value in candidates[:2]:
                codec, duration, bitrate = _probe(value)
                expected = resource["durationMs"]
                if (codec not in _formats or abs(duration - expected) > max(2000, min(10000, expected * .03))
                        or (level == "lossless" and codec != "flac") or (level == "exhigh" and bitrate < 160000)):
                    continue
                file_type, mime = _formats[codec]
                ticket = _issue(value, uid, mime, room_template=room_template)
                url = 'kugou:' + ticket if room_template else origin + '/cm/kugou/media/' + ticket
                return {"url": url, "quality": level, "format": file_type,
                        "bitrate": bitrate, "expiresIn": _ticket_ttl}
    raise Error(403, "playback_unavailable", "酷狗未返回可验证的完整音频")


def resolve_video(ident, uid, origin):
    if not _hash.fullmatch(ident):
        raise Error(400, "invalid_input", "酷狗 MV ID 格式错误")
    _https_origin(origin)
    reply = _api("/video/url", {"hash": ident}, cookie=_catalog_cookie())
    data, privileges = reply.get("data") or {}, reply.get("privileges") or {}
    source = data.get(ident) or {}
    # The tracker reports a separate video privilege. Only the explicit free/full value is accepted here.
    if reply.get("status") != 1 or type(privileges.get(ident)) is not int or privileges[ident] != 0:
        raise Error(403, "playback_unavailable", "当前酷狗 MV 不可播放")
    urls = [source.get("downurl"), *(source.get("backupdownurl") or [])]
    for url in dict.fromkeys(url for url in urls if isinstance(url, str) and _cdn_url(url, video=True)):
        if _verify_video(url):
            token = _issue(url, uid, "video/mp4", video=True)
            return {"url": origin + "/cm/kugou/media/" + token, "format": "mp4", "expiresIn": _ticket_ttl}
    raise Error(403, "playback_unavailable", "酷狗未返回可验证的 MV")


def _verify_video(url):
    request = Request(url, headers={"Range": "bytes=0-15"})
    try:
        with _opener.open(request, timeout=5) as response:
            size = _media_size(response.headers.get("Content-Range", ""))
            return (response.status == 206 and 16 <= size <= _max_video_size
                    and response.headers.get("Content-Range", "").startswith("bytes 0-15/")
                    and response.read(16)[4:8] == b"ftyp")
    except (OSError, ValueError):
        return False


def _media_size(content_range):
    if _content_range.fullmatch(content_range):
        return int(content_range.rsplit("/", 1)[1])
    return 0


def stream(handler, token, authorized, room_authorized=None):
    with _ticket_lock:
        ticket = _tickets.get(token)
    if not ticket or ticket.expires <= time.monotonic():
        raise Error(410, "kugou_media_expired", "播放地址已过期，请重新取址")
    if ticket.room_template:
        raise Error(404, 'not_found', '播放地址不可用')
    if ticket.room and (not room_authorized or not room_authorized(ticket.uid, *ticket.room)):
        raise Error(403, 'room_media_forbidden', '当前试听会话已离开房间或曲目已变化')
    if ticket.uid:
        if not playback_enabled() or not authorized(ticket.uid):
            raise Error(403, "account_access_restricted", "当前账号没有播放权限")
    elif (handler.client_address[0] != "127.0.0.1" or
          handler.headers.get("Host") != f"127.0.0.1:{os.environ.get('CM_PORT', '3010')}"):
        raise Error(404, "not_found", "播放地址不可用")
    if not _cdn_url(ticket.url, video=ticket.video):
        raise Error(502, "kugou_upstream_error", "酷狗媒体地址不可用")
    byte_range = handler.headers.get("Range", "")
    if byte_range and not _range.fullmatch(byte_range):
        raise Error(416, "invalid_range", "仅支持单段字节范围")
    head = getattr(handler, "head_only", False)
    headers = {"Range": byte_range} if byte_range else {}
    req = Request(ticket.url, headers=headers, method="HEAD" if head else "GET")
    try:
        try:
            response = _opener.open(req, timeout=10)
        except HTTPError as exc:
            response = exc
        with response:
            status = response.status
            length = response.headers.get("Content-Length", "")
            content_range = response.headers.get("Content-Range", "")
            max_size = _max_video_size if ticket.video else _max_size
            if (status not in (200, 206, 416) or not re.fullmatch(r"[0-9]{1,12}", length)
                    or int(length) > max_size or (status in (206, 416) and not 0 < _media_size(content_range) <= max_size)):
                raise Error(502, "kugou_upstream_error", "酷狗媒体暂不可用")
            handler.send_response(status)
            handler.send_header("Content-Type", ticket.mime or "application/octet-stream")
            handler.send_header("Content-Length", length if status != 416 else "0")
            handler.send_header("Accept-Ranges", "bytes")
            if content_range and _content_range.fullmatch(content_range):
                handler.send_header("Content-Range", content_range)
            handler.send_header("Access-Control-Allow-Origin", "*")
            handler.send_header("Access-Control-Expose-Headers", "Accept-Ranges, Content-Length, Content-Range")
            handler.send_header("Referrer-Policy", "no-referrer")
            handler.send_header("Cache-Control", "no-store")
            handler.end_headers()
            if head or status == 416:
                return
            try:
                remaining = int(length)
                while remaining:
                    size = min(65536, remaining)
                    if not ticket.uid:
                        with _ticket_lock:
                            size = min(size, ticket.budget)
                            ticket.budget -= size
                    chunk = response.read(size) if size else b""
                    if not chunk:
                        break
                    handler.wfile.write(chunk)
                    remaining -= len(chunk)
            except OSError:
                handler.close_connection = True
    except OSError:
        raise Error(502, "kugou_upstream_error", "酷狗音频暂不可用") from None
