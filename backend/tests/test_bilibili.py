import io
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from xml.etree import ElementTree

import test_music_service as harness
import cm_bilibili as bili
import cm_db
import cm_server


BV = 'BV1tfei6MEMn'
ID = f'{BV}:10'
ORIGIN = 'https://provider.example.test'
CONFIG = {'CM_BILIBILI_BASE': 'http://127.0.0.1:3202', 'CM_BILIBILI_PLAYBACK': '1'}
MEDIA = 'https://upos-hz-mirrorakam.akamaized.net/fixture.m4s'


def stream(ident, codecs):
    return {'id': ident, 'codecs': codecs, 'baseUrl': MEDIA, 'bandwidth': 125000,
            'SegmentBase': {'Initialization': '0-99', 'indexRange': '100-200'}}


DASH = {'duration': 100, 'dolby': {'type': 2, 'audio': [stream(30250, 'ec-3')]},
        'audio': [stream(30280, 'mp4a.40.2')],
        'video': [stream(126, 'hvc1.2.4.H150.90'), stream(80, 'avc1.640028')]}
INFO = {'bvid': BV, 'title': '花の塔 Remix', 'uploader': 'fixture', 'parts': [
    {'cid': 10, 'page': 1, 'part': '花の塔 Remix', 'duration': 105},
    {'cid': 20, 'page': 2, 'part': '花の塔 Remix', 'duration': 106},
]}


