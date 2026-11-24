"""Read and edit the bound user's own upstream library."""

import os

import cm_aggregate as aggregate
import cm_db
import cm_kugou
import cm_kugou_account
import cm_kugou_catalog
import cm_ncm


PAGE_SIZE = 50


def binding_state(uid, provider):
    if provider == 'kugou':
        state = cm_kugou_account.status(uid)
        return 'bound' if state['bound'] else 'unbound' if state['enabled'] else 'unavailable'
    state = cm_db.get_binding(uid)
    if not state or not state['cookie'] or not state['ncm_uid']:
        return 'unbound'
    return 'expired' if state['stale'] else 'bound'


def writable(provider):
    return provider == 'netease' or os.environ.get('CM_KUGOU_NATIVE_LIBRARY_WRITE') == '1'


class NativeLibrary:
    def __init__(self, uid, provider):
        self.provider = provider
        if provider == 'kugou':
            self.cookie = cm_kugou_account._load(uid)
            self.uid = None
        else:
            state = cm_db.get_binding(uid)
            if not state or not state['cookie'] or not state['ncm_uid'] or state['stale']:
                aggregate.fail(403, 'binding_required', '请先绑定或重新连接个人网易云账号')
            self.cookie, self.uid = state['cookie'], state['ncm_uid']

    def request(self, path, values, *, write=False):
        try:
            if self.provider == 'kugou':
                result = cm_kugou_account._account_request(self.cookie, path, None if write else values,
                                                         payload=values if write else None)
                if result.get('status') != 1:
                    aggregate.fail(502, 'upstream_error', '酷狗没有确认本次歌单操作')
                return result.get('data') or {}
            response = (cm_ncm.post_json(path, values, cookie=self.cookie) if write else
                        cm_ncm.call(path, values, cookie=self.cookie, retries=0, bypass_cache=True))
            data = response.get('body', response)
            if data.get('code') == 401:
                aggregate.fail(403, 'binding_required', '网易云绑定已失效，请重新连接')
            if data.get('code') != 200:
                aggregate.fail(502, 'upstream_error', '网易云未确认本次歌单操作')
            return data
        except cm_ncm.NcmError as error:
            if error.status_code == 401:
                aggregate.fail(403, 'binding_required', '网易云绑定已失效，请重新连接')
            aggregate.fail(502, 'upstream_error', '网易云歌单暂不可用')

    def playlists(self, offset, limit):
        if self.provider == 'netease':
            response = self.request('/user/playlist', {'uid': self.uid, 'offset': offset, 'limit': limit})
            return response.get('playlist', []), offset + limit if response.get('more') else None, None
        index, skip = divmod(offset, PAGE_SIZE)
        page = self.request('/user/playlist', {'page': index + 1, 'pagesize': PAGE_SIZE})
        rows = page.get('info', [])[skip:skip + limit]
        version = page.get('total_ver')
        if len(rows) < limit and len(page.get('info', [])) == PAGE_SIZE:
            second = self.request('/user/playlist', {'page': index + 2, 'pagesize': PAGE_SIZE})
            rows += second.get('info', [])[:limit-len(rows)]
        total = (page.get('list_count') or 0) + (page.get('collect_count') or 0)
        next_offset = offset + len(rows)
        more = len(rows) == limit and (total > next_offset or next_offset % PAGE_SIZE == 0)
        return rows, next_offset if more else None, version

    def resource(self, row):
        if self.provider == 'kugou':
            ident = row['listid']
            name, desc, cover, count = row.get('name'), row.get('intro'), row.get('pic'), row.get('count')
            liked = row.get('is_def') == 2
            # Subscribed lists are identified by type. The built-in like list is editable via the heart action.
            edit = row.get('type') == 0 and row.get('is_edit') == 1 and not row.get('is_def')
            cover = cm_kugou._image(cover)
        else:
            ident = row['id']
            name, desc, cover, count = row.get('name'), row.get('description'), row.get('coverImgUrl'), row.get('trackCount')
            liked = row.get('specialType') == 5 and str(row.get('userId')) == str(self.uid)
            edit = str(row.get('userId')) == str(self.uid) and not liked
        return {'id': f'{self.provider}:{ident}', 'source': self.provider, 'kind': 'playlist',
                'name': name or '', 'description': desc or '', 'artwork': cover or '', 'trackCount': count or 0,
                'liked': liked, 'editable': edit and writable(self.provider)}

    def locate(self, ident=None, liked=False):
        offset = 0
        while offset is not None and offset <= 10000:
            rows, offset, version = self.playlists(offset, PAGE_SIZE)
            for row in rows:
                resource = self.resource(row)
                if (liked and resource['liked']) or (not liked and resource['id'] == f'{self.provider}:{ident}'):
                    return row, resource, version
        aggregate.fail(404, 'not_found', '绑定账号中未找到歌单')

    def tracks(self, ident, row, offset, limit):
        if self.provider == 'netease':
            answer = self.request('/playlist/track/all', {'id': ident, 'offset': offset, 'limit': limit})
            rows = answer.get('songs', [])
            tracks = [self._netease_track(song) for song in rows]
            return tracks, offset + len(rows) if rows and offset + len(rows) < (row.get('trackCount') or 0) else None, None, rows
        index, skip = divmod(offset, PAGE_SIZE)
        query = {'listid': ident, 'type': row.get('type', 0), 'pagesize': PAGE_SIZE}
        answer = self.request('/playlist/track/all/new', {**query, 'page': index + 1})
        rows = answer.get('info', [])[skip:skip + limit]
        if len(rows) < limit and skip + len(rows) == PAGE_SIZE:
            second = self.request('/playlist/track/all/new', {**query, 'page': index + 2})
            if second.get('list_ver') != answer.get('list_ver'):
                aggregate.fail(409, 'revision_conflict', '酷狗歌单已更新，请重试')
            rows += second.get('info', [])[:limit-len(rows)]
        tracks = [dict(track, provider='kugou') for song in rows if (track := cm_kugou_catalog._collection_song(song))]
        next_offset = offset + len(rows)
        return tracks, next_offset if rows and next_offset < (answer.get('count') or 0) else None, answer.get('list_ver'), rows

    @staticmethod
    def _netease_track(song):
        track = cm_ncm._norm_song(song)
        return {'provider': 'netease', 'id': str(track['ncm_id']), 'kind': 'song', 'name': track['name'],
                'artists': track['artists'], 'album': track['album'], 'artwork': track['pic'],
                'durationMs': track['duration'], 'videoId': str(song.get('mv') or '')}

    def _all_tracks(self, ident, row):
        offset, tracks, raw, revision = 0, [], [], None
        while offset is not None:
            new_tracks, offset, version, rows = self.tracks(ident, row, offset, PAGE_SIZE)
            if revision is not None and version != revision:
                aggregate.fail(409, 'revision_conflict', '上游歌单已更新，请重试')
            revision = version
            tracks.extend(new_tracks)
            raw.extend(rows)
            if len(raw) > 10000:
                aggregate.fail(400, 'invalid_input', '此歌单曲目超过批量操作上限')
        return tracks, raw, revision

    def favorites(self, offset, limit):
        row, resource, _ = self.locate(liked=True)
        tracks, next_offset, _, _ = self.tracks(int(resource['id'].split(':')[1]), row, offset, limit)
        return {'items': tracks, 'nextOffset': next_offset}

    def selected(self, refs):
        if self.provider == 'netease':
            data = self.request('/likelist', {'uid': self.uid})
            known = {str(ident) for ident in data.get('ids', [])}
        else:
            row, resource, _ = self.locate(liked=True)
            tracks, _, _ = self._all_tracks(int(resource['id'].split(':')[1]), row)
            known = {track['id'] for track in tracks}
        return [{'provider': provider, 'id': ident} for provider, ident in refs if provider == self.provider and ident in known]

    def _changes(self, refs, ident, row, selected):
        if any(provider != self.provider for provider, _ in refs):
            aggregate.fail(400, 'wrong_platform', 'App 歌单只支持本平台的歌曲，可改用流声聚合歌单')
        ids = [id for _, id in refs]
        if self.provider == 'netease':
            self.request('/playlist/tracks', {'pid': ident, 'tracks': ','.join(ids), 'op': 'add' if selected else 'del'}, write=True)
        elif selected:
            data = []
            for track_id in ids:
                song, meta, _ = cm_kugou.track(track_id)
                digest, mixid = track_id.split('-')
                name = ' - '.join(value for value in (song['artists'], song['name']) if value).replace('|', ' ').replace(',', '，')
                data.append(f"{name}|{digest}|{meta.get('album_id') or 0}|{mixid}")
            self.request('/playlist/tracks/add', {'listid': ident, 'data': ','.join(data)}, write=True)
        else:
            _, raw, _ = self._all_tracks(ident, row)
            fileids = [str(song['fileid']) for song in raw if song.get('fileid') and self._matches(song, ids)]
            if fileids:
                self.request('/playlist/tracks/del', {'listid': ident, 'fileids': ','.join(fileids)}, write=True)

    @staticmethod
    def _matches(row, ids):
        track = cm_kugou_catalog._collection_song(row)
        return track and track['id'] in ids

    def require_write(self):
        if not writable(self.provider):
            aggregate.fail(503, 'native_write_unavailable', '该服务尚未开放酷狗 App 歌单写入，可切换为流声聚合歌单')

    def set_favorite(self, ref, selected):
        self.require_write()
        if ref[0] != self.provider:
            aggregate.fail(400, 'wrong_platform', 'App 收藏须使用同平台歌曲')
        if bool(self.selected([ref])) == selected:
            return {'selected': selected}
        try:
            if self.provider == 'netease':
                cm_ncm.set_song_like(self.cookie, self.uid, int(ref[1]), selected)
            else:
                row, resource, _ = self.locate(liked=True)
                self._changes([ref], int(resource['id'].split(':')[1]), row, selected)
        except (cm_kugou.Error, cm_ncm.NcmError):
            if bool(self.selected([ref])) != selected:
                aggregate.fail(502, 'upstream_error', '上游尚未确认收藏状态，请刷新后重试')
        return {'selected': selected}

    def create(self, name, description):
        self.require_write()
        if self.provider == 'netease':
            data = self.request('/playlist/create', {'name': name, 'privacy': 10}, write=True)
            row = data.get('playlist') or data
            ident = row.get('id')
        else:
            data = self.request('/playlist/add', {'name': name, 'is_pri': 1}, write=True)
            ident = data.get('listid')
        if not ident:
            aggregate.fail(502, 'upstream_error', '上游未返回歌单 ID，请刷新列表确认结果')
        if description:
            return self.edit(int(ident), 'edit', {'name': name, 'description': description})
        return {'resource': self.locate(int(ident))[1]}

    def edit(self, ident, action, body):
        self.require_write()
        row, resource, version = self.locate(ident)
        if not resource['editable']:
            aggregate.fail(403, 'not_editable', '只能编辑本人创建的歌单')
        if action == 'delete':
            path = '/playlist/delete' if self.provider == 'netease' else '/playlist/del'
            self.request(path, {'id' if self.provider == 'netease' else 'listid': ident}, write=True)
            return {'ok': True}
        if action == 'edit':
            name = aggregate.value_text(body.get('name', resource['name']), 60, required=True)
            description = aggregate.value_text(body.get('description', resource['description']), 300)
            values = ({'id': ident, 'name': name, 'desc': description, 'tags': ';'.join(row.get('tags') or [])}
                      if self.provider == 'netease' else {'listid': ident, 'total_ver': version, 'name': name,
                      'intro': description, 'sort': row.get('sort', 0), 'tags': row.get('tags', '')})
            self.request('/playlist/update', values, write=True)
        if action in ('add', 'remove'):
            self._changes(aggregate.track_refs(body.get('tracks')), ident, row, action == 'add')
        if action == 'order':
            refs = aggregate.track_refs(body.get('tracks'), 10000, allow_empty=True)
            current, raw, revision = self._all_tracks(ident, row)
            if set(refs) != {(track['provider'], track['id']) for track in current} or len(refs) != len(raw):
                aggregate.fail(409, 'revision_conflict', '歌单已更新，请刷新后重试')
            if self.provider == 'netease':
                self.request('/song/order/update', {'pid': ident, 'ids': '['+','.join(track for _,track in refs)+']'}, write=True)
            else:
                fileids = {track['id']: item['fileid'] for item in raw if item.get('fileid') and
                           (track := cm_kugou_catalog._collection_song(item))}
                if len(fileids) != len(refs):
                    aggregate.fail(502, 'upstream_error', '酷狗未返回完整的歌单排序资料')
                data = ','.join(f'{fileids[track]}|{index}' for index, (_,track) in enumerate(refs, 1))
                self.request('/playlist/tracks/sort', {'listid': ident, 'list_ver': revision, 'data': data}, write=True)
        return {'resource': self.locate(ident)[1]}
