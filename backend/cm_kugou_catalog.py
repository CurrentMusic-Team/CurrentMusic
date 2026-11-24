"""Normalize the KuGou concept catalogue into CurrentMusic service resources."""

from base64 import b64decode
from binascii import Error as Base64Error
from datetime import datetime, timedelta, timezone
import json
import re

import cm_kugou as kg
from cm_ncm import _parse_lrc


SEARCH_TYPES = {"song": "song", "artist": "author", "album": "album",
                "playlist": "special", "lyric": "lyric", "mv": "mv"}
_NUMBER = re.compile(r"[1-9][0-9]{0,14}")
_COLLECTION = re.compile(r"collection_(?:[0-9]+_){2,5}[0-9]+")
_RANK = re.compile(r"rank_([1-9][0-9]{0,14})_([0-9]{1,15})")
_WORD = re.compile(r"<(\d+),(\d+),\d+>([^\n<]*)")
_LINE = re.compile(r"^\[(\d+),\d+\](.*)$")
_LANGUAGE = re.compile(r"^\[language:([^\]]+)\]$", re.M)


def _read(path, params=None):
    return kg._api(path, params, cookie=kg._catalog_cookie())


def _resource(ident, kind, name, artwork, *, artists="", description="", count=None):
    if not name:
        return None
    item = {"id": str(ident), "kind": kind, "name": str(name)[:300], "artists": str(artists or "")[:300],
            "album": "", "artwork": kg._image(artwork), "durationMs": 0, "videoId": ""}
    if description:
        item["description"] = str(description)[:2000]
    if type(count) is int and count >= 0:
        item["trackCount"] = count
    return item


def _song(digest, mix_id, name, artists, album, artwork, duration, mv=None):
    ident = f"{str(digest or '').lower()}-{mix_id}"
    if not kg._record.fullmatch(ident) or not name:
        return None
    return kg._track(ident, name, artists, album, artwork, duration, mv)


def _album(row):
    ident = str(row.get("album_id") or row.get("albumid") or "")
    if _NUMBER.fullmatch(ident):
        return _resource(ident, "album", row.get("album_name") or row.get("albumname"),
                         row.get("sizable_cover") or row.get("img") or row.get("cover"),
                         artists=row.get("author_name") or row.get("singer"), description=row.get("intro"),
                         count=row.get("songcount"))


def _artist(row):
    ident = str(row.get("author_id") or row.get("AuthorId") or "")
    if _NUMBER.fullmatch(ident):
        return _resource(ident, "artist", row.get("author_name") or row.get("AuthorName"),
                         row.get("sizable_avatar") or row.get("Avatar"), description=row.get("intro"),
                         count=row.get("song_count") or row.get("AudioCount"))


def _playlist(row):
    ident = str(row.get("global_collection_id") or row.get("gid") or "")
    if len(ident) <= 80 and _COLLECTION.fullmatch(ident):
        return _resource(ident, "playlist", row.get("specialname") or row.get("name"),
                         row.get("flexible_cover") or row.get("img") or row.get("pic") or row.get("imgurl"),
                         description=row.get("intro"), count=row.get("song_count", row.get("count")))


def _lyric_hit(row):
    item = _song(row.get("FileHash"), row.get("MixSongID"), row.get("SongName"), row.get("SingerName"),
                 row.get("AlbumName"), row.get("Image"), row.get("TimeLength"), row.get("MvHash"))
    if item and isinstance(row.get("Lyric"), str):
        item["snippet"] = row["Lyric"][:1000]
    return item


def _video_hit(row):
    ident = kg.video_hash(row.get("MvHash"))
    if not ident:
        return None
    item = _resource(ident, "mv", row.get("MvName"), row.get("Pic"), artists=row.get("SingerName"),
                     description=row.get("Description"))
    if item:
        duration = row.get("Duration")
        item.update(videoId=ident, durationMs=kg._duration(duration * 1000) if type(duration) is int else 0)
    return item