class BilibiliTests(unittest.TestCase):
    setUp = harness.MusicServiceTests.setUp
    _cleanup = harness.MusicServiceTests._cleanup
    call = harness.MusicServiceTests.call
    account = harness.MusicServiceTests.account
    operation = harness.MusicServiceTests.operation

    def test_public_catalog_and_playback_permission(self):
        uid, token = self.account('openid music.play')
        with patch.dict(os.environ, CONFIG), patch.object(bili, '_details', return_value=INFO):
            status, result, _ = self.call('POST', '/bilibili/v1/catalog.search', {'query': BV})
            self.assertEqual(status, 200)
            self.assertEqual([row['id'] for row in result['items']], [ID, f'{BV}:20'])
            self.assertEqual(self.call('POST', '/bilibili/v1/playback.resolve', {'id': ID})[0], 401)
            self.assertEqual(self.operation('playback.resolve', {'id': ID}, provider='bilibili')[0], 401)
            with patch.object(bili, '_streams', return_value=DASH), patch.object(bili, '_verify_audio', return_value=True):
                status, result = self.operation('playback.resolve', {'id': ID, 'supportsDolby': True}, bearer=token, provider='bilibili')
                self.assertEqual((status, result['trackId'], result['codecs']), (200, 30250, 'ec-3'))
                self.assertEqual(bili._tickets[result['url'].rsplit('/', 1)[1]].uid, uid)
                self.assertNotIn(MEDIA, result['url'])

    def test_match_boundary_and_manual_override(self):
        with patch.dict(os.environ, CONFIG), patch.object(bili, '_details', return_value=INFO), \
                patch.object(bili, '_search_entries', return_value=([BV], False)), patch.object(bili, '_streams', return_value=DASH), \
                patch.object(bili, '_native_track', return_value={'name': '花の塔', 'duration': 100000}):
            result = bili._candidates('花の塔', 100000)
            self.assertEqual([(row['id'], row['differenceMs']) for row in result], [(ID, 5000)])
            self.assertEqual(bili.linked('netease', '11'), ID)
            self.assertEqual(self.call('POST', '/admin/bilibili/link', {'provider': 'netease', 'id': '11', 'mode': 'blocked'})[0], 401)
            bili.admin('link', {'provider': 'netease', 'id': '11', 'mode': 'blocked'}, 1)
            self.assertEqual(bili.linked('netease', '11'), '')
            bili.admin('link', {'provider': 'netease', 'id': '11', 'mode': 'manual', 'videoId': f'{BV}:20'}, 1)
            self.assertEqual(bili.linked('netease', '11'), f'{BV}:20')

    def test_manifest_keeps_codecs_and_separates_ranges(self):
        with patch.object(bili, '_details', return_value=INFO), patch.object(bili, '_streams', return_value=DASH):
            result = bili.resolve_video(ID, {'supportsDolby': True}, 1, ORIGIN)
            fallback = bili.resolve_video(ID, {'supportsDolbyVision': False}, 1, ORIGIN)
        self.assertEqual((result['dolbyVision'], result['audioCodec']), (True, 'ec-3'))
        self.assertEqual((fallback['dolbyVision'], fallback['audioCodec']), (False, 'mp4a.40.2'))
        stored = bili._tickets[result['url'].rsplit('/', 1)[1]]
        doc = ElementTree.fromstring(stored.manifest)
        self.assertNotIn(MEDIA.encode(), stored.manifest)
        self.assertEqual(len(doc.findall('.//{*}SegmentBase')), 2)
        self.assertTrue(all(x.text.startswith(ORIGIN) for x in doc.findall('.//{*}BaseURL')))

    def test_range_and_revocation(self):
        uid, _ = self.account('openid music.play')
        handler = SimpleNamespace(headers={'Range': 'bytes=0-15'}, wfile=io.BytesIO(), head_only=False)
        def record(status, *_):
            handler.status = status
        handler.send_response, handler.send_header, handler.end_headers = record, lambda *_: None, lambda: None
        fixture = io.BytesIO(b'0000ftyp00000000')
        fixture.status = 206
        fixture.headers = {'Content-Length': '16', 'Content-Range': 'bytes 0-15/1000'}
        with patch.dict(os.environ, CONFIG), patch.object(bili, '_details', return_value=INFO), \
                patch.object(bili, '_verify_audio', return_value=True), patch.object(bili, '_open_media', return_value=fixture) as media:
            url = bili.resolve_audio(ID, 'standard', False, uid, ORIGIN, dash=DASH)['url']
            key = url.rsplit('/', 1)[1]
            bili.stream(handler, key)
            self.assertEqual((handler.status, handler.wfile.getvalue()), (206, b'0000ftyp00000000'))
            media.assert_called_once_with(MEDIA, 'bytes=0-15')
            with patch.object(bili, 'authorized', return_value=False), self.assertRaisesRegex(bili.Error, '没有播放权限'):
                bili.stream(handler, key)
            bili._tickets[key].expires = 0
            with self.assertRaisesRegex(bili.Error, '已过期'):
                bili.stream(handler, key)

    def test_netease_dolby_first_and_native_fallback(self):
        uid, _ = self.account('openid music.play')
        token = cm_db.issue_token(uid)
        path = '/ncm/song/url?id=11&allowBilibili=1&supportsDolby=1'
        with patch.dict(os.environ, CONFIG), patch.object(cm_server, '_probe_one', side_effect=[{}, None, None]), \
                patch.object(cm_server, '_verify_auto_audio', side_effect=[{'url': 'https://native.example/dolby'}, None, None]), \
                patch.object(bili, 'linked_dolby', side_effect=[{'url': ORIGIN + '/cm/bilibili/media/test', 'format': 'm4a',
                   'bitrate': 1000000, 'provider': 'bilibili'}, None]) as alternate, \
                patch.object(cm_server, 'resolve_play_url', return_value={'level': 'vivid'}) as fallback:
            status, result, _ = self.call('GET', path, bearer=token)
            self.assertEqual((status, result['level']), (200, 'dolby'))
            alternate.assert_not_called()
            status, result, _ = self.call('GET', path, bearer=token)
            self.assertEqual((status, result['provider']), (200, 'bilibili'))
            fallback.assert_not_called()
            status, result, _ = self.call('GET', path, bearer=token)
            self.assertEqual((status, result['level']), (200, 'vivid'))
            self.assertFalse(fallback.call_args.kwargs['supports_dolby'])
