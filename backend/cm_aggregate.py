"""CurrentMusic's account-owned, provider-aware library and legacy projections."""

from contextlib import closing, contextmanager
import json
import re
import time

import cm_db
import cm_kugou
import cm_kugou_library
import cm_ncm


PROVIDERS = ('netease', 'kugou')
MODES = ('auto', 'native', 'aggregate')
SCHEMA = """
CREATE TABLE IF NOT EXISTS library_migrations (name TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS library_preferences (
    user_id INTEGER NOT NULL, provider TEXT NOT NULL, mode TEXT NOT NULL,
    PRIMARY KEY (user_id, provider)
);
CREATE TABLE IF NOT EXISTS aggregate_playlists (
    playlist_id INTEGER PRIMARY KEY, kugou_alias INTEGER UNIQUE,
    version INTEGER NOT NULL DEFAULT 1, legacy_public INTEGER NOT NULL DEFAULT 1,
    shared INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS aggregate_tracks (
    playlist_id INTEGER NOT NULL, provider TEXT NOT NULL, track_id TEXT NOT NULL, position INTEGER NOT NULL,
    PRIMARY KEY (playlist_id, provider, track_id)
);
CREATE INDEX IF NOT EXISTS aggregate_track_order ON aggregate_tracks(playlist_id, position);
CREATE TRIGGER IF NOT EXISTS aggregate_playlist_added AFTER INSERT ON playlists
WHEN COALESCE(NEW.source, 'local') <> 'ncm' BEGIN
    INSERT INTO aggregate_playlists(playlist_id) VALUES (NEW.id);
END;
CREATE TRIGGER IF NOT EXISTS aggregate_playlist_changed AFTER UPDATE OF name, description, cover ON playlists BEGIN
    UPDATE aggregate_playlists SET version=version+1 WHERE playlist_id=NEW.id;
END;
CREATE TRIGGER IF NOT EXISTS aggregate_playlist_removed AFTER DELETE ON playlists BEGIN
    DELETE FROM playlist_tracks WHERE playlist_id=OLD.id;
    DELETE FROM aggregate_tracks WHERE playlist_id=OLD.id;
    DELETE FROM kugou_playlist_tracks WHERE playlist_id=(SELECT kugou_alias FROM aggregate_playlists WHERE playlist_id=OLD.id);
    DELETE FROM kugou_playlists WHERE id=(SELECT kugou_alias FROM aggregate_playlists WHERE playlist_id=OLD.id);
    DELETE FROM aggregate_playlists WHERE playlist_id=OLD.id;
END;
CREATE TRIGGER IF NOT EXISTS aggregate_netease_track_added AFTER INSERT ON playlist_tracks
WHEN EXISTS (SELECT 1 FROM aggregate_playlists WHERE playlist_id=NEW.playlist_id) BEGIN
    INSERT OR IGNORE INTO aggregate_tracks VALUES (NEW.playlist_id, 'netease', CAST(NEW.ncm_id AS TEXT),
        (SELECT COALESCE(MAX(position),0)+1 FROM aggregate_tracks WHERE playlist_id=NEW.playlist_id));
    UPDATE aggregate_playlists SET version=version+1 WHERE playlist_id=NEW.playlist_id;
END;
CREATE TRIGGER IF NOT EXISTS aggregate_netease_track_removed AFTER DELETE ON playlist_tracks BEGIN
    DELETE FROM aggregate_tracks WHERE playlist_id=OLD.playlist_id AND provider='netease' AND track_id=CAST(OLD.ncm_id AS TEXT);
    UPDATE aggregate_playlists SET version=version+1 WHERE playlist_id=OLD.playlist_id;
END;
CREATE TRIGGER IF NOT EXISTS aggregate_kugou_playlist_added AFTER INSERT ON kugou_playlists BEGIN
    INSERT INTO playlists(user_id,name,description,source,created_at,updated_at)
        VALUES (NEW.user_id,NEW.name,NEW.description,'local',unixepoch(),unixepoch());
    UPDATE aggregate_playlists SET kugou_alias=NEW.id, legacy_public=0 WHERE playlist_id=last_insert_rowid();
END;
CREATE TRIGGER IF NOT EXISTS aggregate_kugou_playlist_changed AFTER UPDATE OF name, description ON kugou_playlists BEGIN
    UPDATE playlists SET name=NEW.name,description=NEW.description,updated_at=unixepoch()
        WHERE id=(SELECT playlist_id FROM aggregate_playlists WHERE kugou_alias=NEW.id);
END;
CREATE TRIGGER IF NOT EXISTS aggregate_kugou_playlist_removed AFTER DELETE ON kugou_playlists BEGIN
    DELETE FROM playlists WHERE id=(SELECT playlist_id FROM aggregate_playlists WHERE kugou_alias=OLD.id);
END;
CREATE TRIGGER IF NOT EXISTS aggregate_kugou_track_added AFTER INSERT ON kugou_playlist_tracks BEGIN
    INSERT OR IGNORE INTO aggregate_tracks
        SELECT a.playlist_id, 'kugou', NEW.track_id,
            (SELECT COALESCE(MAX(position),0)+1 FROM aggregate_tracks WHERE playlist_id=a.playlist_id)
        FROM aggregate_playlists a WHERE a.kugou_alias=NEW.playlist_id;
    UPDATE aggregate_playlists SET version=version+1 WHERE kugou_alias=NEW.playlist_id;
END;
CREATE TRIGGER IF NOT EXISTS aggregate_kugou_track_removed AFTER DELETE ON kugou_playlist_tracks BEGIN
    DELETE FROM aggregate_tracks WHERE provider='kugou' AND track_id=OLD.track_id
        AND playlist_id=(SELECT playlist_id FROM aggregate_playlists WHERE kugou_alias=OLD.playlist_id);
    UPDATE aggregate_playlists SET version=version+1 WHERE kugou_alias=OLD.playlist_id;
END;
"""


