"""Versioned, declarative music-service API for independently connected clients."""
from dataclasses import dataclass
from html import escape
import json
import os
import re
from urllib.parse import urlencode, urlsplit

import cm_db
import cm_bilibili
import cm_kugou
import cm_kugou_account
import cm_kugou_catalog
import cm_kugou_library
import cm_ncm
import cm_passport
import cm_qq
import cm_qq_catalog

PREFIX = "/music-service"
VERSION = 1
X_CONNECT = "https://currentmusicx.bileizhen.top/connect"
X_CALLBACK = X_CONNECT + "/callback"
CLIENT_ENV = "CM_MUSIC_SERVICE_CLIENT_ID"
DEFAULT_CLIENT_ID = "cs_app_currentmusicx_music_service"
MEDIA_ENV = "CM_MUSIC_SERVICE_MEDIA"
LEVELS = ("standard", "higher", "exhigh", "lossless", "hires", "sky", "jyeffect", "jymaster", "dolby", "vivid")
ID = re.compile(r"[A-Za-z0-9_-]{1,80}")


class Error(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code = status, code


@dataclass(frozen=True)
class Operation:
    label: str
    run: object
    scope: str = ""
    providers: tuple = ("netease",)
    media: str = ""
    write: bool = False


def issuer(origin):
    return origin.rstrip("/") + "/cm"


def app(origin):
    client_id = os.environ.get(CLIENT_ENV, DEFAULT_CLIENT_ID).strip()
    entry = cm_db.passport_app_get(client_id) if origin.startswith("https://") and client_id else None
    return entry if (entry and not entry["disabled"] and not entry["secret_hash"]
                     and X_CALLBACK in json.loads(entry["redirects"])) else None


def media_enabled(name):
    return not name or name in {scope.strip() for scope in os.environ.get(MEDIA_ENV, "").split(",")}


def descriptor(origin):
    client = app(origin)
    usable_scopes = set(cm_passport.scope_names(client["scopes"])) if client else set()
    platforms = {}
    for provider in ("netease", "qqmusic", "kugou", "bilibili"):
        if provider == "qqmusic" and not cm_qq.BASE:
            continue
        if provider == "kugou" and not cm_kugou.base():
            continue
        if provider == "bilibili" and not cm_bilibili.base():
            continue
        platforms[provider] = [
            {"id": key, "label": op.label, "scope": op.scope or None, "write": op.write,
             **({"kinds": list(cm_kugou_catalog.SEARCH_TYPES)} if provider == "kugou" and key == "catalog.search" else {}),
             **({"kinds": ["song", "mv"]} if provider == "bilibili" and key == "catalog.search" else {})}
            for key, op in OPERATIONS.items()
            if provider in op.providers and operation_enabled(provider, op) and (not op.scope or op.scope in usable_scopes)
        ]
    base = issuer(origin)
    return {
        "format": "currentmusic.music-service",
        "version": VERSION,
        "id": "currentmusic-engine",
        "name": "流声引擎",
        "apiBase": base + PREFIX + "/v1",
        "platforms": platforms,
        "authorization": {
            "issuer": base,
            "clientId": client["key"],
            "authorizeUrl": origin + "/app/#/passport/authorize",
            "tokenUrl": base + "/passport/token",
            "revokeUrl": base + "/passport/revoke",
        } if client else None,
    }


def connection_page(origin):
    profile = issuer(origin) + PREFIX + "/descriptor"
    link = X_CONNECT + "?" + urlencode({"descriptor": profile})
    return f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer"><title>连接流声引擎</title>
<main><h1>连接流声引擎</h1>
<p>在流声音乐里确认连接。需要个人音乐权限时，浏览器会打开本服务的授权页。</p>
<p><a href="{escape(link, quote=True)}">在流声音乐中连接</a></p>
<p><a href="/api-docs/reference/guide-music-service-api/">接入文档</a></p>
</main></html>"""


def error(status, code, message):
    raise Error(status, code, message)


def string(data, name, limit=200, *, default=""):
    value = data.get(name, default)
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 for c in value):
        error(400, "invalid_input", f"{name} 格式错误")
    return value.strip()


def integer(data, name, lo, hi, default=None):
    value = data.get(name, default)
    if type(value) is not int or not lo <= value <= hi:
        error(400, "invalid_input", f"{name} 超出允许范围")
    return value


def reference(data, name="id"):
    value = string(data, name, 80)
    if not ID.fullmatch(value):
        error(400, "invalid_input", f"{name} 格式错误")
    return value


def ncm_id(data, name="id"):
    value = reference(data, name)
    if not value.isascii() or not value.isdigit() or not 0 < int(value) <= 2**63 - 1:
        error(400, "invalid_input", f"{name} 须为正整数 ID 字符串")
    return int(value)


def paging(data):
    return integer(data, "offset", 0, 1000, 0), integer(data, "limit", 1, 50, 30)


def public_url(value):
    if not isinstance(value, str) or len(value) > 4096 or any(ord(c) < 32 for c in value):
        return ""
    try:
        url = urlsplit(value)
        return value if url.scheme == "https" and url.hostname and not url.username and not url.password else ""
    except ValueError:
        return ""


def resource(row, kind="song"):
    ident = row.get("ncm_id") or row.get("id")
    if not ident:
        return None
    duration = row.get("duration")
    return {
        "id": str(ident), "kind": kind, "name": str(row.get("name") or "")[:300],
        "artists": str(row.get("artists") or row.get("artist") or "")[:300],
        "album": str(row.get("album") or "")[:300],
        "artwork": public_url(row.get("pic") or row.get("cover") or row.get("coverImgUrl")),
        "durationMs": max(0, duration) if type(duration) is int else 0,
        "videoId": str(row["mv"]) if row.get("mv") else "",
    }


def resources(rows, kind="song"):
    return [item for row in rows if isinstance(row, dict) and (item := resource(row, kind))]


def page(items, offset=0, more=False):
    return {"items": items, "nextOffset": offset + len(items) if more and items else None}


def qq(path, query):
    return cm_qq_catalog.read(path, query, None, cm_qq.call, cm_qq.Error)


def search(provider, data, _uid, ip):
    query = string(data, "query", 60)
    kind = string(data, "kind", 15, default="song")
    if not query or kind not in ("song", "artist", "album", "playlist"):
        error(400, "invalid_input", "需要有效的搜索词和资源类型")
    offset, limit = paging(data)
    result = (cm_ncm.search_types(query, (kind,), limit=limit, offset=offset, real_ip=ip) if provider == "netease"
              else qq("/search", {"keywords": query, "type": kind, "offset": offset, "limit": limit}))
    key = kind + "s"
    return page(resources(result.get(key, []), kind), offset, result["hasMore"].get(kind, False))


def details(kind):
    def run(provider, data, _uid, _ip):
        ident = str(ncm_id(data)) if provider == "netease" else reference(data)
        if kind == "song":
            rows = cm_ncm.song_detail([ident]) if provider == "netease" else qq("/song/detail", {"ids": ident})["songs"]
            items = resources(rows)
            if not items:
                error(404, "not_found", "歌曲不存在")
            return {"resource": items[0]}
        offset, limit = paging(data)
        if provider == "qqmusic":
            result = qq("/" + kind, {"id": ident, "offset": offset, "limit": limit})
        elif kind == "album":
            result = cm_ncm.album_songs(ident)
        elif kind == "artist":
            result = cm_ncm.artist_works(ident, limit, offset)
        else:
            result = cm_ncm.playlist_full("", ident)
        tracks = resources(result.get("songs", []))
        if kind != "artist":
            tracks, more = tracks[offset:offset + limit], len(tracks) > offset + limit
        else:
            more = result.get("more", result.get("hasMore", False))
        return {
            "resource": resource({**result, "id": ident}, kind),
            "tracks": tracks, "albums": resources(result.get("albums", []), "album"),
            "nextOffset": offset + len(tracks) if more and tracks else None,
        }
    return run


def hot(_provider, _data, _uid, _ip):
    return {"items": [h["name"] for h in cm_ncm.search_hot()]}


def suggest(_provider, data, _uid, ip):
    query = string(data, "query", 60)
    if not query:
        error(400, "invalid_input", "需要搜索词")
    reply = cm_ncm.call("/search/suggest", {"keywords": query, "type": "mobile"}, with_cookie=False, real_ip=ip)
    return {"items": [str(row.get("keyword") or "") for row in (reply.get("result") or {}).get("allMatch", [])][:20]}


def charts(_provider, _data, _uid, _ip):
    reply = cm_ncm.call("/toplist", with_cookie=False)
    return page(resources(reply.get("list", []), "playlist"))


def binding(uid):
    bound = cm_db.get_binding(uid)
    if not bound or not bound["cookie"] or bound.get("stale"):
        error(403, "binding_required", "请在服务网页绑定或重新连接网易云账号")
    return bound["cookie"]


def recommendation(_provider, _data, uid, ip):
    reply = cm_ncm.call("/recommend/songs", cookie=binding(uid), real_ip=ip)
    return page(resources([cm_ncm._norm_song(row) for row in (reply.get("data") or {}).get("dailySongs", [])]))


def lyrics(provider, data, _uid, _ip):
    return (cm_ncm.lyric(ncm_id(data)) if provider == "netease"
            else qq("/lyric", {"id": reference(data)}))


def comments(_provider, data, _uid, _ip):
    sort = string(data, "sort", 12, default="hot")
    if sort not in cm_ncm.COMMENT_SORTS:
        error(400, "invalid_input", "不支持的评论排序")
    return cm_ncm.comment_page(ncm_id(data), 0, sort, integer(data, "page", 1, 500, 1),
                               integer(data, "limit", 1, 50, 20), string(data, "cursor", 120) or None,
                               cookie="")


def comment_add(_provider, data, uid, ip):
    text = string(data, "text", 300)
    if not text:
        error(400, "invalid_input", "评论不能为空")
    return {"ok": bool(cm_ncm.comment_post(binding(uid), ncm_id(data), text, real_ip=ip))}


def qualities(_provider, data, uid, _ip):
    ncm_id(data)
    binding(uid)
    return {"levels": list(LEVELS)}


def play(_provider, data, uid, ip):
    ident = ncm_id(data)
    level = string(data, "quality", 16, default="auto")
    if level != "auto" and level not in LEVELS:
        error(400, "invalid_input", "不支持的音质")
    cookie = binding(uid)
    if level == "auto":
        level = "jymaster"
    reply = cm_ncm.call("/song/url/v1", {"id": ident, "level": level}, cookie=cookie,
                        real_ip=ip, bypass_cache=True, timeout=8, retries=0)
    rows = reply.get("data") or []
    audio = next((row for row in rows if str(row.get("id")) == str(ident)), {})
    url = public_url(cm_ncm._https(audio.get("url")))
    if reply.get("code") != 200 or not url or audio.get("freeTrialInfo") is not None or audio.get("code") != 200:
        error(403, "playback_unavailable", "当前曲目或音质不可播放，可能受账号或地区权限限制")
    return {"url": url, "quality": audio.get("level") or "", "format": audio.get("type") or "",
            "bitrate": audio.get("br") or 0, "expiresIn": audio.get("expi") or 60}


def mv(_provider, data, uid, ip):
    reply = cm_ncm.call("/mv/url", {"id": ncm_id(data)}, cookie=binding(uid), real_ip=ip, bypass_cache=True)
    result = reply.get("data") or {}
    url = public_url(cm_ncm._https(result.get("url")))
    if reply.get("code") != 200 or not url or result.get("fee") or result.get("freeTrialInfo"):
        error(403, "playback_unavailable", "当前 MV 不可播放")
    return {"url": url, "format": "mp4", "expiresIn": 60}


def download(_provider, data, uid, ip):
    ident = ncm_id(data)
    level = string(data, "quality", 16, default="auto")
    if level != "auto" and level not in LEVELS:
        error(400, "invalid_input", "不支持的音质")
    reply = cm_ncm.call("/song/download/url/v1", {"id": ident, "level": "lossless" if level == "auto" else level},
                        cookie=binding(uid), real_ip=ip, bypass_cache=True, timeout=8, retries=0)
    result = reply.get("data") or {}
    url = public_url(cm_ncm._https(result.get("url")))
    if (reply.get("code") != 200 or result.get("code") != 200 or not url
            or str(result.get("id")) != str(ident) or result.get("freeTrialInfo") is not None):
        error(403, "download_unavailable", "当前账号或曲目不允许下载")
    return {"url": url, "quality": result.get("level") or "", "format": result.get("type") or "",
            "bitrate": result.get("br") or 0, "expiresIn": result.get("expi") or 60}


def favorites(_provider, data, uid, _ip):
    offset, limit = paging(data)
    ids = cm_db.like_ids(uid, table="favs", limit=offset + limit + 1)
    rows = cm_db.song_rows_to_meta(cm_db.songs_by_ids(ids[offset:offset + limit]))
    return page(resources(rows), offset, len(ids) > offset + limit)


def favorite_set(_provider, data, uid, _ip):
    ident, selected = ncm_id(data), data.get("selected")
    if type(selected) is not bool:
        error(400, "invalid_input", "selected 须为布尔值")
    if selected:
        save_tracks([ident])
    cm_db.set_favorite(uid, ident, selected)
    return {"selected": selected}


def save_tracks(ids):
    for track in cm_ncm.song_detail(ids):
        if track.get("ncm_id"):
            cm_db.upsert_song(track)


def user_playlists(_provider, data, uid, _ip):
    offset, limit = paging(data)
    rows = cm_db.playlists_of(uid)
    return page(resources(rows[offset:offset + limit], "playlist"), offset, len(rows) > offset + limit)


def owned_playlist(data, uid):
    playlist = cm_db.get_playlist(ncm_id(data))
    if not playlist or playlist["user_id"] != uid:
        error(404, "not_found", "歌单不存在")
    return playlist


def user_playlist(_provider, data, uid, _ip):
    playlist = owned_playlist(data, uid)
    offset, limit = paging(data)
    ids = cm_db.playlist_track_ids(playlist["id"])
    tracks = resources(cm_db.song_rows_to_meta(cm_db.songs_by_ids(ids[offset:offset + limit])))
    return {"resource": resource(playlist, "playlist"), "tracks": tracks,
            "nextOffset": offset + len(tracks) if len(ids) > offset + limit and tracks else None}


def playlist_create(_provider, data, uid, _ip):
    name = string(data, "name", 100)
    if not name:
        error(400, "invalid_input", "需要歌单名称")
    ident = cm_db.create_playlist(uid, name, string(data, "description", 300))
    return {"resource": resource(cm_db.get_playlist(ident), "playlist")}


def playlist_edit(_provider, data, uid, _ip):
    playlist = owned_playlist(data, uid)
    name = string(data, "name", 100)
    if not name:
        error(400, "invalid_input", "需要歌单名称")
    fields = {"name": name}
    if "description" in data:
        fields["description"] = string(data, "description", 300)
    cm_db.update_playlist(playlist["id"], fields)
    return {"resource": resource(cm_db.get_playlist(playlist["id"]), "playlist")}


def playlist_delete(_provider, data, uid, _ip):
    cm_db.delete_playlist(owned_playlist(data, uid)["id"])
    return {"ok": True}


def playlist_tracks(_provider, data, uid, _ip):
    playlist = owned_playlist(data, uid)
    ids = data.get("trackIds")
    if not isinstance(ids, list) or not 1 <= len(ids) <= 50:
        error(400, "invalid_input", "需要 1–50 个歌曲 ID")
    ids = [ncm_id({"id": ident}) for ident in ids]
    selected = data.get("selected")
    if type(selected) is not bool:
        error(400, "invalid_input", "selected 须为布尔值")
    if selected:
        save_tracks(ids)
        cm_db.playlist_add_tracks(playlist["id"], ids)
    else:
        cm_db.playlist_del_tracks(playlist["id"], ids)
    return {"ok": True}


def recent(_provider, data, uid, _ip):
    offset, limit = paging(data)
    ids = cm_db.recent_plays(uid, offset + limit + 1)
    tracks = cm_db.song_rows_to_meta(cm_db.songs_by_ids(ids[offset:offset + limit]))
    return page(resources(tracks), offset, len(ids) > offset + limit)


def history_record(_provider, data, uid, _ip):
    ident = ncm_id(data)
    if "playId" in data:
        play_id = ncm_id(data, "playId")
        elapsed = integer(data, "elapsedMs", 0, 4 * 3600 * 1000)
        cm_db.update_play_total(uid, ident, play_id, elapsed)
        return {"ok": True, "playId": str(play_id)}
    # Resolve server-side metadata before storing it in the existing library.
    if not cm_db.songs_by_ids([ident]):
        save_tracks([ident])
    return {"ok": True, "playId": str(cm_db.record_play(uid, ident))}


def roaming_songs(_provider, data, uid, ip):
    cookie = binding(uid)
    limit = integer(data, "limit", 1, 30, 20)
    reply = cm_ncm.call("/personal_fm", cookie=cookie, real_ip=ip)
    return page(resources([cm_ncm._norm_song(row) for row in (reply.get("data") or [])[:limit]]))


def cloud(_provider, data, uid, ip):
    offset, limit = paging(data)
    result = cm_ncm.call("/user/cloud", {"offset": offset, "limit": limit},
                         cookie=binding(uid), real_ip=ip)
    tracks = [cm_ncm._norm_song(row["simpleSong"]) for row in result.get("data", []) if row.get("simpleSong")]
    return page(resources(tracks), offset, result.get("hasMore", False))


BOTH = ("netease", "qqmusic")
OPERATIONS = {
    "catalog.search": Operation("搜索", search, providers=(*BOTH, "kugou", "bilibili")),
    "catalog.track": Operation("歌曲详情", details("song"), providers=(*BOTH, "kugou", "bilibili")),
    "catalog.podcast": Operation("有声节目详情", None, providers=("kugou",)),
    "discover.podcasts": Operation("发现有声节目", None, providers=("kugou",)),
    "catalog.video": Operation("MV 详情", None, providers=("kugou", "bilibili")),
    "catalog.album": Operation("专辑", details("album"), providers=(*BOTH, "kugou")),
    "catalog.artist": Operation("歌手", details("artist"), providers=(*BOTH, "kugou")),
    "catalog.playlist": Operation("公开歌单", details("playlist"), providers=(*BOTH, "kugou")),
    "discover.hot": Operation("热搜", hot, providers=("netease", "kugou")),
    "discover.suggest": Operation("搜索联想", suggest, providers=("netease", "kugou")),
    "discover.charts": Operation("榜单", charts, providers=("netease", "kugou")),
    "discover.recommendations": Operation("每日推荐", recommendation, "music.library.read", providers=("netease", "kugou")),
    "discover.playlists": Operation("推荐歌单", None, providers=("kugou",)),
    "discover.playlist.categories": Operation("歌单分类", None, providers=("kugou",)),
    "discover.playlist.directory": Operation("歌单广场", None, providers=("kugou",)),
    "discover.new.songs": Operation("推荐新歌", None, providers=("kugou",)),
    "discover.artists": Operation("歌手分类", None, providers=("kugou",)),
    "track.insights": Operation("歌曲与音质资料", None, providers=("kugou",)),
    "track.lyrics": Operation("歌词", lyrics, providers=(*BOTH, "kugou"), media="lyrics"),
    "track.comments": Operation("歌曲评论", comments, providers=("netease", "kugou")),
    "track.comment.replies": Operation("歌曲评论楼层", None, providers=("kugou",)),
    "track.comment.add": Operation("发表评论", comment_add, "music.comment.write", providers=("netease", "kugou"), write=True),
    "playback.qualities": Operation("音质档位", qualities, "music.play", providers=("netease", "kugou", "bilibili"), media="playback"),
    "playback.resolve": Operation("播放取址", play, "music.play", providers=("netease", "kugou", "bilibili"), media="playback"),
    "playback.mv": Operation("MV 取址", mv, "music.play", providers=("netease", "kugou", "bilibili"), media="mv"),
    "playback.download": Operation("下载取址", download, "music.download", media="download"),
    "library.favorites": Operation("收藏", favorites, "music.library.read", providers=("netease", "kugou")),
    "library.favorite.status": Operation("查询收藏状态", None, "music.library.read", providers=("kugou",)),
    "library.favorite.set": Operation("修改收藏", favorite_set, "music.library.write", providers=("netease", "kugou"), write=True),
    "library.playlists": Operation("个人歌单", user_playlists, "music.library.read", providers=("netease", "kugou")),
    "library.playlist": Operation("个人歌单详情", user_playlist, "music.library.read", providers=("netease", "kugou")),
    "library.playlist.create": Operation("新建歌单", playlist_create, "music.library.write", providers=("netease", "kugou"), write=True),
    "library.playlist.edit": Operation("编辑歌单", playlist_edit, "music.library.write", providers=("netease", "kugou"), write=True),
    "library.playlist.delete": Operation("删除歌单", playlist_delete, "music.library.write", providers=("netease", "kugou"), write=True),
    "library.playlist.tracks": Operation("修改歌单曲目", playlist_tracks, "music.library.write", providers=("netease", "kugou"), write=True),
    "library.cloud": Operation("云盘曲库", cloud, "music.cloud.read", providers=("netease", "kugou"), media="cloud"),
    "history.summary": Operation("听歌足迹", None, "music.history.read", providers=("kugou",)),
    "history.recent": Operation("最近播放", recent, "music.history.read", providers=("netease", "kugou")),
    "history.record": Operation("记录播放进度", history_record, "music.history.write", providers=("netease", "kugou"), write=True),
    "roaming.songs": Operation("私人漫游", roaming_songs, "music.library.read", providers=("netease", "kugou")),
    "roaming.stations": Operation("漫游电台", None, "music.library.read", providers=("kugou",)),
    "roaming.skip": Operation("隐藏漫游曲目", None, "music.library.write", providers=("kugou",), write=True),
}


def scope_capability(scope):
    if scope in ("music.play", "music.download"):
        return "access.playback"
    if scope.startswith(("music.library.", "music.cloud.")):
        return "access.library"
    if scope == "music.comment.write":
        return "access.community"
    return ""


def _kugou_track_id(data):
    ident = reference(data)
    if not cm_kugou._record.fullmatch(ident):
        error(400, "invalid_input", "酷狗曲目 ID 格式错误")
    return ident


def _kugou_track_ids(data, key, *, maximum=50):
    ids = data.get(key)
    if not isinstance(ids, list) or not 1 <= len(ids) <= maximum:
        error(400, "invalid_input", f"需要 1–{maximum} 个酷狗歌曲 ID")
    return list(dict.fromkeys(_kugou_track_id({"id": ident}) for ident in ids))


def _kugou_selected(data):
    selected = data.get("selected")
    if type(selected) is not bool:
        error(400, "invalid_input", "selected 须为布尔值")
    return selected


def _kugou_personal(operation, data, uid):
    library = cm_kugou_library
    if operation == "library.favorites":
        return library.favorites(uid, *paging(data))
    if operation == "library.favorite.status":
        return library.favorite_status(uid, _kugou_track_ids(data, "ids", maximum=100))
    if operation == "library.favorite.set":
        return library.favorite_set(uid, _kugou_track_id(data), _kugou_selected(data))
    if operation == "library.playlists":
        return library.playlists(uid, *paging(data))
    if operation == "library.playlist":
        return library.playlist(uid, reference(data), *paging(data))
    if operation in ("library.playlist.create", "library.playlist.edit"):
        name = string(data, "name", 100)
        if not name.strip():
            error(400, "invalid_input", "需要歌单名称")
        description = string(data, "description", 300)
        if operation == "library.playlist.create":
            ids = _kugou_track_ids(data, "trackIds") if "trackIds" in data else []
            return library.playlist_create(uid, name, description, ids)
        return library.playlist_edit(uid, reference(data), name, description if "description" in data else None)
    if operation == "library.playlist.delete":
        return library.playlist_delete(uid, reference(data))
    if operation == "library.playlist.tracks":
        return library.playlist_tracks(uid, reference(data), _kugou_track_ids(data, "trackIds"), _kugou_selected(data))
    if operation == "history.summary":
        return library.summary(uid, integer(data, "limit", 1, 30, 10))
    if operation == "history.recent":
        return library.recent(uid, *paging(data))
    if operation == "history.record":
        ident = _kugou_track_id(data)
        if "playId" in data:
            return library.record(uid, ident, ncm_id(data, "playId"), integer(data, "elapsedMs", 0, 4 * 3600 * 1000))
        return library.record(uid, ident, None, 0)
    error(404, "unsupported_operation", "该服务未开放此酷狗能力")


def principal(header, scope):
    if not header.startswith("Bearer ") or not header[7:].startswith("cs_at_"):
        error(401, "authorization_required", "需要音乐服务访问令牌")
    grant = cm_db.passport_grant_by_access(cm_passport.hash_token(header[7:].strip()))
    if not grant or grant["access_expires"] <= cm_passport.now():
        error(401, "invalid_token", "音乐服务授权已失效")
    client = cm_db.passport_app_get(grant["app_key"])
    if not client or client["disabled"] or not grant["uid"]:
        error(403, "insufficient_scope", "该授权不能访问个人音乐服务")
    user = cm_db.get_user(grant["uid"])
    if (not user or user.get("disabled") or not cm_passport.has_scope(grant["scope"], scope)
            or not cm_passport.has_scope(client["scopes"], scope)):
        error(403, "insufficient_scope", "缺少这项音乐权限")
    return grant["uid"]


def operation_enabled(provider, entry):
    if provider == "bilibili":
        return bool(cm_bilibili.base()) and (not entry.media or cm_bilibili.playback_enabled())
    if provider == "kugou":
        if entry.scope in ("music.comment.write", "music.cloud.read"):
            return cm_kugou_account.enabled()
        return bool(cm_kugou.base()) and (entry.media not in ("playback", "mv") or cm_kugou.playback_enabled())
    return media_enabled(entry.media)


def kugou_operation(operation, data, uid, origin):
    entry = OPERATIONS.get(operation)
    if not entry or "kugou" not in entry.providers or not operation_enabled("kugou", entry):
        error(404, "unsupported_operation", "该服务未开放此酷狗能力")
    if not isinstance(data, dict):
        error(400, "invalid_input", "需要 input 对象")
    if entry.scope:
        if not cm_kugou.authorized_user(cm_db.get_user(uid), scope_capability(entry.scope)):
            error(403, "account_access_restricted", "当前账号的这项服务访问权限已被限制")
    if operation == "library.cloud":
        return cm_kugou_account.cloud(uid, *paging(data))
    if operation == "track.comment.add":
        content = string(data, "text", 300)
        if not content.strip():
            error(400, "invalid_input", "评论不能为空")
        return cm_kugou_account.comment(uid, _kugou_track_id(data), content)
    if operation.startswith(("library.", "history.")):
        return _kugou_personal(operation, data, uid)
    if operation in ("catalog.search", "discover.suggest"):
        query = string(data, "query", 60)
        if not query:
            error(400, "invalid_input", "需要搜索词")
        if operation == "discover.suggest":
            return cm_kugou_catalog.suggest(query)
        kind = string(data, "kind", 15, default="song")
        if kind not in cm_kugou_catalog.SEARCH_TYPES:
            error(400, "invalid_input", "需要有效的资源类型")
        return cm_kugou_catalog.search(query, kind, *paging(data))
    if operation == "discover.hot":
        return cm_kugou_catalog.hot()
    if operation == "discover.charts":
        return cm_kugou_catalog.charts()
    if operation == "discover.recommendations":
        result = cm_kugou_catalog.recommendations()
        excluded = cm_kugou_library.roaming_exclusions(uid)
        result["items"] = [item for item in result["items"] if item["id"] not in excluded]
        return result
    if operation == "discover.playlists":
        return cm_kugou_catalog.featured_playlists()
    if operation == "discover.playlist.categories":
        return cm_kugou_catalog.playlist_categories()
    if operation == "discover.playlist.directory":
        return cm_kugou_catalog.playlist_directory(string(data, "category", 15), *paging(data))
    if operation == "discover.new.songs":
        return cm_kugou_catalog.new_songs(integer(data, "limit", 1, 30, 12))
    if operation == "discover.artists":
        return cm_kugou_catalog.artist_directory(string(data, "group", 20), *paging(data))
    if operation == "discover.podcasts":
        return cm_kugou_catalog.spoken_shows(integer(data, "limit", 1, 30, 20))
    if operation == "catalog.podcast":
        return cm_kugou_catalog.spoken_show(reference(data), *paging(data))
    if operation == "track.insights":
        return cm_kugou_catalog.track_insights(_kugou_track_id(data))
    if operation == "roaming.stations":
        return cm_kugou_catalog.radio_stations()
    if operation == "roaming.skip":
        return cm_kugou_library.roaming_skip(uid, _kugou_track_id(data))
    if operation == "roaming.songs":
        station = string(data, "station", 18)
        if station and not re.fullmatch(r"[1-9][0-9]{0,14}_[1-9][0-9]?", station):
            error(400, "invalid_input", "电台 ID 格式错误")
        seed = _kugou_track_id({"id": data["seedId"]}) if "seedId" in data else ""
        sources = []
        if not station:
            sources = [seed] if seed else [item["id"] for item in cm_kugou_library.recent(uid, 0, 3)["items"]]
        excluded = cm_kugou_library.roaming_exclusions(uid)
        if "excludeIds" in data and data["excludeIds"] != []:
            excluded.update(_kugou_track_ids(data, "excludeIds", maximum=100))
        excluded.update(sources)
        return cm_kugou_catalog.radio(integer(data, "limit", 1, 30, 20), integer(data, "offset", 0, 10000, 0),
                                     [ident.split("-")[1] for ident in sources], excluded, station)
    ident = reference(data)
    if operation == "catalog.video":
        return cm_kugou_catalog.video(ident)
    if operation == "catalog.track":
        return {"resource": cm_kugou.track(ident)[0]}
    if operation.startswith("catalog."):
        return cm_kugou_catalog.details(operation.removeprefix("catalog."), ident, *paging(data))
    if operation == "track.lyrics":
        return cm_kugou_catalog.lyrics(ident)
    if operation == "track.comment.replies":
        return cm_kugou_catalog.comment_replies(
            ident, string(data, 'commentId', 20), string(data, 'replyThread', 80),
            integer(data, 'page', 1, 500, 1), integer(data, 'limit', 1, 50, 20),
        )
    if operation == "track.comments":
        sort = string(data, "sort", 12, default="hot")
        if sort not in ("hot", "recommended") or string(data, "cursor", 120):
            error(400, "invalid_input", "酷狗评论支持热门或推荐排序，以及页码分页")
        return cm_kugou_catalog.comments(ident, integer(data, "page", 1, 500, 1),
                                        integer(data, "limit", 1, 50, 20))
    if operation == "playback.mv":
        return cm_kugou.resolve_video(ident, uid, origin)
    if operation == "playback.qualities":
        return cm_kugou.qualities(ident)
    if operation == "playback.resolve":
        quality = string(data, "quality", 16, default="auto")
        if quality == 'auto' and data.get('supportsDolby') is True:
            alternate = cm_bilibili.linked_dolby('kugou', ident, uid, origin)
            if alternate:
                return alternate
        return cm_kugou.resolve(ident, quality, uid, origin)
    error(404, "unsupported_operation", "该服务未开放此酷狗能力")


def dispatch(operation, body, header, ip, *, origin=""):
    entry = OPERATIONS.get(operation)
    provider = body.get("provider") if isinstance(body, dict) else None
    data = body.get("input", {}) if isinstance(body, dict) else None
    if not isinstance(data, dict) or not isinstance(provider, str):
        error(400, "invalid_input", "需要 provider 和 input 对象")
    if not entry or provider not in entry.providers or not operation_enabled(provider, entry):
        error(404, "unsupported_operation", "该服务或平台未开放此能力")
    if provider == "qqmusic" and not cm_qq.BASE:
        error(404, "unsupported_operation", "该服务未启用 QQ 曲库")
    uid = principal(header, entry.scope) if entry.scope else 0
    if entry.write and body.get("confirm") is not True:
        error(400, "confirmation_required", "需要确认本次写入")
    try:
        if provider == "kugou":
            return kugou_operation(operation, data, uid, origin)
        if provider == "bilibili":
            return cm_bilibili.operation(operation, data, uid, origin)
        if (provider == "netease" and operation == "playback.resolve" and cm_bilibili.playback_enabled()
                and data.get('supportsDolby') is True and data.get('quality', 'auto') == 'auto'):
            try:
                native = play(provider, {**data, 'quality': 'dolby'}, uid, ip)
                if native.get('quality') == 'dolby':
                    return native
            except (Error, cm_ncm.NcmError):
                pass
            alternate = cm_bilibili.linked_dolby('netease', ncm_id(data), uid, origin)
            if alternate:
                return alternate
        return entry.run(provider, data, uid, ip)
    except (cm_kugou.Error, cm_bilibili.Error) as exc:
        error(exc.status, exc.code, str(exc))
    except cm_qq.Error as exc:
        error(exc.status, "upstream_error", exc.body["error"])
    except cm_ncm.NcmError as exc:
        if exc.status_code in (401, 403):
            error(403, "upstream_denied", "平台拒绝本次请求，请检查账号或曲目权限")
        error(502, "upstream_error", "音乐平台暂时不可用")
