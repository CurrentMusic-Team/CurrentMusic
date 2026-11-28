"""Loopback HTTP adapter for the pinned TZFC Bilibili SDK."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from html import unescape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import threading
from urllib.parse import parse_qs, urlsplit

from bilibili_api import Credential, search, user, video
from bilibili_api.exceptions import ResponseCodeException

BV = re.compile(r'BV[0-9A-Za-z]{10}')
SDK = ThreadPoolExecutor(max_workers=12)
SLOTS = threading.BoundedSemaphore(24)
LOOP = asyncio.new_event_loop()


class Error(Exception):
    def __init__(self, status, code):
        self.status, self.code = status, code


def session():
    try:
        directory = os.environ.get('CREDENTIALS_DIRECTORY')
        path = Path(directory) / 'bilibili-cookie' if directory else Path(os.environ['CM_BILIBILI_COOKIE_FILE'])
        with path.open(encoding='utf-8') as source:
            if os.fstat(source.fileno()).st_mode & 0o077:
                raise ValueError
            values = json.loads(source.read(8192))
        keys = ('sessdata', 'bili_jct', 'dedeuserid', 'buvid3', 'buvid4')
        if not isinstance(values.get('sessdata'), str) or not values['sessdata']:
            raise ValueError
        return Credential(**{key: values[key] for key in keys if isinstance(values.get(key), str)})
    except (OSError, ValueError, KeyError, AttributeError):
        raise Error(503, 'bilibili_session_unavailable') from None


async def details(bvid):
    info = await video.Video(bvid=bvid).get_info()
    owner = info.get('owner') or {}
    return {
        'bvid': bvid, 'title': info.get('title', ''), 'artwork': info.get('pic', ''),
        'uploader': owner.get('name', ''), 'description': info.get('desc', ''),
        'parts': [{key: part.get(key) for key in ('cid', 'page', 'part', 'duration')}
                  for part in (info.get('pages') or []) if part.get('cid')],
    }


async def dispatch(path, query):
    if path == '/health':
        return {'ok': True}
    if path == '/session':
        identity = await user.get_self_info(session())
        return {'activeVip': (identity.get('vip') or {}).get('status') == 1}
    if path == '/search':
        keyword = query.get('query', '').strip()
        page = int(query.get('page', 1))
        if not 1 <= len(keyword) <= 100 or not 1 <= page <= 100:
            raise Error(400, 'invalid_input')
        raw = await search.search_by_type(keyword, search_type=search.SearchObjectType.VIDEO, page=page, page_size=20)
        items = []
        for row in raw.get('result') or []:
            bvid = row.get('bvid')
            if isinstance(bvid, str) and BV.fullmatch(bvid):
                items.append({'bvid': bvid, 'title': unescape(re.sub(r'<[^>]+>', '', row.get('title') or ''))})
        return {'items': items, 'more': page < (raw.get('numPages') or page)}
    bvid = query.get('bvid', '')
    if not BV.fullmatch(bvid):
        raise Error(400, 'invalid_input')
    if path == '/details':
        return await details(bvid)
    if path == '/streams':
        cid = int(query.get('cid', '0'))
        if not 0 < cid < 10**18:
            raise Error(400, 'invalid_input')
        return await video.Video(bvid=bvid, credential=session()).get_download_url(cid=cid)
    raise Error(404, 'unsupported_operation')


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlsplit(self.path)
        query = {key: value[0] for key, value in parse_qs(path.query, max_num_fields=8).items()}
        status = 200
        if not SLOTS.acquire(blocking=False):
            self.reply(503, {'code': 'bilibili_busy'})
            return
        try:
            result = asyncio.run_coroutine_threadsafe(asyncio.wait_for(dispatch(path.path, query), 7), LOOP).result(8)
        except Error as error:
            status, result = error.status, {'code': error.code}
        except (ValueError, TypeError):
            status, result = 400, {'code': 'invalid_input'}
        except ResponseCodeException as error:
            if error.code in (-101, -111):
                status, result = 503, {'code': 'bilibili_session_unavailable'}
            elif error.code in (-404, 62002, 62004):
                status, result = 404, {'code': 'not_found'}
            else:
                status, result = 502, {'code': 'bilibili_upstream_error'}
        except Exception:
            status, result = 502, {'code': 'bilibili_upstream_error'}
        finally:
            SLOTS.release()
        self.reply(status, result)

    def reply(self, status, value):
        raw = json.dumps(value, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        with suppress(BrokenPipeError, ConnectionResetError):
            self.wfile.write(raw)

    def log_message(self, *_):
        pass


def main():
    LOOP.set_default_executor(SDK)
    threading.Thread(target=LOOP.run_forever, daemon=True).start()
    with ThreadingHTTPServer(('127.0.0.1', int(os.environ.get('CM_BILIBILI_PORT', '3202'))), Handler) as server:
        server.serve_forever()


if __name__ == '__main__':
    main()
