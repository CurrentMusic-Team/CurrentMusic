"""Personal CurrentMusic data for the KuGou catalogue."""

from contextlib import closing
import json
import re

import cm_db
import cm_kugou as kg


SCHEMA = """
CREATE TABLE IF NOT EXISTS kugou_tracks (
  id TEXT PRIMARY KEY,
  resource TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kugou_favorites (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  track_id TEXT NOT NULL REFERENCES kugou_tracks(id),
  UNIQUE (user_id, track_id)
);
CREATE INDEX IF NOT EXISTS idx_kugou_favorite_user ON kugou_favorites(user_id, seq DESC);
CREATE TABLE IF NOT EXISTS kugou_playlists (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_kugou_playlist_user ON kugou_playlists(user_id, id DESC);
CREATE TABLE IF NOT EXISTS kugou_playlist_tracks (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  playlist_id INTEGER NOT NULL REFERENCES kugou_playlists(id) ON DELETE CASCADE,
  track_id TEXT NOT NULL REFERENCES kugou_tracks(id),
  UNIQUE (playlist_id, track_id)
);
CREATE TABLE IF NOT EXISTS kugou_plays (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  track_id TEXT NOT NULL REFERENCES kugou_tracks(id),
  elapsed_ms INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_kugou_recent ON kugou_plays(user_id, track_id, id DESC);
CREATE TABLE IF NOT EXISTS kugou_roaming_skips (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  track_id TEXT NOT NULL,
  UNIQUE (user_id, track_id)
);
CREATE INDEX IF NOT EXISTS idx_kugou_roaming_skip_user ON kugou_roaming_skips(user_id, id DESC);
"""


def _connection():
    conn = cm_db.connect()
    conn.execute("PRAGMA foreign_keys=ON")
    return closing(conn)


def _save_resources(conn, rows):
    conn.executemany("INSERT OR IGNORE INTO kugou_tracks(id, resource) VALUES(?, ?)",
                     [(row["id"], json.dumps(row, ensure_ascii=False)) for row in rows])


def _new_resources(ids):
    ids = list(dict.fromkeys(ids))
    marks = ",".join("?" for _ in ids)
    with _connection() as conn:
        known = {row[0] for row in conn.execute(f"SELECT id FROM kugou_tracks WHERE id IN ({marks})", ids)}
    missing = [ident for ident in ids if ident not in known]
    return kg.tracks(missing) if missing else []


def _page(rows, offset, limit, normalize):
    return {"items": [normalize(row) for row in rows[:limit]],
            "nextOffset": offset + limit if len(rows) > limit else None}


def _track(row):
    return json.loads(row["resource"])


def _playlist(row):
    return {"id": f"kgp_{row['id']}", "kind": "playlist", "name": row["name"], "description": row["description"],
            "trackCount": row["track_count"], "artwork": "", "artists": "", "album": "", "durationMs": 0, "videoId": ""}


def _playlist_id(value):
    if not re.fullmatch(r"kgp_[1-9][0-9]{0,18}", value) or int(value[4:]) >= 2**63:
        raise kg.Error(400, "invalid_input", "酷狗个人歌单 ID 格式错误")
    return int(value[4:])


def _owned(conn, uid, pid):
    row = conn.execute("""SELECT p.*, (SELECT COUNT(*) FROM kugou_playlist_tracks t WHERE t.playlist_id=p.id)
                          AS track_count FROM kugou_playlists p WHERE id=? AND user_id=?""", (pid, uid)).fetchone()
    if not row:
        raise kg.Error(404, "not_found", "歌单不存在")
    return row


def favorites(uid, offset, limit):
    with _connection() as conn:
        conn.execute("BEGIN")
        rows = conn.execute("""SELECT k.resource FROM kugou_favorites f JOIN kugou_tracks k ON k.id=f.track_id
                              WHERE f.user_id=? ORDER BY f.seq DESC LIMIT ? OFFSET ?""",
                            (uid, limit + 1, offset)).fetchall()
        result = _page(rows, offset, limit, _track)
        result["total"] = conn.execute("SELECT COUNT(*) FROM kugou_favorites WHERE user_id=?", (uid,)).fetchone()[0]
        return result


def favorite_status(uid, ids):
    marks = ",".join("?" for _ in ids)
    with _connection() as conn:
        selected = conn.execute(f"SELECT track_id FROM kugou_favorites WHERE user_id=? AND track_id IN ({marks})",
                                (uid, *ids)).fetchall()
    return {"selectedIds": [row[0] for row in selected]}


def favorite_set(uid, ident, selected):
    rows = _new_resources([ident]) if selected else []
    with _connection() as conn, conn:
        if selected:
            _save_resources(conn, rows)
            conn.execute("INSERT OR IGNORE INTO kugou_favorites(user_id, track_id) VALUES(?, ?)", (uid, ident))
        else:
            conn.execute("DELETE FROM kugou_favorites WHERE user_id=? AND track_id=?", (uid, ident))
    return {"selected": selected}