class Error(cm_kugou.Error):
    pass


def fail(status, code, message):
    raise Error(status, code, message)


@contextmanager
def transaction(write=False):
    with closing(cm_db.connect()) as conn, conn:
        conn.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
        yield conn


def migrate(conn):
    conn.executescript(SCHEMA)
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        marker = conn.execute("INSERT OR IGNORE INTO library_migrations VALUES ('aggregate-v1')")
        if not marker.rowcount:
            return
        conn.execute("""INSERT OR IGNORE INTO aggregate_playlists(playlist_id)
                        SELECT id FROM playlists WHERE COALESCE(source,'local') <> 'ncm'""")
        conn.execute("""INSERT OR IGNORE INTO aggregate_tracks
                        SELECT t.playlist_id,'netease',CAST(t.ncm_id AS TEXT),t.seq
                        FROM playlist_tracks t JOIN aggregate_playlists a ON a.playlist_id=t.playlist_id""")
        for row in conn.execute('SELECT id,user_id,name,description FROM kugou_playlists ORDER BY id').fetchall():
            ident = conn.execute("""INSERT INTO playlists(user_id,name,description,source,created_at,updated_at)
                VALUES (?,?,?,'local',0,0)""", (row['user_id'], row['name'], row['description'])).lastrowid
            conn.execute('UPDATE aggregate_playlists SET kugou_alias=?,legacy_public=0 WHERE playlist_id=?', (row['id'], ident))
            conn.execute("""INSERT INTO aggregate_tracks SELECT ?, 'kugou', track_id, seq
                            FROM kugou_playlist_tracks WHERE playlist_id=?""", (ident, row['id']))


def integer_id(value):
    value = str(value)
    if not re.fullmatch(r'[1-9][0-9]{0,18}', value) or int(value) >= 2**63:
        fail(400, 'invalid_input', '无效的资源 ID')
    return int(value)


def track_ref(value):
    if not isinstance(value, dict):
        fail(400, 'invalid_input', '需要带平台标识的曲目')
    provider, ident = value.get('provider'), value.get('id')
    if provider == 'netease':
        return provider, str(integer_id(ident))
    if provider == 'kugou' and isinstance(ident, str) and re.fullmatch(r'[a-f0-9]{32}-[1-9][0-9]{0,14}', ident):
        return provider, ident
    fail(400, 'invalid_input', '无效的曲目平台或 ID')