def video(ident):
    if not kg.video_hash(ident) or ident != ident.lower():
        raise kg.Error(400, "invalid_input", "酷狗 MV ID 格式错误")
    rows = _read("/video/privilege", {"hash": ident}).get("data") or []
    row = next((row for row in rows if kg.video_hash(row.get("hash")) == ident), None)
    if not row or not row.get("name"):
        raise kg.Error(404, "not_found", "未找到该酷狗 MV")
    item = _resource(ident, "mv", row["name"], "", artists=row.get("singer_name"))
    item["videoId"] = ident
    return {"resource": item}


def _kinds(rows, normalize):
    return [item for row in rows if isinstance(row, dict) and (item := normalize(row))]


def search(query, kind, offset, limit):
    if kind == "song":
        return kg.search(query, offset, limit)
    upstream_kind = SEARCH_TYPES[kind]

    def fetch(page, size):
        data = _read("/search", {"keywords": query, "type": upstream_kind, "page": page, "pagesize": size}).get("data") or {}
        return data.get("lists", []), data.get("total")

    normalize = {"artist": _artist, "album": _album, "playlist": _playlist, "lyric": _lyric_hit, "mv": _video_hit}[kind]
    rows, next_offset = _page(fetch, offset, limit)
    return {"items": _kinds(rows, normalize), "nextOffset": next_offset}


def _page(fetch, offset, limit):
    # Upstream pages use fixed-size indexing. Keep the public offset independent of limit.
    size = 30
    index, skip = divmod(offset, size)
    rows = []
    more = False
    while len(rows) < limit:
        batch, total = fetch(index + 1, size)
        stop = min(len(batch), skip + limit - len(rows))
        rows.extend(batch[skip:stop])
        more = stop < len(batch) or (stop == size and (type(total) is not int or index * size + stop < total))
        if not more:
            break
        index += 1
        skip = 0
    return rows, offset + len(rows) if rows and more else None


def _audio(row):
    base, audio, album = row.get("base") or row, row.get("audio_info") or row, row.get("album_info") or {}
    return _song(audio.get("hash") or audio.get("hash_128"), base.get("album_audio_id") or base.get("mixsongid"),
                 base.get("songname") or base.get("audio_name"), base.get("author_name"),
                 album.get("album_name") or base.get("album_name"), album.get("sizable_cover") or album.get("cover")
                 or base.get("sizable_cover"), audio.get("duration") or audio.get("duration_128")
                 or audio.get("timelength") or audio.get("time_length"), base.get("video_hash") or base.get("mv_hash"))


def _collection_song(row):
    artists = " / ".join(str(a["name"]) for a in row.get("singerinfo", []) if a.get("name"))
    name = str(row.get("name") or "")
    if " - " in name:
        prefix, title = name.split(" - ", 1)
        if not artists or prefix == artists:
            artists, name = artists or prefix, title
    return _song(row.get("hash"), row.get("mixsongid"), name, artists,
                 (row.get("albuminfo") or {}).get("name"), row.get("cover"), row.get("timelen"), row.get("mvhash"))


def _chart(row):
    ident = f"rank_{row.get('rankid')}_{row.get('rank_cid') or 0}"
    if _RANK.fullmatch(ident):
        return _resource(ident, "playlist", row.get("rankname"),
                         row.get("img_cover") or row.get("imgurl"), description=row.get("intro"))