def playlists(uid, offset, limit):
    with _connection() as conn:
        rows = conn.execute("""SELECT p.*, (SELECT COUNT(*) FROM kugou_playlist_tracks t WHERE t.playlist_id=p.id)
                               AS track_count FROM kugou_playlists p WHERE user_id=? ORDER BY id DESC LIMIT ? OFFSET ?""",
                            (uid, limit + 1, offset)).fetchall()
    return _page(rows, offset, limit, _playlist)


def playlist(uid, ident, offset, limit):
    pid = _playlist_id(ident)
    with _connection() as conn:
        conn.execute("BEGIN")
        resource = _playlist(_owned(conn, uid, pid))
        rows = conn.execute("""SELECT k.resource FROM kugou_playlist_tracks t JOIN kugou_tracks k ON k.id=t.track_id
                               WHERE t.playlist_id=? ORDER BY t.seq LIMIT ? OFFSET ?""",
                            (pid, limit + 1, offset)).fetchall()
    page = _page(rows, offset, limit, _track)
    return {"resource": resource, "tracks": page["items"], "nextOffset": page["nextOffset"]}


def playlist_create(uid, name, description, ids):
    tracks = _new_resources(ids) if ids else []
    with _connection() as conn, conn:
        pid = conn.execute("INSERT INTO kugou_playlists(user_id, name, description) VALUES(?, ?, ?)",
                           (uid, name, description)).lastrowid
        _save_resources(conn, tracks)
        _add_tracks(conn, pid, ids)
        return {"resource": _playlist(_owned(conn, uid, pid))}


def playlist_edit(uid, ident, name, description):
    pid = _playlist_id(ident)
    with _connection() as conn, conn:
        result = conn.execute("""UPDATE kugou_playlists SET name=?, description=COALESCE(?, description)
                                 WHERE id=? AND user_id=?""", (name, description, pid, uid))
        if not result.rowcount:
            raise kg.Error(404, "not_found", "歌单不存在")
        return {"resource": _playlist(_owned(conn, uid, pid))}


def playlist_delete(uid, ident):
    with _connection() as conn, conn:
        result = conn.execute("DELETE FROM kugou_playlists WHERE id=? AND user_id=?", (_playlist_id(ident), uid))
        if not result.rowcount:
            raise kg.Error(404, "not_found", "歌单不存在")
    return {"ok": True}


def _add_tracks(conn, pid, ids):
    conn.executemany("INSERT OR IGNORE INTO kugou_playlist_tracks(playlist_id, track_id) VALUES(?, ?)",
                     [(pid, ident) for ident in ids])


def playlist_tracks(uid, ident, ids, selected):
    pid = _playlist_id(ident)
    # Validate ownership before spending time on upstream metadata. Recheck under the write lock.
    with _connection() as conn:
        _owned(conn, uid, pid)
    rows = _new_resources(ids) if selected else []
    with _connection() as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        _owned(conn, uid, pid)
        if selected:
            _save_resources(conn, rows)
            _add_tracks(conn, pid, ids)
        else:
            conn.executemany("DELETE FROM kugou_playlist_tracks WHERE playlist_id=? AND track_id=?",
                             [(pid, track) for track in ids])
    return {"ok": True}


def recent(uid, offset, limit):
    with _connection() as conn:
        rows = conn.execute("""SELECT t.resource FROM (
                                  SELECT track_id, MAX(id) AS last_id FROM kugou_plays
                                  WHERE user_id=? GROUP BY track_id ORDER BY last_id DESC LIMIT ? OFFSET ?
                               ) AS p JOIN kugou_tracks AS t ON t.id=p.track_id ORDER BY p.last_id DESC""",
                            (uid, limit + 1, offset)).fetchall()
    return _page(rows, offset, limit, _track)


def record(uid, ident, play_id, elapsed_ms):
    if play_id is None:
        tracks = _new_resources([ident])
        with _connection() as conn, conn:
            _save_resources(conn, tracks)
            play_id = conn.execute("INSERT INTO kugou_plays(user_id, track_id) VALUES(?, ?)",
                                   (uid, ident)).lastrowid
    else:
        with _connection() as conn, conn:
            result = conn.execute("""UPDATE kugou_plays SET elapsed_ms=MAX(elapsed_ms, ?)
                                     WHERE id=? AND user_id=? AND track_id=?""", (elapsed_ms, play_id, uid, ident))
            if not result.rowcount:
                raise kg.Error(404, "not_found", "播放记录不存在")
    return {"ok": True, "playId": str(play_id)}



def roaming_exclusions(uid):
    with _connection() as conn:
        return {row[0] for row in conn.execute("SELECT track_id FROM kugou_roaming_skips WHERE user_id=?", (uid,))}


def roaming_skip(uid, ident):
    with _connection() as conn, conn:
        conn.execute("INSERT OR IGNORE INTO kugou_roaming_skips(user_id, track_id) VALUES (?, ?)", (uid, ident))
        conn.execute("""DELETE FROM kugou_roaming_skips WHERE user_id=? AND id NOT IN
                       (SELECT id FROM kugou_roaming_skips WHERE user_id=? ORDER BY id DESC LIMIT 500)""", (uid, uid))
    return {"ok": True}
