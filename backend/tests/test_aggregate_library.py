import unittest
from unittest.mock import patch
import test_music_service as harness

import cm_aggregate
import cm_db
import cm_kugou
import cm_kugou_account
import cm_kugou_library
from test_kugou import ID


class AggregateLibraryTests(unittest.TestCase):
    setUp = harness.MusicServiceTests.setUp
    _cleanup = harness.MusicServiceTests._cleanup
    call = harness.MusicServiceTests.call

    def user(self, name):
        uid = cm_db.create_user(name, 'tester-password', name)
        return uid, cm_db.issue_token(uid)

    def api(self, method, path, token='', body=None):
        return self.call(method, '/library/v2/' + path, body, token)

    def test_mixed_playlist_order_revisions_projections_and_sharing(self):
        uid, owner = self.user('aggregate-owner')
        _, outsider = self.user('aggregate-outsider')
        cm_db.upsert_song({'ncm_id': 42, 'name': 'NetEase recording'})
        cm_db.upsert_song({'ncm_id': 512, 'name': 'Second NetEase recording'})
        ncm, kugou, second = {'provider': 'netease', 'id': '42'}, {'provider': 'kugou', 'id': ID}, {'provider': 'netease', 'id': '512'}
        metadata = cm_kugou._track(ID, 'Fixture KuGou', 'Singer', '', '', 5000)
        self.assertEqual(self.api('GET', 'playlists?platform=kugou')[0], 401)
        with patch.object(cm_kugou, 'tracks', return_value=[metadata]):
            status, body, headers = self.api('POST', 'playlists?platform=kugou', owner,
                                             {'name': 'Mix', 'tracks': [ncm, kugou, second]})
        self.assertEqual((status, headers['Cache-Control']), (200, 'no-store'))
        pl = body['resource']
        pid = pl['id'].split(':')[1]
        path = 'playlists/' + pl['id']
        self.assertEqual(self.api('GET', path, outsider)[0], 404)
        self.assertEqual(self.call('GET', '/playlists/' + pid, bearer=outsider)[0], 404)
        self.assertEqual(self.api('GET', 'shared/' + pid)[0], 404)
        self.assertNotIn(pid, {str(row['id']) for row in cm_db.public_profile(uid)['playlists']})
        tracks = self.api('GET', path + '?limit=2', owner)[1]
        self.assertEqual(([t['provider'] for t in tracks['tracks']], tracks['nextOffset']), (['netease', 'kugou'], 2))
        version = pl['version']
        order = {'version': version, 'tracks': [second, kugou, ncm]}
        status, body, _ = self.api('PUT', path + '/order', owner, order)
        self.assertEqual(status, 200)
        self.assertEqual(cm_db.playlist_track_ids(int(pid)), [512, 42])
        self.assertEqual(self.api('PUT', path + '/order', owner, order)[1]['code'], 'revision_conflict')
        status, _, _ = self.api('PUT', path, owner, {'version': body['resource']['version'], 'shared': True})
        self.assertEqual(status, 200)
        self.assertEqual([t['id'] for t in self.api('GET', 'shared/' + pid)[1]['tracks']], ['512', ID, '42'])
        self.assertEqual(self.api('PUT', path, outsider, {'version': version, 'shared': True})[0], 404)

    def test_migration_is_idempotent_and_legacy_edits_update_versions(self):
        uid, token = self.user('migration-user')
        ncm = cm_db.create_playlist(uid, 'Local')
        cm_db.playlist_add_tracks(ncm, [42])
        snapshot = cm_db.create_playlist(uid, 'App snapshot', source='ncm', ncm_pl_id=777)
        cm_db.playlist_add_tracks(snapshot, [42])
        with patch.object(cm_kugou, 'tracks', return_value=[cm_kugou._track(ID, 'KuGou', '', '', '', 5000)]):
            old = cm_kugou_library.playlist_create(uid, 'KuGou', '', [ID])['resource']['id']
        # Recreate the pre-upgrade state and run the migration on real legacy rows.
        with cm_aggregate.transaction(write=True) as conn:
            pid = conn.execute('SELECT playlist_id FROM aggregate_playlists WHERE kugou_alias IS NOT NULL').fetchone()[0]
            conn.execute('DELETE FROM aggregate_tracks')
            conn.execute('DELETE FROM aggregate_playlists')
            conn.execute('DELETE FROM playlists WHERE id=?', (pid,))
            conn.execute('DELETE FROM library_migrations')
        cm_db.init()
        cm_db.init()
        records = self.api('GET', 'playlists?platform=netease', token)[1]['items']
        self.assertEqual(sorted(row['name'] for row in records), ['KuGou', 'Local'])
        migrated = self.api('GET', 'aliases/' + old, token)[1]['resource']
        self.assertEqual(self.api('GET', 'playlists/' + migrated['id'], token)[1]['tracks'][0]['id'], ID)
        cm_kugou_library.playlist_edit(uid, old, 'Renamed with old client', None)
        changed = self.api('GET', 'playlists/' + migrated['id'], token)[1]['resource']
        self.assertGreater(changed['version'], migrated['version'])
        self.assertEqual(changed['name'], 'Renamed with old client')
        self.assertEqual(self.api('POST', 'playlists/import', token, {'snapshotId': snapshot})[0], 200)
        self.assertEqual(cm_db.get_playlist(snapshot)['source'], 'ncm')

    def test_favorites_use_source_and_preserve_mixed_legacy_rows(self):
        uid, token = self.user('favorite-owner')
        _, outsider = self.user('favorite-outsider')
        cm_db.upsert_song({'ncm_id': 42, 'name': 'NetEase recording'})
        legacy = self.call('POST', '/likes/42', {'name': 'Fixture'}, token)
        self.assertEqual(legacy[0], 200)
        ncm = {'provider': 'netease', 'id': '42'}
        kg = {'provider': 'kugou', 'id': ID}
        with patch.object(cm_kugou, 'tracks', return_value=[cm_kugou._track(ID, 'KG', '', '', '', 5000)]):
            for _ in range(2):
                self.assertEqual(self.api('PUT', 'favorites?platform=kugou', token, {'track': kg, 'selected': True})[:2],
                                 (200, {'selected': True}))
        for platform in ('netease', 'kugou'):
            path = 'favorites?platform=' + platform
            result = self.api('GET', path, token)[1]
            self.assertEqual({row['id'] for row in result['items']}, {ID, '42'})
            self.assertEqual(self.api('GET', path, outsider)[1]['items'], [])
        self.assertEqual(self.api('POST', 'favorites/status?platform=kugou', token,
                                  {'tracks': [ncm, kg]})[1]['selected'], [ncm, kg])
        self.assertEqual(cm_kugou_library.favorite_status(uid, [ID])['selectedIds'], [ID])

    def test_listening_summary_counts_played_time_and_omits_unknown_dates_from_week(self):
        uid, token = self.user('footprint-owner')
        _, outsider = self.user('footprint-outsider')
        metadata = cm_kugou._track(ID, 'Fixture KuGou', '', '', '', 100_000)
        with patch.object(cm_kugou, 'tracks', return_value=[metadata]):
            old = cm_kugou_library.record(uid, ID, None, 0)
            new = cm_kugou_library.record(uid, ID, None, 0)
            cm_kugou_library.record(uid, ID, None, 0)
        cm_kugou_library.record(uid, ID, int(old['playId']), 50_000)
        cm_kugou_library.record(uid, ID, int(new['playId']), 70_000)
        with cm_aggregate.transaction(write=True) as conn:
            conn.execute('UPDATE kugou_plays SET started_at=0 WHERE id=?', (old['playId'],))
        with patch.object(cm_kugou, 'base', return_value='http://127.0.0.1:3201'):
            status, data, _ = self.call('POST', '/kugou/v1/history.summary', {}, token)
            foreign = self.call('POST', '/kugou/v1/history.summary', {}, outsider)[1]
        self.assertEqual((status, data['totalMs'], data['weekMs'], data['todayTracks'], data['plays']), (200, 120_000, 70_000, 1, 2))
        self.assertEqual(data['rank'][0]['id'], ID)
        self.assertEqual(foreign['totalMs'], 0)

    def test_preferences_and_verified_native_read_use_personal_session(self):
        uid, token = self.user('native-user')
        self.assertEqual(self.api('GET', 'preferences', token)[1]['providers']['kugou']['source'], 'aggregate')
        info = {'listid': 9, 'is_def': 2, 'name': '内置歌单', 'type': 0, 'is_edit': 1}
        with (patch.object(cm_kugou_account, 'status', return_value={'bound': True, 'enabled': True}),
              patch.object(cm_kugou_account, '_load', return_value='fixture-personal-session'),
              patch.object(cm_kugou_account, '_account_request', return_value={'status': 1, 'data': {'info': [info]}}) as upstream,
              patch.object(cm_kugou, 'server_cookie', side_effect=AssertionError('personal library used shared credentials'))):
            self.assertEqual(self.api('GET', 'preferences', token)[1]['providers']['kugou']['source'], 'native')
            status, body, _ = self.api('GET', 'playlists?platform=kugou', token)
            self.assertEqual((status, body['items'][0]['id'], body['items'][0]['liked']), (200, 'kugou:9', True))
            self.assertEqual(upstream.call_args.args[0], 'fixture-personal-session')
            self.assertEqual(self.api('PUT', 'preferences', token, {'providers': {'kugou': 'aggregate'}})[0], 200)
            self.assertEqual(self.api('GET', 'playlists?platform=kugou', token)[1]['items'], [])
        self.assertEqual(self.api('PUT', 'preferences', token, {'providers': {'kugou': 'native'}})[0], 200)
        self.assertEqual(self.api('GET', 'playlists?platform=kugou', token)[1]['code'], 'kugou_account_unavailable')
