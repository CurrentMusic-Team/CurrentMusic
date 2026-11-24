"""Library API shared by the Web player and CurrentMusicX."""

import cm_aggregate as aggregate
from cm_native_library import NativeLibrary, binding_state, writable


def preferences(uid):
    with aggregate.transaction() as conn:
        saved = dict(conn.execute('SELECT provider,mode FROM library_preferences WHERE user_id=?', (uid,)))
    result = {}
    for provider in aggregate.PROVIDERS:
        binding = binding_state(uid, provider)
        mode = saved.get(provider, 'auto')
        source = ('native' if binding in ('bound', 'expired') else 'aggregate') if mode == 'auto' else mode
        result[provider] = {'mode': mode, 'source': source, 'binding': binding,
                            'writable': source == 'aggregate' or (binding == 'bound' and writable(provider))}
    return {'providers': result}


def change_preferences(uid, body):
    modes = body.get('providers')
    if not isinstance(modes, dict) or not modes or any(provider not in aggregate.PROVIDERS or mode not in aggregate.MODES
                                                        for provider, mode in modes.items()):
        aggregate.fail(400, 'invalid_input', '需要有效的平台和歌单来源设置')
    with aggregate.transaction(write=True) as conn:
        conn.executemany('''INSERT INTO library_preferences VALUES (?,?,?) ON CONFLICT(user_id,provider)
                            DO UPDATE SET mode=excluded.mode''', [(uid, provider, mode) for provider, mode in modes.items()])
    return preferences(uid)


def _target(uid, query):
    provider = query.get('platform')
    if provider not in aggregate.PROVIDERS:
        aggregate.fail(400, 'invalid_input', '需要有效的音乐平台')
    source = query.get('source') or preferences(uid)['providers'][provider]['source']
    if source not in ('aggregate', 'native'):
        aggregate.fail(400, 'invalid_input', '无效的歌单来源')
    return NativeLibrary(uid, provider) if source == 'native' else None


def _paging(query):
    try:
        offset, limit = int(query.get('offset', 0)), int(query.get('limit', 50))
    except (ValueError, TypeError):
        aggregate.fail(400, 'invalid_input', '无效的分页参数')
    if not 0 <= offset <= 10000 or not 1 <= limit <= 50:
        aggregate.fail(400, 'invalid_input', '分页超出允许范围')
    return offset, limit


def _playlist_id(value):
    source, _, ident = value.partition(':')
    if source not in ('aggregate', *aggregate.PROVIDERS):
        aggregate.fail(400, 'invalid_input', '无效的歌单来源')
    return source, aggregate.integer_id(ident)


def dispatch(uid, action, query, body=None, playlist=None):
    body = {} if body is None else body
    if not isinstance(body, dict):
        aggregate.fail(400, 'invalid_input', '需要 JSON 对象')
    if action == 'preferences':
        return preferences(uid)
    if action == 'preferences.write':
        return change_preferences(uid, body)
    if action == 'alias':
        return aggregate.legacy_alias(uid, aggregate.integer_id(playlist.removeprefix('kgp_')))
    if action == 'import':
        return aggregate.create(uid, '', '', [], snapshot=aggregate.integer_id(body.get('snapshotId')))
    if action == 'shared':
        return aggregate.detail(uid, aggregate.integer_id(playlist), *_paging(query), shared=True)
    if playlist is not None:
        source, ident = _playlist_id(playlist)
        if source == 'aggregate':
            return (aggregate.detail(uid, ident, *_paging(query)) if action == 'detail'
                    else aggregate.modify(uid, ident, action, body))
        native = NativeLibrary(uid, source)
        if action != 'detail':
            return native.edit(ident, action, body)
        row, resource, _ = native.locate(ident)
        tracks, next_offset, _, _ = native.tracks(ident, row, *_paging(query))
        return {'resource': resource, 'tracks': tracks, 'nextOffset': next_offset}
    native = _target(uid, query)
    if action == 'list':
        offset, limit = _paging(query)
        if native:
            rows, next_offset, _ = native.playlists(offset, limit)
            return {'items': [native.resource(row) for row in rows], 'nextOffset': next_offset}
        return aggregate.list_playlists(uid, offset, limit)
    if action == 'create':
        name = aggregate.value_text(body.get('name'), 60, required=True)
        description = aggregate.value_text(body.get('description', ''), 300)
        if native:
            return native.create(name, description)
        return aggregate.create(uid, name, description, aggregate.track_refs(body.get('tracks', []), allow_empty=True))
    if action == 'favorites':
        return (native.favorites(*_paging(query)) if native else aggregate.list_favorites(uid, *_paging(query)))
    if action == 'favorites.status':
        tracks = aggregate.track_refs(body.get('tracks'), maximum=100)
        return {'selected': native.selected(tracks) if native else aggregate.selected_favorites(uid, tracks)}
    track = aggregate.track_ref(body.get('track'))
    selected = aggregate.flag(body, 'selected')
    return native.set_favorite(track, selected) if native else aggregate.set_favorite(uid, track, selected)