def details(kind, ident, offset, limit):
    albums = []
    valid_id = _NUMBER if kind in ("album", "artist") else _COLLECTION
    rank = _RANK.fullmatch(ident) if kind == "playlist" else None
    if not rank and not valid_id.fullmatch(ident):
        raise kg.Error(400, "invalid_input", "酷狗资源 ID 格式错误")
    if rank:
        rank_id, rank_cid = rank.groups()
        items = charts()["items"]
        resource = next((item for item in items if item["id"] == ident), None)
        path, params, key, normalize = "/rank/audio", {"rankid": rank_id, "rank_cid": rank_cid}, "songlist", _audio
    elif kind == "album":
        resource = next(iter(_kinds(_read("/album/detail", {"id": ident}).get("data", []), _album)), None)
        path, params, key, normalize = "/album/songs", {"id": ident}, "songs", _audio
    elif kind == "artist":
        resource = _artist(_read("/artist/detail", {"id": ident}).get("data") or {})
        path, params, key, normalize = "/artist/audios", {"id": ident, "sort": "hot"}, None, _audio
        if resource and offset == 0:
            albums = _kinds(_read("/artist/albums", {"id": ident, "pagesize": limit}).get("data", []), _album)
    else:
        rows = _read("/playlist/detail", {"ids": ident}).get("data") or []
        public_rows = (row for row in rows if isinstance(row, dict) and row.get("is_pri") in (0, "0"))
        resource = next(iter(_kinds(public_rows, _playlist)), None)
        path, params, key, normalize = "/playlist/track/all", {"id": ident}, "songs", _collection_song

    if not resource or resource["id"] != ident:
        raise kg.Error(404, "not_found", "未找到该酷狗资源")

    def fetch(page, size):
        response = _read(path, {**params, "page": page, "pagesize": size})
        data = response.get("data")
        if key is None:
            return data or [], response.get("total")
        data = data or {}
        return data.get(key, []), data.get("total", data.get("count", response.get("total")))

    rows, next_offset = _page(fetch, offset, limit)
    return {"resource": resource, "tracks": _kinds(rows, normalize), "albums": albums, "nextOffset": next_offset}


def _keywords(values):
    return {"items": list(dict.fromkeys(str(word)[:120] for word in values if isinstance(word, str) and word))[:20]}


def hot():
    tabs = (_read("/search/hot").get("data") or {}).get("list", [])
    return _keywords(row.get("keyword") for tab in tabs for row in tab.get("keywords", []))


def suggest(query):
    tabs = _read("/search/suggest", {"keywords": query}).get("data") or []
    return _keywords(row.get("HintInfo") for tab in tabs for row in tab.get("RecordDatas", []))


def charts():
    rows = (_read("/rank/list").get("data") or {}).get("info", [])
    return {"items": _kinds(rows, _chart), "nextOffset": None}


def recommendations():
    rows = (_read("/everyday/recommend").get("data") or {}).get("song_list", [])
    return {"items": _kinds(rows, _audio), "nextOffset": None}


def featured_playlists():
    rows = (_read("/top/playlist").get("data") or {}).get("special_list", [])
    return {"items": _kinds(rows, _playlist), "nextOffset": None}


def _krc_aux(raw, count):
    match = _LANGUAGE.search(raw.replace("\r", ""))
    if not match:
        return {}
    try:
        language = json.loads(b64decode(match[1], validate=True))
    except (ValueError, UnicodeError, Base64Error):
        return {}
    tracks = language.get("content") if isinstance(language, dict) else None
    if not isinstance(tracks, list):
        return {}
    aux = {}
    # KuGou type 1 carries translations. Type 0 aligns romanization tokens to the same timed line.
    for entry in tracks:
        if not isinstance(entry, dict):
            continue
        kind, rows = entry.get("type"), entry.get("lyricContent")
        if kind not in (0, 1) or not isinstance(rows, list) or len(rows) != count:
            continue
        field = "trans" if kind == 1 else "roma"
        values = []
        for row in rows:
            parts = [part for part in row if isinstance(part, str)] if isinstance(row, list) else []
            separator = " " if kind == 0 and all(part == part.strip() for part in parts) else ""
            values.append(separator.join(parts).strip())
        if any(values) and (field not in aux or entry.get("language") == 0):
            aux[field] = values
    return aux


def _krc_lines(raw):
    timed = [match for row in raw.splitlines() if (match := _LINE.fullmatch(row))]
    aux = _krc_aux(raw, len(timed))
    lines = []
    # The language vectors count every timed line, including blank lines that the UI skips.
    for index, match in enumerate(timed):
        start = int(match[1])
        words = [[start + int(offset), word] for offset, _, word in _WORD.findall(match[2])]
        if words:
            line = {"t": start, "txt": "".join(w[1] for w in words), "w": words, "trans": "", "roma": ""}
            for field, values in aux.items():
                line[field] = values[index]
            lines.append(line)
    return lines