def track_refs(values, maximum=50, allow_empty=False):
    if not isinstance(values, list) or not (0 if allow_empty else 1) <= len(values) <= maximum:
        fail(400, 'invalid_input', '无效的曲目列表长度')
    return list(dict.fromkeys(track_ref(value) for value in values))


def page(rows, offset, limit):
    return {'items': rows[:limit], 'nextOffset': offset + limit if len(rows) > limit else None}


def track_rows(conn, refs):
    netease = [int(ident) for provider, ident in refs if provider == 'netease']
    kugou = [ident for provider, ident in refs if provider == 'kugou']
    data = {}
    if netease:
        marks = ','.join('?' for _ in netease)
        for row in conn.execute(f'SELECT * FROM songs WHERE ncm_id IN ({marks})', netease):
            data['netease', str(row['ncm_id'])] = {
                'name': row['name'], 'artists': row['artists'], 'album': row['album'],
                'artwork': row['pic'], 'durationMs': row['duration'],
            }
    if kugou:
        marks = ','.join('?' for _ in kugou)
        for row in conn.execute(f'SELECT id,resource FROM kugou_tracks WHERE id IN ({marks})', kugou):
            data['kugou', row['id']] = json.loads(row['resource'])
    return [{**data.get((provider, ident), {'name': '歌曲资料暂不可用'}), 'provider': provider, 'id': ident,
             'kind': 'song'} for provider, ident in refs]


def prepare_tracks(refs):
    with transaction() as conn:
        missing = [(row['provider'], row['id']) for row in track_rows(conn, refs) if 'durationMs' not in row]
    netease = [ident for provider, ident in missing if provider == 'netease']
    kugou = [ident for provider, ident in missing if provider == 'kugou']
    if netease:
        found = cm_ncm.song_detail(netease)
        if {str(row['ncm_id']) for row in found} != set(netease):
            fail(404, 'track_not_found', '找不到部分网易云曲目')
        cm_db.upsert_songs_many(found)
    return cm_kugou.tracks(kugou) if kugou else []


_SELECT = """SELECT p.*,a.kugou_alias,a.version,a.shared,a.legacy_public,
    (SELECT COUNT(*) FROM aggregate_tracks t WHERE t.playlist_id=p.id) AS track_count
    FROM playlists p JOIN aggregate_playlists a ON a.playlist_id=p.id"""


def resource(row):
    return {'id': f"aggregate:{row['id']}", 'kind': 'playlist', 'source': 'aggregate',
            'name': row['name'], 'description': row['description'], 'artwork': row['cover'],
            'trackCount': row['track_count'], 'version': row['version'], 'shared': bool(row['shared']),
            'editable': True}


def owned(conn, uid, pid, version=None):
    row = conn.execute(_SELECT + ' WHERE p.id=? AND p.user_id=?', (pid, uid)).fetchone()
    if row is None:
        fail(404, 'not_found', '歌单不存在')
    if version is not None and (type(version) is not int or version != row['version']):
        fail(409, 'revision_conflict', '歌单已经更新，请刷新后重试')
    return row


def list_playlists(uid, offset, limit):
    with transaction() as conn:
        rows = conn.execute(_SELECT + ' WHERE p.user_id=? ORDER BY p.created_at DESC,p.id DESC LIMIT ? OFFSET ?',
                            (uid, limit + 1, offset))
        return page([resource(row) for row in rows], offset, limit)


def legacy_alias(uid, alias):
    with transaction() as conn:
        row = conn.execute(_SELECT + ' WHERE p.user_id=? AND a.kugou_alias=?', (uid, alias)).fetchone()
        if row is None:
            fail(404, 'not_found', '歌单不存在')
        return {'resource': resource(row)}


