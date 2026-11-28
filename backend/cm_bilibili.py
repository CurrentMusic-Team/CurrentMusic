"""Bilibili catalogue, song links and scoped media through the local TZFC adapter."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from contextlib import closing
from dataclasses import dataclass
import ipaddress
import json
import os
import re
import secrets
import socket
import threading
import time
import unicodedata
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from xml.etree import ElementTree as XML

import cm_db
import cm_kugou


SCHEMA = '''
CREATE TABLE IF NOT EXISTS bilibili_settings (
    id INTEGER PRIMARY KEY CHECK(id = 1), auto_match INTEGER NOT NULL CHECK(auto_match IN (0, 1))
);
CREATE TABLE IF NOT EXISTS bilibili_links (
    provider TEXT NOT NULL, track_id TEXT NOT NULL, mode TEXT NOT NULL,
    video_id TEXT NOT NULL, PRIMARY KEY(provider, track_id)
);
CREATE TABLE IF NOT EXISTS bilibili_audit (
    id INTEGER PRIMARY KEY, actor_id INTEGER NOT NULL, at INTEGER NOT NULL,
    action TEXT NOT NULL, details TEXT NOT NULL
);
'''
OPERATIONS = ('catalog.search', 'catalog.track', 'catalog.video', 'playback.qualities', 'playback.resolve', 'playback.mv')
BV = r'BV[0-9a-zA-Z]{10}'
ID = re.compile(rf'({BV}):([1-9][0-9]{{0,17}})')
TRACK_IDS = {'netease': re.compile(r'[1-9][0-9]{0,17}'), 'kugou': re.compile(r'[a-f0-9]{32}-[1-9][0-9]{0,14}')}
CODEC = re.compile(r'[a-zA-Z0-9.-]{1,64}')
BYTES = re.compile(r'bytes=(?:[0-9]{1,12}-[0-9]{0,12}|-[0-9]{1,12})')
CONTENT_RANGE = re.compile(r'bytes (?:[0-9]{1,12}-[0-9]{1,12}|\*)/[0-9]{1,12}')
SEGMENT_RANGE = re.compile(r'[0-9]{1,10}-[0-9]{1,10}')


class Error(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code = status, code


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args):
        return None


_opener = build_opener(ProxyHandler({}), _NoRedirect())
_catalog_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix='bilibili-catalog')
_match_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix='bilibili-match')
_match_lock = threading.Lock()
_pending = {}


def base():
    address = os.environ.get('CM_BILIBILI_BASE', '').rstrip('/')
    if re.fullmatch(r'http://127\.0\.0\.1:[1-9][0-9]{0,4}', address) and int(address.rsplit(':', 1)[1]) < 65536:
        return address
    return ''


def playback_enabled():
    return bool(base() and os.environ.get('CM_BILIBILI_PLAYBACK') == '1')


def authorized(uid):
    account = cm_db.get_user(uid) if uid else None
    return cm_kugou.authorized_user(account)


def _call(path, params=None):
    if not base():
        raise Error(503, 'bilibili_unavailable', '哔哩哔哩服务暂未启用')
    url = base() + path + ('?' + urlencode(params) if params else '')
    try:
        try:
            response = _opener.open(Request(url, headers={'Accept': 'application/json'}), timeout=8)
        except HTTPError as error:
            response = error
        with response:
            status = response.status
            result = json.loads(response.read(2 * 1024 * 1024))
        if status == 200 and isinstance(result, dict):
            return result
        if status == 404:
            raise Error(404, 'not_found', '未找到对应的哔哩哔哩视频')
        if status == 503:
            code = result.get('code') if isinstance(result, dict) else None
            if code == 'bilibili_session_unavailable':
                raise Error(503, code, '哔哩哔哩服务端会话需要重新扫码')
            raise Error(503, 'bilibili_busy', '哔哩哔哩服务暂时繁忙')
    except (OSError, ValueError):
        pass
    raise Error(502, 'bilibili_upstream_error', '哔哩哔哩上游暂时不可用')


def _id(value):
    match = ID.fullmatch(value) if isinstance(value, str) else None
    if not match:
        raise Error(400, 'invalid_input', '需要 BV号:CID 格式的视频分 P 标识')
    return match[1], int(match[2])


def _reference(data):
    provider, ident = data.get('provider'), data.get('id')
    if isinstance(provider, str) and provider in TRACK_IDS and isinstance(ident, str) and TRACK_IDS[provider].fullmatch(ident):
        return provider, ident
    raise Error(400, 'invalid_input', '需要网易云或酷狗的原始歌曲 ID')


def _details(bvid):
    key = f'bili-info:{bvid}'
    cached = cm_db.cache_get(key)
    if cached:
        return json.loads(cached)
    result = _call('/details', {'bvid': bvid})
    cm_db.cache_set(key, json.dumps(result, ensure_ascii=False), 900)
    return result


def _image(value):
    try:
        url = urlsplit(value)
        if url.scheme in ('http', 'https') and url.hostname and url.hostname.endswith('.hdslb.com') and url.port is None and not url.username:
            return url._replace(scheme='https', query='', fragment='').geturl()
    except (TypeError, ValueError, AttributeError):
        pass
    return ''


def _resource(info, part, kind='song'):
    bvid = info['bvid']
    ident = f'{bvid}:{part["cid"]}'
    part_title = str(part.get('part') or '')[:300]
    title = str(info.get('title') or part_title)[:300]
    if len(info['parts']) > 1 and part_title and part_title != title:
        title += ' · ' + part_title
    page = int(part.get('page') or 1)
    return {'id': ident, 'kind': kind, 'provider': 'bilibili', 'name': title, 'partTitle': part_title,
            'uploader': str(info.get('uploader') or '')[:100], 'durationMs': int(part.get('duration') or 0) * 1000,
            'artwork': _image(info.get('artwork')), 'videoId': ident,
            'originalUrl': f'https://www.bilibili.com/video/{bvid}/?p={page}', 'page': page}


def detail(ident, kind='song'):
    bvid, cid = _id(ident)
    info = _details(bvid)
    parts = [_resource(info, part, kind) for part in info.get('parts') or []]
    selected = next((row for row in parts if row['id'] == f'{bvid}:{cid}'), None)
    if not selected:
        raise Error(404, 'not_found', '未找到对应的哔哩哔哩视频分 P')
    return {'resource': selected, 'parts': parts}


def _search_entries(keyword, page=1):
    response = _call('/search', {'query': keyword, 'page': page})
    bvids = list(dict.fromkeys(row['bvid'] for row in response.get('items') or []))
    return bvids, bool(response.get('more'))


def _find_bv(keyword):
    if re.fullmatch(BV, keyword):
        return keyword, 0
    matched = re.fullmatch(rf'https?://(?:www\.)?bilibili\.com/video/({BV})/?(?:\?[^\s#]*)?(?:#[^\s]*)?', keyword)
    if matched:
        page = (parse_qs(urlsplit(keyword).query).get('p') or ['0'])[0]
        return matched[1], int(page) if re.fullmatch(r'[0-9]{1,6}', page) else 0
    return '', 0


def search(data):
    keyword, kind = data.get('query'), data.get('kind', 'song')
    offset, limit = data.get('offset', 0), data.get('limit', 20)
    if (not isinstance(keyword, str) or not 1 <= len(keyword.strip()) <= 100 or kind not in ('song', 'mv')
            or type(offset) is not int or not 0 <= offset < 2000 or type(limit) is not int or not 1 <= limit <= 50):
        raise Error(400, 'invalid_input', '需要关键词和有效的分页参数')
    keyword = keyword.strip()
    bvid, part_number = _find_bv(keyword)
    resource_kind = 'video' if kind == 'mv' else 'song'
    if bvid:
        info = _details(bvid)
        parts = [_resource(info, part, resource_kind) for part in info.get('parts') or []
                 if not part_number or part.get('page') == part_number]
        return {'items': parts[offset:offset + limit], 'nextOffset': offset + limit if offset + limit < len(parts) else None}
    page, skip = divmod(offset, 20)
    page += 1
    targets = []
    next_offset = offset
    while len(targets) < limit:
        entries, more = _search_entries(keyword, page)
        selected = entries[skip:skip + limit - len(targets)]
        targets.extend(selected)
        consumed = skip + len(selected)
        next_offset = 20 * (page - 1) + consumed
        if consumed < len(entries) or not more or page == 100 or len(targets) == limit:
            break
        page += 1
        skip = 0
    # Search only exposes the default part. Details expand into the verified CIDs.
    def first(ident):
        try:
            info = _details(ident)
            parts = info.get('parts') or []
            return _resource(info, parts[0], resource_kind) if parts else None
        except Error:
            return None

    rows = [row for row in _catalog_pool.map(first, targets) if row]
    if consumed >= len(entries) and more:
        next_offset = 20 * page
    return {'items': rows, 'nextOffset': next_offset if targets and next_offset < 2000 and (more or consumed < len(entries)) else None}


def _upstream_media(url):
    try:
        split = urlsplit(url)
        host = split.hostname or ''
        return (split.scheme == 'https' and not (split.username or split.password or split.port or split.fragment)
                and (host.endswith(('.bilivideo.com', '.bilivideo.cn')) or host == 'upos-hz-mirrorakam.akamaized.net'))
    except (AttributeError, TypeError, ValueError):
        return False


def _streams(ident):
    bvid, cid = _id(ident)
    result = _call('/streams', {'bvid': bvid, 'cid': cid})
    if result.get('is_preview') or result.get('isPreview'):
        raise Error(403, 'playback_unavailable', '当前仅有视频试听片段')
    return result.get('dash') or {}


def _track_url(track):
    if not isinstance(track, dict) or not CODEC.fullmatch(str(track.get('codecs') or '')):
        return ''
    backups = track.get('backupUrl') or track.get('backup_url') or []
    for value in [track.get('baseUrl') or track.get('base_url'), *backups]:
        if _upstream_media(value):
            return value
    return ''


def _dolby(dash):
    info = dash.get('dolby') or {}
    if info.get('type') != 2:
        return None
    return next((track for track in info.get('audio') or []
                 if track.get('id') == 30250 and track.get('codecs') == 'ec-3' and _track_url(track)), None)


def _standard(dash):
    return max((track for track in dash.get('audio') or [] if str(track.get('codecs')).startswith('mp4a.')
                and _track_url(track)), key=lambda track: int(track.get('bandwidth') or 0), default=None)


def _videos(dash, dolby):
    return [track for track in dash.get('video') or [] if (track.get('id') == 126 if dolby else
            track.get('id') != 126 and str(track.get('codecs')).startswith('avc1.')) and _track_url(track)]


def _link_config(provider=None, ident=None):
    with closing(cm_db.connect()) as db:
        setting = db.execute('SELECT auto_match FROM bilibili_settings WHERE id=1').fetchone()
        link = db.execute('SELECT mode,video_id FROM bilibili_links WHERE provider=? AND track_id=?', (provider, ident)).fetchone()
    return bool(setting is None or setting[0]), tuple(link) if link else None


def _title(value):
    return ''.join(char for char in unicodedata.normalize('NFKC', value).casefold() if char.isalnum())


def _candidates(name, duration):
    if not name or duration <= 0:
        return []
    title = _title(name)
    if not title:
        return []
    def search_ids(query):
        try:
            return _search_entries(query)[0]
        except Error:
            return None

    targeted, general = _catalog_pool.map(search_ids, (name[:96] + ' 杜比', name[:100]))
    if targeted is None and general is None:
        raise Error(502, 'bilibili_upstream_error', '哔哩哔哩搜索暂不可用')
    bvids = list(dict.fromkeys((targeted or [])[:6] + (general or [])[:8]))[:10]

    def inspect(bvid):
        try:
            return _details(bvid)
        except Error as error:
            if error.status == 404:
                return None
            raise

    results = []
    for info in _catalog_pool.map(inspect, bvids):
        if not info:
            continue
        for part in (info.get('parts') or [])[:12]:
            candidate_titles = (_title(str(part.get('part') or '')), _title(str(info.get('title') or '')))
            difference = abs(int(part.get('duration') or 0) * 1000 - duration)
            title_match = title in candidate_titles or (len(title) >= 2 and any(title in item for item in candidate_titles))
            if part.get('duration') and difference <= 5000 and title_match:
                results.append((title not in candidate_titles, difference, _resource(info, part)))
    ranked = sorted(results, key=lambda row: (row[0], row[1], row[2]['id']))

    def classify(result):
        approximate, difference, row = result
        try:
            dash = _streams(row['id'])
        except Error as error:
            if error.status in (403, 404):
                return None
            raise
        row.update(differenceMs=difference, atmos=bool(_dolby(dash)), vision=bool(_videos(dash, True)))
        return not row['atmos'], not row['vision'], approximate, difference, row['id'], row

    output = [entry for entry in _catalog_pool.map(classify, ranked[:6]) if entry]
    return [entry[-1] for entry in sorted(output)]


def _native_track(provider, ident):
    if provider == 'netease':
        import cm_ncm
        return next(iter(cm_ncm.song_detail([ident])), {})
    return cm_kugou.track(ident)[0]


def _match_title(provider, ident, track=None):
    row = track or _native_track(provider, ident)
    return row.get('name') or '', int(row.get('durationMs') or row.get('duration') or 0)


def _match_key(provider, ident):
    return f'bili-link:v1:{provider}:{ident}'


def _finish_match(key, job):
    with _match_lock:
        if _pending.get(key) is job:
            del _pending[key]


def linked(provider, ident, track=None):
    if not playback_enabled():
        return ''
    ident = str(ident)
    enabled, override = _link_config(provider, ident)
    if override:
        return override[1] if override[0] == 'manual' else ''
    if not enabled:
        return ''
    key = _match_key(provider, ident)
    cached = cm_db.cache_get(key)
    if cached is not None:
        return json.loads(cached)

    def match():
        try:
            rows = _candidates(*_match_title(provider, ident, track))
            result = next((row['id'] for row in rows if row['atmos'] or row['vision']), '')
            cm_db.cache_set(key, json.dumps(result), 3600 if result else 600)
            return result
        except Exception:
            return ''

    with _match_lock:
        job = _pending.get(key)
        if not job:
            if len(_pending) >= 3:
                return ''
            job = _pending[key] = _match_pool.submit(match)
            created = True
        else:
            created = False
    if created:
        job.add_done_callback(lambda future: _finish_match(key, future))
    try:
        result = job.result(2.5)
    except TimeoutError:
        return ''
    enabled, override = _link_config(provider, ident)
    return (override[1] if override[0] == 'manual' else '') if override else result if enabled else ''


def _audit(db, uid, action, details):
    db.execute('INSERT INTO bilibili_audit(actor_id,at,action,details) VALUES(?,?,?,?)',
               (uid, int(time.time()), action, json.dumps(details, ensure_ascii=False)))


def admin(action, data, uid):
    if action == 'status':
        enabled, _ = _link_config()
        session = False
        if playback_enabled():
            try:
                session = bool(_call('/session').get('activeVip'))
            except Error:
                pass
        return {'autoMatch': enabled, 'configured': bool(base()), 'activeVip': session}
    if not isinstance(data, dict):
        raise Error(400, 'invalid_input', '需要提交对象')
    if action == 'settings':
        enabled = data.get('autoMatch')
        if type(enabled) is not bool:
            raise Error(400, 'invalid_input', '需要指定是否自动匹配')
        with closing(cm_db.connect()) as db, db:
            db.execute('INSERT INTO bilibili_settings VALUES(1,?) ON CONFLICT(id) DO UPDATE SET auto_match=excluded.auto_match', (enabled,))
            _audit(db, uid, 'settings', {'autoMatch': enabled})
        return {'autoMatch': enabled}
    provider, ident = _reference(data)
    if action == 'candidates':
        name, duration = _match_title(provider, ident)
        if not name:
            raise Error(404, 'not_found', '未找到指定歌曲')
        _, override = _link_config(provider, ident)
        cached = cm_db.cache_get(_match_key(provider, ident))
        selected = override[1] if override else json.loads(cached) if cached else ''
        return {'name': name, 'durationMs': duration, 'mode': override[0] if override else 'auto',
                'selectedId': selected, 'items': _candidates(name, duration)}
    if action == 'link':
        mode = data.get('mode')
        if mode not in ('manual', 'blocked', 'auto'):
            raise Error(400, 'invalid_input', '无效的关联方式')
        video_id = data.get('videoId', '') if mode == 'manual' else ''
        if mode == 'manual':
            detail(video_id)
            if not _native_track(provider, ident):
                raise Error(404, 'not_found', '未找到指定歌曲')
        with closing(cm_db.connect()) as db, db:
            if mode == 'auto':
                db.execute('DELETE FROM bilibili_links WHERE provider=? AND track_id=?', (provider, ident))
            else:
                db.execute('''INSERT INTO bilibili_links VALUES(?,?,?,?) ON CONFLICT(provider,track_id)
                              DO UPDATE SET mode=excluded.mode,video_id=excluded.video_id''', (provider, ident, mode, video_id))
            _audit(db, uid, 'link', {'provider': provider, 'id': ident, 'mode': mode, 'videoId': video_id})
        cm_db.cache_delete(_match_key(provider, ident))
        return {'mode': mode, 'selectedId': video_id}
    raise Error(404, 'unsupported_operation', '不支持的管理操作')


@dataclass
class Ticket:
    uid: int
    expires: float
    mime: str
    url: str = ''
    manifest: bytes = b''


_tickets = {}
_ticket_lock = threading.Lock()
_media_slots = threading.BoundedSemaphore(48)


def _ttl(urls):
    ttl = 480
    for url in urls:
        parts = parse_qs(urlsplit(url).query)
        for key in ('deadline', 'expires', 'expire'):
            expiry = (parts.get(key) or [''])[0]
            if re.fullmatch(r'[0-9]{10}', expiry):
                ttl = min(ttl, int(expiry) - int(time.time()) - 10)
    if ttl <= 10:
        raise Error(502, 'bilibili_upstream_error', '上游媒体地址即将到期，请重试')
    return ttl


def _publish(tickets):
    now = time.monotonic()
    with _ticket_lock:
        for key in [key for key, row in _tickets.items() if row.expires <= now]:
            del _tickets[key]
        if len(_tickets) + len(tickets) > 2048:
            raise Error(503, 'bilibili_busy', '哔哩哔哩播放暂时繁忙')
        _tickets.update(tickets)


def _origin(origin):
    address = urlsplit(origin)
    if address.scheme != 'https' or not address.hostname or any((address.username, address.password, address.path, address.query, address.fragment)):
        raise Error(503, 'https_required', '哔哩哔哩播放需要 HTTPS 服务地址')
    return origin + '/cm/bilibili/media/'


def _address_ok(url):
    if not _upstream_media(url):
        return False
    try:
        entries = socket.getaddrinfo(urlsplit(url).hostname, 443, type=socket.SOCK_STREAM)
        return bool(entries) and all(ipaddress.ip_address(entry[4][0]).is_global for entry in entries)
    except (OSError, ValueError):
        return False


def _open_media(url, byte_range='', head=False):
    headers = {'Referer': 'https://www.bilibili.com/', 'User-Agent': 'Mozilla/5.0', 'Accept-Encoding': 'identity'}
    if byte_range:
        headers['Range'] = byte_range
    for _ in range(3):
        if not _address_ok(url):
            break
        try:
            return _opener.open(Request(url, headers=headers, method='HEAD' if head else 'GET'), timeout=8)
        except HTTPError as error:
            if error.code == 416:
                return error
            with error:
                if error.code not in (301, 302, 303, 307, 308) or not error.headers.get('Location'):
                    break
                url = urljoin(url, error.headers['Location'])
    raise Error(502, 'bilibili_upstream_error', '哔哩哔哩媒体暂不可用')


def _verify_audio(url):
    try:
        with _open_media(url, 'bytes=0-15') as response:
            return (response.status == 206 and response.headers.get('Content-Range', '').startswith('bytes 0-15/')
                    and response.read(16)[4:8] == b'ftyp')
    except (Error, OSError):
        return False


def resolve_audio(ident, quality, supports_dolby, uid, origin, *, dash=None):
    prefix = _origin(origin)
    dash = _streams(ident) if dash is None else dash
    variants = [(name, track) for name, track in (('dolby', _dolby(dash)), ('standard', _standard(dash))) if track]
    for name, track in variants:
        if (quality != 'auto' and quality != name) or (quality == 'auto' and name == 'dolby' and not supports_dolby):
            continue
        url = _track_url(track)
        if not _verify_audio(url):
            continue
        ttl = _ttl([url])
        token = secrets.token_urlsafe(24)
        _publish({token: Ticket(uid, time.monotonic() + ttl, 'audio/mp4', url=url)})
        return {'url': prefix + token, 'format': 'm4a', 'quality': name, 'provider': 'bilibili', 'trackId': track['id'],
                'codecs': track['codecs'], 'bitrate': int(track.get('bandwidth') or 0), 'expiresIn': ttl,
                'sourceId': ident, 'sourceTitle': detail(ident)['resource']['name']}
    raise Error(403, 'playback_unavailable', '当前没有可用的哔哩哔哩音轨')


def linked_dolby(provider, ident, uid, origin):
    if not playback_enabled() or not authorized(uid):
        return None
    try:
        match = linked(provider, ident)
        return resolve_audio(match, 'dolby', True, uid, origin) if match else None
    except (Error, OSError):
        return None


def _segment(track):
    source = track.get('segment_base') or track.get('SegmentBase') or track.get('segmentBase') or {}
    index = source.get('index_range') or source.get('indexRange') or ''
    init = source.get('initialization') or source.get('Initialization') or ''
    if SEGMENT_RANGE.fullmatch(str(index)) and SEGMENT_RANGE.fullmatch(str(init)):
        return index, init
    return None


def _manifest(ident, audio, videos, duration, uid, origin):
    prefix = _origin(origin)
    tracks = (audio, *videos)
    ttl = _ttl(_track_url(track) for track in tracks)
    expires = time.monotonic() + ttl
    tickets = {}
    root = XML.Element('MPD', {'xmlns': 'urn:mpeg:dash:schema:mpd:2011', 'type': 'static',
                               'profiles': 'urn:mpeg:dash:profile:isoff-on-demand:2011',
                               'mediaPresentationDuration': f'PT{duration:.3f}S', 'minBufferTime': 'PT1.5S'})
    period = XML.SubElement(root, 'Period', {'id': '0', 'start': 'PT0S'})
    for kind, group in (('audio', (audio,)), ('video', videos)):
        mime = f'{kind}/mp4'
        representations = XML.SubElement(period, 'AdaptationSet', {'mimeType': mime, 'contentType': kind, 'segmentAlignment': 'true'})
        for track in group:
            attrs = {'id': str(track['id']), 'codecs': track['codecs'], 'bandwidth': str(max(1, int(track.get('bandwidth') or 0)))}
            if kind == 'video':
                attrs.update(width=str(int(track.get('width') or 0)), height=str(int(track.get('height') or 0)))
            representation = XML.SubElement(representations, 'Representation', attrs)
            token = secrets.token_urlsafe(24)
            tickets[token] = Ticket(uid, expires, mime, url=_track_url(track))
            XML.SubElement(representation, 'BaseURL').text = prefix + token
            index, init = _segment(track)
            segment = XML.SubElement(representation, 'SegmentBase', {'indexRange': index})
            XML.SubElement(segment, 'Initialization', {'range': init})
    token = secrets.token_urlsafe(24)
    tickets[token] = Ticket(uid, expires, 'application/dash+xml', manifest=XML.tostring(root, encoding='utf-8', xml_declaration=True))
    _publish(tickets)
    return {'url': prefix + token, 'format': 'dash', 'expiresIn': ttl, 'id': ident}


def resolve_video(ident, data, uid, origin, resource=None):
    selected = resource if resource is not None else detail(ident, 'video')['resource']
    dash = _streams(ident)
    duration = float(dash.get('duration') or selected['durationMs'] / 1000)
    standard = _standard(dash)
    audio = _dolby(dash) if data.get('supportsDolby') is True else None
    if not audio or not _segment(audio):
        audio = standard
    wanted_vision = data.get('supportsDolbyVision', True) is True
    vision = next((track for track in _videos(dash, True) if _segment(track)), None) if wanted_vision else None
    ordinary = {}
    for track in _videos(dash, False):
        if _segment(track):
            ordinary.setdefault(track['id'], track)
    choices = [vision] if vision else list(ordinary.values())
    if not choices or not audio or not _segment(audio) or not 0 < duration <= 86400:
        raise Error(403, 'playback_unavailable', '当前没有可用的哔哩哔哩 MV')
    output = _manifest(ident, audio, choices, duration, uid, origin)
    return dict(output, originalUrl=selected['originalUrl'], page=selected['page'], dolbyVision=bool(vision),
                audioCodec=audio['codecs'], videoCodecs=list(dict.fromkeys(track['codecs'] for track in choices)))


def operation(name, data, uid, origin):
    if name not in OPERATIONS or not base() or (name.startswith('playback.') and not playback_enabled()):
        raise Error(404, 'unsupported_operation', '该服务未开放此哔哩哔哩能力')
    if not isinstance(data, dict):
        raise Error(400, 'invalid_input', '需要提交对象')
    if name == 'catalog.search':
        return search(data)
    if name.startswith('playback.') and not authorized(uid):
        raise Error(403, 'account_access_restricted', '当前账号没有播放权限')
    reference = data.get('linkedTrack')
    if name == 'playback.mv' and isinstance(reference, dict):
        provider, track_id = _reference(reference)
        ident = linked(provider, track_id)
        if not ident:
            raise Error(404, 'not_found', '该歌曲没有关联的哔哩哔哩 MV')
    else:
        ident = data.get('id')
    if name in ('catalog.track', 'catalog.video'):
        return detail(ident, 'song' if name == 'catalog.track' else 'video')
    if name == 'playback.mv':
        return resolve_video(ident, data, uid, origin)
    resource = detail(ident)['resource']
    dash = _streams(ident)
    if name == 'playback.qualities':
        return {'levels': [name for name, track in (('dolby', _dolby(dash)), ('standard', _standard(dash))) if track]}
    quality = data.get('quality', 'auto')
    if quality not in ('auto', 'dolby', 'standard'):
        raise Error(400, 'invalid_input', '不支持该音质档位')
    return resolve_audio(resource['id'], quality, data.get('supportsDolby') is True, uid, origin, dash=dash)


def _media_response(handler, status, mime, length, content_range=''):
    handler.send_response(status)
    handler.send_header('Content-Type', mime)
    handler.send_header('Content-Length', str(length))
    handler.send_header('Accept-Ranges', 'bytes')
    if content_range:
        handler.send_header('Content-Range', content_range)
    handler.send_header('Access-Control-Allow-Origin', '*')
    handler.send_header('Access-Control-Expose-Headers', 'Accept-Ranges, Content-Length, Content-Range')
    handler.send_header('Referrer-Policy', 'no-referrer')
    handler.send_header('Cache-Control', 'no-store')
    handler.end_headers()


def stream(handler, token):
    with _ticket_lock:
        ticket = _tickets.get(token)
    if not ticket or ticket.expires <= time.monotonic():
        raise Error(410, 'bilibili_media_expired', '播放地址已过期，请重新取址')
    if not playback_enabled() or not authorized(ticket.uid):
        raise Error(403, 'account_access_restricted', '当前账号没有播放权限')
    head = getattr(handler, 'head_only', False)
    byte_range = handler.headers.get('Range', '')
    if byte_range and not BYTES.fullmatch(byte_range):
        raise Error(416, 'invalid_range', '仅支持单段字节范围')
    if ticket.manifest:
        _media_response(handler, 200, ticket.mime, len(ticket.manifest))
        if not head:
            handler.wfile.write(ticket.manifest)
        return
    if not _media_slots.acquire(blocking=False):
        raise Error(503, 'bilibili_busy', '哔哩哔哩播放暂时繁忙')
    try:
        # Bilibili CDN origins may reject HEAD. A bounded GET yields the same range metadata.
        probe = 'bytes=0-0' if head and not byte_range else byte_range
        with _open_media(ticket.url, probe) as response:
            status = response.status
            length = response.headers.get('Content-Length', '')
            content_range = response.headers.get('Content-Range', '')
            limit = 128 * 1024**2 if ticket.mime == 'audio/mp4' else 1024**3
            ranged = CONTENT_RANGE.fullmatch(content_range)
            if (status not in (200, 206, 416) or (status != 416 and not re.fullmatch(r'[0-9]{1,12}', length))
                    or (status in (206, 416) and (not ranged or int(content_range.rsplit('/', 1)[1]) > limit))
                    or (status != 416 and int(length) > limit) or (byte_range and status == 200)):
                raise Error(502, 'bilibili_upstream_error', '哔哩哔哩媒体暂不可用')
            remaining = int(length) if status != 416 else 0
            if head and not byte_range and status == 206:
                _media_response(handler, 200, ticket.mime, int(content_range.rsplit('/', 1)[1]))
                return
            _media_response(handler, status, ticket.mime, remaining, content_range if ranged else '')
            if head:
                return
            try:
                while remaining:
                    chunk = response.read(min(65536, remaining))
                    if not chunk:
                        handler.close_connection = True
                        break
                    handler.wfile.write(chunk)
                    remaining -= len(chunk)
            except OSError:
                handler.close_connection = True
    except OSError:
        raise Error(502, 'bilibili_upstream_error', '哔哩哔哩媒体暂不可用') from None
    finally:
        _media_slots.release()