def lyrics(ident):
    song, _, _ = kg.track(ident)
    digest, mix_id = ident.split("-")
    candidates = _read("/search/lyric", {"hash": digest, "album_audio_id": mix_id,
                       "duration": song["durationMs"]}).get("candidates") or []
    lines = []
    if candidates:
        candidate = candidates[0]
        parameters = {"id": candidate["id"], "accesskey": candidate["accesskey"], "decode": 1}
        raw = _read("/lyric", {**parameters, "fmt": "krc"}).get("decodeContent") or ""
        lines = _krc_lines(raw)
        if not lines:
            raw = _read("/lyric", {**parameters, "fmt": "lrc"}).get("decodeContent") or ""
            lines = [{"t": time, "txt": text, "trans": "", "roma": ""} for time, text in _parse_lrc(raw)]
    word_level = any("w" in row for row in lines)
    source = "kugou:krc" if word_level else "kugou:lrc" if lines else "none"
    return {"lines": lines, "source": source, "isYrc": False, "wordLevel": word_level}



def comments(ident, page, limit):
    kg.track(ident)
    _, mix_id = ident.split("-")
    payload = _read("/comment/music", {"mixsongid": mix_id, "page": page, "pagesize": limit})
    rows = []
    for row in payload.get("list") or []:
        try:
            date = datetime.fromisoformat(row.get("addtime") or "")
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone(timedelta(hours=8)))
            timestamp = int(date.timestamp() * 1000)
        except ValueError:
            timestamp = 0
        rows.append({"id": str(row.get("id") or ""), "userId": row.get("user_id") or 0,
                     "nickname": str(row.get("user_name") or ""), "avatar": kg._image(row.get("user_pic")),
                     "content": str(row.get("content") or ""), "time": timestamp,
                     "liked": (row.get("like") or {}).get("count") or 0, "beNickname": "",
                     "beContent": str(row.get("pcontent") or ""), "replyCount": row.get("reply_num") or 0})
    return {"comments": rows, "total": payload.get("count") or 0,
            "more": bool(rows and page < min(payload.get("maxPage") or 0, 500))}


def radio_stations():
    result = _read("/fm/class").get("data") or {}
    stations = {}
    for group in result.get("class_list") or []:
        for row in group.get("fmlist") or []:
            station_id = str(row.get("fmid") or "")
            station_type = row.get("fmtype")
            name = str(row.get("fmname") or "").strip()
            if not _NUMBER.fullmatch(station_id) or type(station_type) is not int or not 0 < station_type <= 99 or not name:
                continue
            ident = f"{station_id}_{station_type}"
            stations.setdefault(ident, {"id": ident, "name": name[:120], "category": str(group.get("classname") or "")[:120]})
    return {"items": list(stations.values())}


def _station_track(row):
    name = str(row.get("name") or "")
    artists, separator, title = name.partition(" - ")
    return _song(row.get("hash"), row.get("album_audio_id"), title if separator else name,
                 artists if separator else "", row.get("albumname"), row.get("cover"),
                 row.get("time"), row.get("mvhash"))


def radio(limit, offset, seeds, excluded, station=""):
    if station:
        station_id, station_type = station.split("_")
        query = {"fmid": station_id, "type": station_type, "size": limit, "offset": offset, "userid": 0}
        reply = next(iter(_read("/fm/songs", query).get("data") or []), {})
        items = _kinds(reply.get("songs") or [], _station_track)
        cursor = reply.get("offset")
        next_offset = cursor if type(cursor) is int and offset < cursor <= 10000 and items else None
    else:
        # Use recent recordings as seeds on the initial batch. Subsequent pages widen the selection.
        items = []
        if offset == 0:
            data = _read("/ai/recommend", {"album_audio_id": ",".join(seeds)}).get("data") or {}
            items = _kinds(data.get("song_list") or [], _audio)
        if len([row for row in items if row["id"] not in excluded]) < limit:
            data = _read("/ai/recommend/song").get("data") or {}
            items.extend(_kinds(data.get("list") or [], _audio))
        next_offset = offset + 1 if offset < 10000 else None
    unique = []
    seen = set(excluded)
    for item in items:
        if item["id"] not in seen:
            seen.add(item["id"])
            unique.append(item)
    return {"items": unique[:limit], "nextOffset": next_offset if unique else None}