def visibility(pid):
    with transaction() as conn:
        return conn.execute('SELECT legacy_public,shared FROM aggregate_playlists WHERE playlist_id=?', (pid,)).fetchone()


def detail(uid, pid, offset, limit, shared=False):
    with transaction() as conn:
        if shared:
            row = conn.execute(_SELECT + ' WHERE p.id=? AND a.shared=1', (pid,)).fetchone()
            if row is None:
                fail(404, 'not_found', '歌单不存在或未开启分享')
        else:
            row = owned(conn, uid, pid)
        tracks = conn.execute('''SELECT provider,track_id FROM aggregate_tracks WHERE playlist_id=?
                              ORDER BY position,provider,track_id LIMIT ? OFFSET ?''', (pid, limit + 1, offset))
        result = page(track_rows(conn, [tuple(track) for track in tracks]), offset, limit)
        return {'resource': {**resource(row), 'editable': not shared}, 'tracks': result['items'], 'nextOffset': result['nextOffset']}


def _append(conn, row, refs, new_resources):
    cm_kugou_library._save_resources(conn, new_resources)
    pid = row['id']
    for provider, ident in refs:
        if provider == 'netease':
            conn.execute('INSERT OR IGNORE INTO playlist_tracks(playlist_id,ncm_id) VALUES (?,?)', (pid, int(ident)))
        elif row['kugou_alias'] is not None:
            conn.execute('INSERT OR IGNORE INTO kugou_playlist_tracks(playlist_id,track_id) VALUES (?,?)', (row['kugou_alias'], ident))
        else:
            conn.execute("""INSERT OR IGNORE INTO aggregate_tracks SELECT ?,'kugou',?,COALESCE(MAX(position),0)+1
                            FROM aggregate_tracks WHERE playlist_id=?""", (pid, ident, pid))


def create(uid, name, description, refs, *, snapshot=None):
    resources = prepare_tracks(refs)
    with transaction(write=True) as conn:
        if snapshot is not None:
            saved = conn.execute("SELECT * FROM playlists WHERE id=? AND user_id=? AND source='ncm'", (snapshot, uid)).fetchone()
            if saved is None:
                fail(404, 'not_found', '歌单快照不存在')
            name, description, cover = saved['name'], saved['description'], saved['cover']
            refs = [('netease', str(row[0])) for row in conn.execute(
                'SELECT ncm_id FROM playlist_tracks WHERE playlist_id=? ORDER BY seq', (snapshot,))]
        else:
            cover = ''
        now = int(time.time())
        pid = conn.execute('''INSERT INTO playlists(user_id,name,description,cover,created_at,updated_at)
                              VALUES (?,?,?,?,?,?)''', (uid, name, description, cover, now, now)).lastrowid
        conn.execute('UPDATE aggregate_playlists SET legacy_public=0 WHERE playlist_id=?', (pid,))
        _append(conn, owned(conn, uid, pid), refs, resources)
        return {'resource': resource(owned(conn, uid, pid))}