def track_insights(ident):
    resource, meta, audio = kg.track(ident)
    qualities = []
    for level, label, suffix in [('standard', '标准', ''), ('exhigh', '高品质', '_320'), ('lossless', '无损', '_flac')]:
        if kg.video_hash(audio.get('hash' + suffix)):
            bitrate = audio.get('bitrate' + suffix) or 0
            qualities.append({'level': level, 'label': label, 'br': bitrate * 1000 if 0 < bitrate < 5000 else bitrate,
                              'size': audio.get('filesize' + suffix) or 0})
    return {'resource': resource, 'qualities': qualities,
            'albumId': str(meta.get('album_id') or ''), 'language': meta.get('language') or '',
            'publishDate': meta.get('publish_date') or '', 'version': meta.get('version') or ''}


def playlist_categories():
    rows = _read('/playlist/tags').get('data') or []
    return {'items': [{'id': str(tag['tag_id']), 'name': tag['tag_name'], 'group': group['tag_name']}
                      for group in rows for tag in group.get('son', []) if tag.get('tag_id') and tag.get('tag_name')]}


def playlist_directory(category, offset, limit):
    if category and not _NUMBER.fullmatch(category):
        raise kg.Error(400, 'invalid_input', '无效的歌单分类')
    params = {'category_id': category or '0'}
    # This upstream returns fixed pages of 35 entries even when pagesize is supplied.
    size = 35
    cursor, skip = divmod(offset, size)
    items = []
    next_offset = None
    consumed = 0
    while consumed < limit:
        data = _read('/top/playlist', {**params, 'page': cursor + 1, 'pagesize': size}).get('data') or {}
        batch = data.get('special_list') or []
        candidates = batch[skip:skip + limit - consumed]
        items.extend(_kinds(candidates, _playlist))
        consumed += len(candidates)
        if skip + len(candidates) >= len(batch) and (len(batch) != size or not data.get('has_next')):
            break
        next_offset = offset + consumed
        if not candidates or skip + len(candidates) < len(batch):
            break
        cursor, skip = cursor + 1, 0
    return {'items': items, 'nextOffset': next_offset if consumed == limit else None}


def artist_directory(group, offset, limit):
    data = _read('/artist/lists', {'hotsize': 30}).get('data') or {}
    groups = data.get('info') or []
    rows = next((item.get('singer') or [] for item in groups if item.get('title') == (group or '热门')), [])
    items = _kinds([{'author_id': row.get('singerid'), 'author_name': row.get('singername'),
                    'sizable_avatar': row.get('imgurl'), 'song_count': row.get('songcount')}
                   for row in rows[offset:offset + limit]], _artist)
    return {'items': items, 'groups': [item['title'] for item in groups if item.get('title')],
            'nextOffset': offset + limit if offset + limit < len(rows) else None}


def new_songs(limit):
    rows = _read('/top/song', {'pagesize': limit}).get('data') or []
    return {'items': _kinds(rows, _audio), 'nextOffset': None}



def _spoken_show(row):
    ident = str(row.get('album_id') or '')
    if _NUMBER.fullmatch(ident):
        return _resource(ident, 'podcast', row.get('album_name'), row.get('sizable_cover'), artists=row.get('author_name'),
                         description=row.get('intro'), count=row.get('audio_total'))


def spoken_shows(limit):
    data = _read('/longaudio/daily/recommend').get('data') or {}
    return {'items': _kinds(data.get('albums', []), _spoken_show)[:limit], 'nextOffset': None}


def spoken_show(ident, offset, limit):
    if not _NUMBER.fullmatch(ident):
        raise kg.Error(400, 'invalid_input', '无效的有声节目专辑 ID')
    rows = _read('/longaudio/album/detail', {'album_id': ident}).get('data') or []
    resource = next((item for item in _kinds(rows, _spoken_show) if item['id'] == ident), None)
    if not resource:
        raise kg.Error(404, 'not_found', '未找到该酷狗有声节目')

    def fetch(page, size):
        response = _read('/longaudio/album/audios', {'album_id': ident, 'page': page, 'pagesize': size})
        return response.get('data') or [], response.get('total')

    tracks, next_offset = _page(fetch, offset, limit)
    return {'resource': resource, 'tracks': _kinds(tracks, _audio), 'nextOffset': next_offset}