def modify(uid, pid, action, body):
    version = body.get('version')
    if type(version) is not int or version < 1:
        fail(400, 'invalid_input', '需要歌单版本号')
    refs = track_refs(body.get('tracks'), 10000 if action == 'order' else 50, action == 'order') if action in ('add', 'remove', 'order') else []
    with transaction() as conn:
        owned(conn, uid, pid, version)
    resources = prepare_tracks(refs) if action == 'add' else []
    with transaction(write=True) as conn:
        row = owned(conn, uid, pid, version)
        if action == 'delete':
            conn.execute('DELETE FROM playlists WHERE id=?', (pid,))
            conn.execute('DELETE FROM playlist_tracks WHERE playlist_id=?', (pid,))
            if row['kugou_alias'] is not None:
                conn.execute('DELETE FROM kugou_playlist_tracks WHERE playlist_id=?', (row['kugou_alias'],))
            return {'ok': True}
        if action == 'edit':
            name = value_text(body.get('name', row['name']), 60, required=True)
            description = value_text(body.get('description', row['description']), 300)
            if row['kugou_alias'] is not None:
                conn.execute('UPDATE kugou_playlists SET name=?,description=? WHERE id=?', (name, description, row['kugou_alias']))
            else:
                conn.execute('UPDATE playlists SET name=?,description=? WHERE id=?', (name, description, pid))
            if 'shared' in body:
                conn.execute('UPDATE aggregate_playlists SET shared=? WHERE playlist_id=?', (flag(body, 'shared'), pid))
        if action == 'add':
            _append(conn, row, refs, resources)
        if action == 'remove':
            for provider, ident in refs:
                if provider == 'netease':
                    conn.execute('DELETE FROM playlist_tracks WHERE playlist_id=? AND ncm_id=?', (pid, int(ident)))
                elif row['kugou_alias'] is not None:
                    conn.execute('DELETE FROM kugou_playlist_tracks WHERE playlist_id=? AND track_id=?', (row['kugou_alias'], ident))
                else:
                    conn.execute("DELETE FROM aggregate_tracks WHERE playlist_id=? AND provider='kugou' AND track_id=?", (pid, ident))
        if action == 'order':
            existing = {tuple(item) for item in conn.execute('SELECT provider,track_id FROM aggregate_tracks WHERE playlist_id=?', (pid,))}
            if set(refs) != existing or len(body['tracks']) != len(refs):
                fail(400, 'invalid_input', '排序需要每首曲目各出现一次')
            conn.executemany('UPDATE aggregate_tracks SET position=? WHERE playlist_id=? AND provider=? AND track_id=?',
                             [(pos, pid, provider, ident) for pos, (provider, ident) in enumerate(refs, 1)])
        conn.execute('UPDATE aggregate_playlists SET version=version+1 WHERE playlist_id=?', (pid,))
        conn.execute('UPDATE playlists SET updated_at=? WHERE id=?', (int(time.time()), pid))
        return {'resource': resource(owned(conn, uid, pid))}


def value_text(value, maximum, required=False):
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        fail(400, 'invalid_input', '文本为空或长度超出限制')
    return value.strip()


def flag(body, name):
    value = body.get(name)
    if type(value) is not bool:
        fail(400, 'invalid_input', f'{name} 必须是布尔值')
    return value


def list_favorites(uid, offset, limit):
    with transaction() as conn:
        rows = conn.execute("""SELECT provider,track_id FROM (
            SELECT 'netease' AS provider,CAST(ncm_id AS TEXT) AS track_id,created_at AS stamp,rowid AS sequence FROM likes WHERE user_id=?
            UNION ALL SELECT 'kugou',track_id,0,seq FROM kugou_favorites WHERE user_id=?
            ) ORDER BY stamp DESC,sequence DESC,provider LIMIT ? OFFSET ?""", (uid, uid, limit + 1, offset))
        return page(track_rows(conn, [tuple(row) for row in rows]), offset, limit)


def selected_favorites(uid, refs):
    with transaction() as conn:
        return [{'provider': provider, 'id': ident} for provider, ident in refs if conn.execute(
            'SELECT 1 FROM likes WHERE user_id=? AND ncm_id=?' if provider == 'netease'
            else 'SELECT 1 FROM kugou_favorites WHERE user_id=? AND track_id=?', (uid, ident)).fetchone()]


def set_favorite(uid, ref, selected):
    provider, ident = ref
    resources = prepare_tracks([ref]) if selected else []
    with transaction(write=True) as conn:
        if provider == 'netease':
            if selected:
                conn.execute('INSERT OR IGNORE INTO likes(user_id,ncm_id,created_at) VALUES (?,?,?)', (uid, int(ident), int(time.time())))
            else:
                conn.execute('DELETE FROM likes WHERE user_id=? AND ncm_id=?', (uid, int(ident)))
        elif selected:
            cm_kugou_library._save_resources(conn, resources)
            conn.execute('INSERT OR IGNORE INTO kugou_favorites(user_id,track_id) VALUES (?,?)', (uid, ident))
        else:
            conn.execute('DELETE FROM kugou_favorites WHERE user_id=? AND track_id=?', (uid, ident))
    return {'selected': selected}
