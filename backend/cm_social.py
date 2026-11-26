"""CurrentMusic friendships, private conversations and world chat membership."""
from __future__ import annotations

import base64
from contextlib import contextmanager
import json
import re
import secrets
import time

import cm_chat
import cm_db

WORLD = 1
WORLD_NAME = "世界聊天"
WORLD_DESCRIPTION = "流声音乐官方公共聊天室"
ACTIVE_ACCOUNT = "u.disabled=0 AND u.is_bot=0 AND u.username!='__internal__'"
IMAGE_TYPES = {"jpg": "image/jpeg", "png": "image/png", "gif": "image/gif", "webp": "image/webp"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS social_conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '',
    announcement TEXT NOT NULL DEFAULT '', owner_id INTEGER NOT NULL DEFAULT 0,
    low_id INTEGER, high_id INTEGER, created_at INTEGER NOT NULL,
    UNIQUE(low_id, high_id)
);
CREATE INDEX IF NOT EXISTS social_direct_high ON social_conversations(high_id) WHERE kind='direct';
CREATE TABLE IF NOT EXISTS social_members (
    conversation_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
    role TEXT NOT NULL DEFAULT 'member', removed INTEGER NOT NULL DEFAULT 0,
    muted_until INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(conversation_id,user_id)
);
CREATE INDEX IF NOT EXISTS social_members_user ON social_members(user_id,removed,conversation_id);
CREATE TABLE IF NOT EXISTS social_friends (
    low_id INTEGER NOT NULL, high_id INTEGER NOT NULL, requester_id INTEGER NOT NULL,
    state TEXT NOT NULL, accepted_at INTEGER NOT NULL DEFAULT 0, updated_at INTEGER NOT NULL,
    PRIMARY KEY(low_id,high_id)
);
CREATE INDEX IF NOT EXISTS social_friends_high ON social_friends(high_id,state,updated_at);
CREATE INDEX IF NOT EXISTS social_friends_requests ON social_friends(requester_id,updated_at);
CREATE TABLE IF NOT EXISTS social_invites (
    conversation_id INTEGER NOT NULL, user_id INTEGER NOT NULL, sender_id INTEGER NOT NULL,
    state TEXT NOT NULL, updated_at INTEGER NOT NULL, PRIMARY KEY(conversation_id,user_id)
);
CREATE INDEX IF NOT EXISTS social_invites_user ON social_invites(user_id,state,updated_at);
CREATE TABLE IF NOT EXISTS social_reads (
    conversation_id INTEGER NOT NULL, user_id INTEGER NOT NULL, message_id INTEGER NOT NULL,
    PRIMARY KEY(conversation_id,user_id)
);
CREATE TABLE IF NOT EXISTS social_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL, client_id TEXT,
    content TEXT NOT NULL, image TEXT NOT NULL, mentions TEXT NOT NULL, atts TEXT NOT NULL,
    quote INTEGER NOT NULL, mention_all INTEGER NOT NULL, deleted INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL, UNIQUE(conversation_id,user_id,client_id)
);
CREATE INDEX IF NOT EXISTS social_messages_history ON social_messages(conversation_id,deleted,id);
CREATE TABLE IF NOT EXISTS social_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id INTEGER NOT NULL,
    type TEXT NOT NULL, message_id INTEGER NOT NULL, created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS social_events_conversation ON social_events(conversation_id,seq);
CREATE TABLE IF NOT EXISTS social_images (
    id TEXT PRIMARY KEY, conversation_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
    created_at INTEGER NOT NULL, type TEXT NOT NULL, data BLOB NOT NULL,
    message_id INTEGER
);
CREATE INDEX IF NOT EXISTS social_images_expiry ON social_images(message_id,created_at);
CREATE TABLE IF NOT EXISTS social_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT, reporter_id INTEGER NOT NULL, target_id INTEGER NOT NULL,
    reason TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', created_at INTEGER NOT NULL,
    reviewed_by INTEGER, reviewed_at INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS social_reports_pending
    ON social_reports(reporter_id,target_id) WHERE status='pending';
CREATE INDEX IF NOT EXISTS social_reports_queue ON social_reports(status,id);
"""


class Error(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def init_tables(conn):
    conn.executescript(SCHEMA)
    with conn:
        conn.execute("""INSERT OR IGNORE INTO social_conversations(id,kind,name,description,created_at)
                        VALUES(?, 'world', ?, ?, ?)""", (WORLD, WORLD_NAME, WORLD_DESCRIPTION, now()))


@contextmanager
def transaction(write=False):
    conn = cm_db.connect()
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield conn
    finally:
        conn.close()


def now():
    return int(time.time())


def seed_world_roles(owner_name, admin_name):
    """Initialize one installation from exact existing login names."""
    with transaction(write=True) as db:
        room = conversation(db, WORLD)
        ids = [db.execute(f"SELECT u.id FROM users u WHERE {ACTIVE_ACCOUNT} AND u.username=?",
                          (name,)).fetchone() for name in (owner_name, admin_name)]
        if not all(ids) or ids[0][0] == ids[1][0]:
            raise Error(400, "两个登录名必须对应不同的启用账号")
        owner, admin = (row[0] for row in ids)
        if room["owner_id"]:
            if room["owner_id"] != owner or membership(db, WORLD, admin)["role"] != "admin":
                raise Error(409, "世界聊天已完成初始化，请通过群管理调整角色")
            return
        db.execute("UPDATE social_conversations SET owner_id=? WHERE id=?", (owner, WORLD))
        set_member(db, WORLD, owner, "owner")
        set_member(db, WORLD, admin, "admin")


def conversation(db, cid):
    row = db.execute("SELECT * FROM social_conversations WHERE id=?", (cid,)).fetchone()
    if not row:
        raise Error(404, "对话不存在")
    return row


def membership(db, cid, uid):
    row = db.execute("SELECT * FROM social_members WHERE conversation_id=? AND user_id=?",
                     (cid, uid)).fetchone()
    if row:
        return dict(row)
    return {"role": "member", "removed": 0, "muted_until": 0} if cid == WORLD and uid else {
        "role": "", "removed": 0, "muted_until": 0,
    }


def set_member(db, cid, uid, role):
    db.execute("""INSERT INTO social_members(conversation_id,user_id,role) VALUES(?,?,?)
                  ON CONFLICT(conversation_id,user_id) DO UPDATE
                  SET role=excluded.role, removed=0, muted_until=0""", (cid, uid, role))


def account(db, uid, discoverable=False):
    privacy = " AND u.public_square=1" if discoverable else ""
    return db.execute(f"SELECT {cm_chat._USER_FIELDS} FROM users u WHERE u.id=? AND {ACTIVE_ACCOUNT}{privacy}",
                      (uid,)).fetchone()


def person(db, uid):
    row = db.execute(f"SELECT {cm_chat._USER_FIELDS} FROM users u WHERE u.id=?", (uid,)).fetchone()
    return cm_chat._user_out(row) if row else {"id": uid, "nickname": "已注销用户"}


def pair(a, b):
    return (a, b) if a < b else (b, a)


def relation(db, a, b):
    return db.execute("SELECT * FROM social_friends WHERE low_id=? AND high_id=?", pair(a, b)).fetchone()


def friends(db, a, b):
    row = relation(db, a, b)
    return bool(row and row["state"] == "accepted")


def other(room, uid):
    if room["kind"] != "direct" or uid not in (room["low_id"], room["high_id"]):
        raise Error(404, "对话不存在")
    return room["high_id"] if uid == room["low_id"] else room["low_id"]


def world_permissions(db, user, *, sending=False, staff=False):
    uid = user["id"]
    member = membership(db, WORLD, uid)
    if member["removed"] or user.get("disabled"):
        raise Error(403, "你已无法参与世界聊天")
    if sending and member["muted_until"] > now():
        raise Error(403, "你正在被禁言")
    return staff or member["role"] in ("owner", "admin")


def access(db, room, uid, *, sending=False):
    cid = room["id"]
    member = membership(db, cid, uid)
    if room["kind"] == "direct":
        peer = other(room, uid)
        if sending and (not friends(db, uid, peer) or not account(db, peer)):
            raise Error(403, "当前无法向对方发送私聊")
    elif cid != WORLD and (not member["role"] or member["removed"]):
        raise Error(404, "对话不存在")
    if sending and (member["removed"] or member["muted_until"] > now()):
        raise Error(403, "当前无法在这个群发送消息")
    return member


def manager(db, room, uid):
    member = access(db, room, uid)
    if room["kind"] == "direct" or member["removed"] or member["role"] not in ("owner", "admin"):
        raise Error(403, "需要群管理权限")
    return member["role"]


def integer(value, default=0, maximum=2**63-1):
    if value is None or value == "":
        return default
    if isinstance(value, bool) or not isinstance(value, (int, str)) or not re.fullmatch(r"[0-9]{1,19}", str(value)):
        raise Error(400, "数字参数无效")
    result = int(value)
    if result > maximum:
        raise Error(400, "数字参数超出范围")
    return result


def text(value, cap, label, required=False):
    if not isinstance(value, str):
        raise Error(400, f"{label}格式无效")
    result = value.strip()
    if len(result) > cap or (required and not result) or any(ord(c) < 32 and c != "\n" for c in result):
        raise Error(400, f"{label}长度或内容无效")
    return result


def encode(value):
    return json.dumps(value, ensure_ascii=False)


def sender_out(db, row, people):
    mentions = json.loads(row["mentions"] or "[]")
    quote = None
    if row["quote"]:
        quoted = db.execute(f"""SELECT m.*, {cm_db.PUBLIC_NICKNAME} AS nickname
                                FROM social_messages m LEFT JOIN users u ON m.user_id=u.id
                                WHERE m.conversation_id=? AND m.id=?""",
                            (row["conversation_id"], row["quote"])).fetchone()
        quote = cm_chat._quote_summary(quoted) if quoted else None
    return cm_chat._row_out(dict(row), people.get(row["user_id"]),
                            [people[i] for i in mentions if i in people], quote=quote)


def messages_out(db, rows):
    ids = {uid for row in rows for uid in [row["user_id"], *json.loads(row["mentions"] or "[]")]}
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    people = {r["id"]: r for r in db.execute(
        f"SELECT {cm_chat._USER_FIELDS} FROM users u WHERE u.id IN ({placeholders})", tuple(ids))}
    return [sender_out(db, row, people) for row in rows]


def summary(db, room, uid, staff=False):
    cid, kind = room["id"], room["kind"]
    member = access(db, room, uid)
    peer = person(db, other(room, uid)) if kind == "direct" else None
    can_send = bool(uid and not member["removed"] and member["muted_until"] <= now())
    if can_send and peer:
        can_send = friends(db, uid, peer["id"]) and bool(account(db, peer["id"]))
    scope = "deleted=0" if cid == WORLD else "deleted=0 AND conversation_id=?"
    args = () if cid == WORLD else (cid,)
    table = "chat_messages" if cid == WORLD else "social_messages"
    last = db.execute(f"SELECT * FROM {table} WHERE {scope} ORDER BY id DESC LIMIT 1", args).fetchone()
    unread = mention = 0
    if uid and last and not member["removed"]:
        read = db.execute("SELECT message_id FROM social_reads WHERE conversation_id=? AND user_id=?",
                          (cid, uid)).fetchone()
        unread, mention = db.execute(f"""SELECT COUNT(*), COALESCE(MAX(mention_all=1 OR EXISTS(
               SELECT 1 FROM json_each(CASE WHEN json_valid(mentions) THEN mentions ELSE '[]' END)
               WHERE value=?)),0) FROM {table} WHERE {scope} AND id>? AND user_id!=?""",
                                     (uid, *args, read[0] if read else 0, uid)).fetchone()
    return {"id": cid, "kind": kind, "userId": uid, "name": peer["nickname"] if peer else room["name"],
            "peer": peer, "description": room["description"], "announcement": room["announcement"],
            "role": "" if member["removed"] else member["role"], "ownerId": room["owner_id"],
            "canSend": can_send, "canDelete": bool(uid and not member["removed"]), "removed": bool(member["removed"]), "mutedUntil": member["muted_until"],
            "canModerate": bool(uid and not member["removed"] and (
                member["role"] in ("owner", "admin") or cid == WORLD and staff)),
            "unread": unread, "mentioned": bool(mention),
            "last": {"id": last["id"], "preview": cm_chat._quote_summary(
                dict(last, nickname=""))["excerpt"], "createdAt": last["created_at"]} if last else None}


def summaries(db, uid, staff):
    rooms = []
    if uid:
        rooms = db.execute("""SELECT c.* FROM social_conversations c
            WHERE (c.kind='direct' AND (c.low_id=? OR c.high_id=?))
               OR (c.kind='group' AND EXISTS (SELECT 1 FROM social_members m
                   WHERE m.conversation_id=c.id AND m.user_id=? AND m.removed=0))""", (uid, uid, uid))
    items = [summary(db, room, uid, staff) for room in rooms]
    items.sort(key=lambda item: ((item["last"] or {}).get("createdAt", 0), item["id"]), reverse=True)
    return [summary(db, conversation(db, WORLD), uid, staff), *items]


def directory(db, query, offset):
    if not query:
        return []
    rows = db.execute(f"""SELECT {cm_chat._USER_FIELDS} FROM users u
        WHERE {ACTIVE_ACCOUNT} AND u.public_square=1 AND {cm_db.PUBLIC_NICKNAME} LIKE ? ESCAPE '\\'
        ORDER BY {cm_db.PUBLIC_NICKNAME},u.id LIMIT 31 OFFSET ?""", (cm_chat.like_pattern(query), offset))
    return [cm_chat._user_out(row) for row in rows]


def members(db, room, uid, query, offset, *, removed=False):
    cid = room["id"]
    current = access(db, room, uid)
    if removed:
        manager(db, room, uid)
    if room["kind"] == "direct":
        restriction = "u.id IN (?,?)"
        params = [room["low_id"], room["high_id"]]
    elif cid == WORLD and not removed:
        restriction = "(m.removed IS NULL OR m.removed=0)"
        visibility = "u.public_square=1 OR COALESCE(m.role,'member') IN ('owner','admin')"
        if current["role"] in ("owner", "admin"):
            visibility += " OR EXISTS (SELECT 1 FROM chat_messages cm WHERE cm.user_id=u.id)"
        restriction += f" AND ({visibility})"
        params = []
    else:
        restriction = "m.removed=?"
        params = [int(removed)]
    rows = db.execute(f"""SELECT {cm_chat._USER_FIELDS}, COALESCE(m.role,'member') AS group_role,
          COALESCE(m.muted_until,0) AS muted_until
        FROM users u LEFT JOIN social_members m ON m.user_id=u.id AND m.conversation_id=?
        WHERE {ACTIVE_ACCOUNT} AND {restriction} AND {cm_db.PUBLIC_NICKNAME} LIKE ? ESCAPE '\\'
        ORDER BY CASE m.role WHEN 'owner' THEN 0 WHEN 'admin' THEN 1 ELSE 2 END, u.id DESC
        LIMIT 31 OFFSET ?""", (cid, *params, cm_chat.like_pattern(query), offset))
    return [dict(cm_chat._user_out(row), role=row["group_role"], mutedUntil=row["muted_until"]) for row in rows]


def world_members(query, limit):
    with transaction() as db:
        eligibility = """u.disabled=0 AND u.username!='__internal__'
                         AND (u.public_square=1 OR u.is_bot=1) AND COALESCE(m.removed,0)=0"""
        if query:
            rows = db.execute(f"""SELECT {cm_chat._USER_FIELDS} FROM users u
                LEFT JOIN social_members m ON m.user_id=u.id AND m.conversation_id=?
                WHERE {eligibility} AND {cm_db.PUBLIC_NICKNAME} LIKE ? ESCAPE '\\'
                ORDER BY CASE WHEN {cm_db.PUBLIC_NICKNAME} LIKE ? ESCAPE '\\' THEN 0 ELSE 1 END, u.id DESC
                LIMIT ?""", (WORLD, cm_chat.like_pattern(query), cm_chat.like_pattern(query, prefix_only=True), limit))
        else:
            rows = db.execute(f"""WITH recent AS (
                SELECT user_id,MAX(created_at) AS last_at FROM chat_messages WHERE created_at>?
                    AND deleted=0 GROUP BY user_id)
                SELECT {cm_chat._USER_FIELDS} FROM users u
                LEFT JOIN social_members m ON m.user_id=u.id AND m.conversation_id=?
                LEFT JOIN recent r ON r.user_id=u.id
                WHERE {eligibility} ORDER BY r.last_at DESC,u.id DESC LIMIT ?""",
                              (now() - 7 * 86400, WORLD, limit))
        return [cm_chat._user_out(row) for row in rows]


def contact_out(db, row, uid):
    peer = row["high_id"] if row["low_id"] == uid else row["low_id"]
    state = row["state"]
    if state == "pending":
        state = "outgoing" if row["requester_id"] == uid else "incoming"
    return {"user": person(db, peer), "state": state, "canReport": bool(row["accepted_at"])}


def contacts(db, uid):
    rows = db.execute("""SELECT * FROM social_friends WHERE (low_id=? OR high_id=?)
         AND (state IN ('accepted','pending') OR accepted_at>0) ORDER BY updated_at DESC""", (uid, uid))
    return [contact_out(db, row, uid) for row in rows]


def contact_change(db, uid, peer, action):
    if uid == peer:
        raise Error(400, "请选择其他用户")
    row = relation(db, uid, peer)
    state = row["state"] if row else ""
    requested = row["requester_id"] if row else uid
    accepted = row["accepted_at"] if row else 0
    stamped = now()
    if action == "request":
        if state in ("pending", "accepted"):
            return contact_out(db, row, uid)
        if not account(db, peer, discoverable=not accepted):
            raise Error(404, "找不到可添加的用户")
        count = db.execute("SELECT COUNT(*) FROM social_friends WHERE requester_id=? AND updated_at>=?",
                           (uid, stamped - 86400)).fetchone()[0]
        if count >= 30:
            raise Error(429, "今日好友申请已达上限")
        if row and state == "declined" and requested == uid and row["updated_at"] + 86400 > stamped:
            raise Error(429, "请稍后再发送好友申请")
        state, requested = "pending", uid
    elif action in ("accept", "decline"):
        wanted = "accepted" if action == "accept" else "declined"
        if state == wanted and requested == peer:
            return contact_out(db, row, uid)
        if state != "pending" or requested != peer:
            raise Error(409, "这条好友申请已处理")
        if action == "accept" and not account(db, peer):
            raise Error(404, "对方账号不可用")
        state = wanted
        if action == "accept":
            accepted = accepted or stamped
    elif action == "cancel" and state == "pending" and requested == uid:
        state = "cancelled"
    elif action == "remove" and state == "accepted":
        state = "removed"
    elif state in ("cancelled", "removed") and (
            action == "cancel" and state == "cancelled" and requested == uid or
            action == "remove" and state == "removed"):
        return contact_out(db, row, uid)
    else:
        raise Error(409, "当前好友状态不支持此操作")
    db.execute("""INSERT INTO social_friends VALUES(?,?,?,?,?,?) ON CONFLICT(low_id,high_id) DO UPDATE
        SET requester_id=excluded.requester_id,state=excluded.state,accepted_at=excluded.accepted_at,
            updated_at=excluded.updated_at""", (*pair(uid, peer), requested, state, accepted, stamped))
    return contact_out(db, relation(db, uid, peer), uid)


def open_direct(db, uid, peer):
    if uid == peer:
        raise Error(400, "请选择其他用户")
    ids = pair(uid, peer)
    existing = db.execute("SELECT * FROM social_conversations WHERE low_id=? AND high_id=?", ids).fetchone()
    if existing:
        return existing
    if not friends(db, uid, peer) or not account(db, peer):
        raise Error(403, "成为好友后可以发起私聊")
    result = db.execute("""INSERT INTO social_conversations(kind,low_id,high_id,created_at)
                           VALUES('direct',?,?,?)""", (*ids, now()))
    return conversation(db, result.lastrowid)


def create_group(db, uid, body):
    name = text(body.get("name"), 50, "群名称", required=True)
    description = text(body.get("description", ""), 300, "群简介")
    result = db.execute("""INSERT INTO social_conversations(kind,name,description,owner_id,created_at)
                           VALUES('group',?,?,?,?)""", (name, description, uid, now()))
    cid = result.lastrowid
    set_member(db, cid, uid, "owner")
    return conversation(db, cid)


def update_group(db, room, uid, body):
    manager(db, room, uid)
    desc = text(body["description"], 300, "群简介") if "description" in body else room["description"]
    announcement = text(body["announcement"], 1000, "群公告") if "announcement" in body else room["announcement"]
    db.execute("UPDATE social_conversations SET description=?,announcement=? WHERE id=?",
               (desc, announcement, room["id"]))
    return conversation(db, room["id"])


def group_invite(db, room, uid, peer):
    cid = room["id"]
    manager(db, room, uid)
    if not account(db, peer) or (cid != WORLD and not friends(db, uid, peer)):
        raise Error(403, "只能邀请可用的好友")
    target = membership(db, cid, peer)
    if target["role"] and not target["removed"]:
        raise Error(409, "对方已在群内")
    db.execute("""INSERT INTO social_invites VALUES(?,?,?,'pending',?)
        ON CONFLICT(conversation_id,user_id) DO UPDATE SET sender_id=excluded.sender_id,
        state=excluded.state,updated_at=excluded.updated_at WHERE social_invites.state!='pending'""",
               (cid, peer, uid, now()))


def inbox(db, uid):
    return [{"id": row["id"], "name": row["name"], "sender": person(db, row["sender_id"])}
            for row in db.execute("""SELECT c.id,c.name,i.sender_id FROM social_invites i
                JOIN social_conversations c ON c.id=i.conversation_id
                WHERE i.user_id=? AND i.state='pending' ORDER BY i.updated_at DESC""", (uid,))]


def answer_invite(db, cid, uid, answer):
    row = db.execute("SELECT * FROM social_invites WHERE conversation_id=? AND user_id=?",
                     (cid, uid)).fetchone()
    if not row or row["state"] not in ("pending", answer):
        raise Error(404, "邀请不存在或已处理")
    if row["state"] == answer:
        return
    if answer == "accepted":
        set_member(db, cid, uid, "member")
    db.execute("UPDATE social_invites SET state=?,updated_at=? WHERE conversation_id=? AND user_id=?",
               (answer, now(), cid, uid))


def member_change(db, room, uid, peer, body):
    cid = room["id"]
    action = body.get("action")
    role = manager(db, room, uid)
    target = membership(db, cid, peer)
    if not account(db, peer) or not target["role"]:
        raise Error(404, "群成员不存在")
    if uid == peer or target["role"] == "owner" or (role != "owner" and target["role"] == "admin"):
        raise Error(403, "无法管理这位成员")
    if target["removed"]:
        if action == "remove":
            return
        raise Error(404, "群成员不存在")
    if action in ("promote", "demote") and role == "owner":
        db.execute("""INSERT INTO social_members(conversation_id,user_id,role) VALUES(?,?,?)
            ON CONFLICT(conversation_id,user_id) DO UPDATE SET role=excluded.role""",
                   (cid, peer, "admin" if action == "promote" else "member"))
    elif action in ("mute", "unmute"):
        duration = integer(body.get("seconds"), maximum=30 * 86400) if action == "mute" else 0
        if action == "mute" and not duration:
            raise Error(400, "请选择禁言时长")
        db.execute("""INSERT INTO social_members(conversation_id,user_id,muted_until) VALUES(?,?,?)
            ON CONFLICT(conversation_id,user_id) DO UPDATE SET muted_until=excluded.muted_until""",
                   (cid, peer, now() + duration if duration else 0))
    elif action == "remove":
        remove_member(db, cid, peer)
    else:
        raise Error(400, "不支持的群管理操作")


def remove_member(db, cid, uid):
    db.execute("""INSERT INTO social_members(conversation_id,user_id,removed) VALUES(?,?,1)
                  ON CONFLICT(conversation_id,user_id) DO UPDATE SET removed=1,role='member',muted_until=0""",
               (cid, uid))
    db.execute("UPDATE social_invites SET state='revoked' WHERE conversation_id=? AND user_id=?", (cid, uid))


def leave(db, room, uid):
    member = membership(db, room["id"], uid)
    if room["kind"] == "group" and not member["role"]:
        raise Error(404, "对话不存在")
    if room["kind"] != "group" or member["role"] == "owner":
        raise Error(403, "当前身份无法退出此群")
    if member["removed"]:
        return
    remove_member(db, room["id"], uid)


def list_messages(db, room, before, limit):
    rows = db.execute("""SELECT * FROM social_messages
        WHERE conversation_id=? AND deleted=0 AND (?=0 OR id<?) ORDER BY id DESC LIMIT ?""",
                      (room["id"], before, before, limit)).fetchall()
    return messages_out(db, rows[::-1])


def events(db, cid, since):
    oldest, latest = db.execute("SELECT COALESCE(MIN(seq),0),COALESCE(MAX(seq),0) FROM social_events "
                                "WHERE conversation_id=?", (cid,)).fetchone()
    if since and ((oldest and since < oldest) or since > latest):
        return {"events": [], "latestSeq": latest, "reset": True}
    rows = db.execute("""SELECT seq,type,message_id FROM social_events
                         WHERE conversation_id=? AND seq>? ORDER BY seq LIMIT 100""", (cid, since)).fetchall()
    ids = [row["message_id"] for row in rows if row["type"] == "message"]
    by_id = {}
    if ids:
        marks = ",".join("?" for _ in ids)
        by_id = {item["id"]: item for item in messages_out(db, db.execute(
            f"SELECT * FROM social_messages WHERE conversation_id=? AND id IN ({marks}) AND deleted=0",
            (cid, *ids)).fetchall())}
    return {"latestSeq": latest, "events": [
        {"seq": row["seq"], "type": row["type"], "payload": by_id.get(row["message_id"], {"id": row["message_id"]})}
        for row in rows if row["type"] != "message" or row["message_id"] in by_id
    ], "cursor": rows[-1]["seq"] if rows else latest}


def emit(db, cid, kind, mid):
    db.execute("INSERT INTO social_events(conversation_id,type,message_id,created_at) VALUES(?,?,?,?)",
               (cid, kind, mid, now()))
    oldest = db.execute("""SELECT seq FROM social_events WHERE conversation_id=?
                         ORDER BY seq DESC LIMIT 1 OFFSET 500""", (cid,)).fetchone()
    if oldest:
        db.execute("DELETE FROM social_events WHERE conversation_id=? AND seq<=?", (cid, oldest[0]))


def send_private(db, room, uid, body):
    cid = room["id"]
    role = access(db, room, uid, sending=True)["role"]
    client = body.get("clientId")
    if client is not None:
        if not isinstance(client, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", client):
            raise Error(400, "发言标识无效")
        existing = db.execute("""SELECT * FROM social_messages
            WHERE conversation_id=? AND user_id=? AND client_id=?""", (cid, uid, client)).fetchone()
        if existing:
            if existing["deleted"]:
                raise Error(409, "这条消息已删除")
            return messages_out(db, [existing])[0]
    valid, content = cm_chat.validate_content(body.get("content"))
    atts = cm_chat.parse_atts(body.get("atts"))
    image = body.get("image") or ""
    if not valid or not isinstance(image, str) or not (content or image or atts):
        raise Error(400, "请输入消息或选择附件")
    quote = integer(body.get("quoteId"))
    if quote and not db.execute("SELECT 1 FROM social_messages WHERE id=? AND conversation_id=? AND deleted=0",
                                (quote, cid)).fetchone():
        raise Error(400, "引用的消息不存在或已删除")
    if image and not db.execute("""SELECT 1 FROM social_images WHERE id=? AND conversation_id=?
                            AND user_id=? AND message_id IS NULL AND created_at>?""",
                                (image, cid, uid, now() - 86400)).fetchone():
        raise Error(400, "图片引用无效或已过期")
    mention_all = body.get("mentionAll", False)
    if not isinstance(mention_all, bool):
        raise Error(400, "提及参数无效")
    if mention_all:
        if role not in ("admin", "owner"):
            raise Error(403, "只有群管理者可以 @全体成员")
        if cm_chat.QUOTE_SNIPPET not in content:
            raise Error(400, "内容中需要包含 @全体成员")
    mentions = []
    for peer in cm_chat.parse_mentions(body.get("mentions")):
        if room["kind"] == "direct":
            eligible = peer in (room["low_id"], room["high_id"])
        else:
            member = membership(db, cid, peer)
            eligible = member["role"] and not member["removed"]
        if eligible and account(db, peer):
            mentions.append(peer)
    if cm_chat.throttled_send(uid):
        raise Error(429, "发言太快啦，歇一下")
    mid = db.execute("""INSERT INTO social_messages(conversation_id,user_id,client_id,content,image,mentions,
                      atts,quote,mention_all,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                     (cid, uid, client, content, image, encode(mentions), encode(atts), quote,
                      int(mention_all), now())).lastrowid
    if image:
        db.execute("UPDATE social_images SET message_id=? WHERE id=?", (mid, image))
    emit(db, cid, "message", mid)
    row = db.execute("SELECT * FROM social_messages WHERE id=?", (mid,)).fetchone()
    return messages_out(db, [row])[0]


def delete_private(db, room, uid, mid):
    cid = room["id"]
    member = access(db, room, uid)
    row = db.execute("SELECT user_id,image,deleted FROM social_messages WHERE id=? AND conversation_id=?",
                     (mid, cid)).fetchone()
    if not row:
        raise Error(404, "消息不存在")
    if row["user_id"] != uid and member["role"] not in ("admin", "owner"):
        raise Error(403, "没有删除权限")
    if not row["deleted"]:
        db.execute("UPDATE social_messages SET deleted=1 WHERE id=?", (mid,))
        if row["image"]:
            db.execute("DELETE FROM social_images WHERE id=?", (row["image"],))
        emit(db, cid, "delete", mid)


def upload_image(db, room, uid, body):
    access(db, room, uid, sending=True)
    try:
        raw = base64.b64decode(body.get("file", ""), validate=True)
    except (ValueError, TypeError) as exc:
        raise Error(400, "图片数据无效") from exc
    extension = cm_chat.sniff_image_ext(raw)
    if not extension or len(raw) > cm_chat.MAX_IMAGE_BYTES:
        raise Error(400, "请选择 1.5 MB 以内的 JPEG、PNG、GIF 或 WebP")
    if cm_chat.throttled_image(uid):
        raise Error(429, "图片传得太频繁")
    image = secrets.token_hex(16) + "." + extension
    db.execute("DELETE FROM social_images WHERE message_id IS NULL AND created_at<=?", (now() - 86400,))
    db.execute("INSERT INTO social_images VALUES(?,?,?,?,?,?,NULL)",
               (image, room["id"], uid, now(), IMAGE_TYPES[extension], raw))
    return {"image": image}


def load_image(db, room, uid, image):
    row = db.execute("""SELECT type,data FROM social_images WHERE id=? AND conversation_id=?
       AND (message_id IS NOT NULL OR (user_id=? AND created_at>?))""",
                     (image, room["id"], uid, now() - 86400)).fetchone()
    if not row:
        raise Error(404, "图片不存在")
    return row["type"], row["data"]


def mark_read(db, room, uid, mid):
    if not mid:
        return
    cid = room["id"]
    if cid == WORLD:
        if membership(db, cid, uid)["removed"]:
            return
        exists = db.execute("SELECT 1 FROM chat_messages WHERE id=?", (mid,)).fetchone()
    else:
        exists = db.execute("SELECT 1 FROM social_messages WHERE id=? AND conversation_id=?", (mid, cid)).fetchone()
    if not exists:
        return
    db.execute("""INSERT INTO social_reads VALUES(?,?,?)
                  ON CONFLICT(conversation_id,user_id) DO UPDATE
                  SET message_id=MAX(social_reads.message_id,excluded.message_id)""", (cid, uid, mid))


def report(db, uid, peer, body):
    row = relation(db, uid, peer)
    if not row or not row["accepted_at"]:
        raise Error(403, "只能举报现任或曾经的好友")
    reason = text(body.get("reason"), 500, "举报原因", required=True)
    existing = db.execute("SELECT id FROM social_reports WHERE reporter_id=? AND target_id=? AND status='pending'",
                          (uid, peer)).fetchone()
    if existing:
        return {"id": existing["id"]}
    if db.execute("SELECT COUNT(*) FROM social_reports WHERE reporter_id=? AND created_at>=?",
                  (uid, now() - 86400)).fetchone()[0] >= 5:
        raise Error(429, "今日举报次数已达上限")
    return {"id": db.execute("INSERT INTO social_reports(reporter_id,target_id,reason,created_at) VALUES(?,?,?,?)",
                             (uid, peer, reason, now())).lastrowid}


def review_reports(method, path, q, body, uid):
    with transaction(write=method == "PUT") as db:
        if method == "GET" and path == "/admin/social/reports":
            state = q.get("status", "pending")
            if state not in ("pending", "resolved", "dismissed"):
                raise Error(400, "举报状态无效")
            cursor = integer(q.get("before"))
            rows = db.execute("""SELECT * FROM social_reports WHERE status=? AND (?=0 OR id<?)
                ORDER BY id DESC LIMIT 31""", (state, cursor, cursor)).fetchall()
            items = [{
                "id": row["id"], "reporter": person(db, row["reporter_id"]),
                "target": person(db, row["target_id"]), "reason": row["reason"],
                "status": row["status"], "createdAt": row["created_at"], "reviewedAt": row["reviewed_at"],
            } for row in rows[:30]]
            return {"reports": items, "nextBefore": items[-1]["id"] if len(rows) > 30 else 0}
        match = re.fullmatch(r"/admin/social/reports/([0-9]+)", path)
        if method == "PUT" and match:
            rid = integer(match[1])
            state = (body or {}).get("status") if isinstance(body, dict) else None
            if state not in ("resolved", "dismissed"):
                raise Error(400, "请选择处理结果")
            result = db.execute("""UPDATE social_reports SET status=?,reviewed_by=?,reviewed_at=?
                                  WHERE id=? AND status='pending'""", (state, uid, now(), rid))
            if result.rowcount != 1:
                row = db.execute("SELECT status FROM social_reports WHERE id=?", (rid,)).fetchone()
                if not row or row["status"] != state:
                    raise Error(409, "举报不存在或已处理")
            return {"ok": True}
    raise Error(404, "接口不存在")


def dispatch(method, path, q, body, user, staff=False, can_send_world=True):
    uid = user["id"] if user else 0
    body = {} if body is None else body
    if not isinstance(body, dict):
        raise Error(400, "请求体必须是 JSON 对象")
    with transaction(write=method != "GET") as db:
        if path == "/social/conversations" and method == "GET":
            items = summaries(db, uid, staff)
            items[0]["canSend"] &= can_send_world
            items[0]["canModerate"] &= can_send_world
            items[0]["canDelete"] &= can_send_world
            pending = 0
            if uid:
                pending = db.execute("""SELECT
                    (SELECT COUNT(*) FROM social_friends WHERE state='pending' AND requester_id!=?
                        AND (low_id=? OR high_id=?)) +
                    (SELECT COUNT(*) FROM social_invites WHERE user_id=? AND state='pending')""",
                                      (uid, uid, uid, uid)).fetchone()[0]
            return {"conversations": items, "userId": uid, "pending": pending}
        if path == f"/social/conversations/{WORLD}" and method == "GET":
            item = summary(db, conversation(db, WORLD), uid, staff)
            item["canSend"] &= can_send_world
            item["canModerate"] &= can_send_world
            item["canDelete"] &= can_send_world
            return {"conversation": item}
        if not uid:
            raise Error(401, "未登录")
        if not account(db, uid):
            raise Error(403, "当前账号不可使用社交功能")
        if path == "/social/conversations" and method == "POST":
            kind = body.get("kind")
            if kind == "group":
                room = create_group(db, uid, body)
            elif kind == "direct":
                room = open_direct(db, uid, integer(body.get("userId")))
            else:
                raise Error(400, "请选择对话类型")
            return {"conversation": summary(db, room, uid, staff)}
        if path == "/social/users" and method == "GET":
            offset = integer(q.get("offset"), maximum=10000)
            rows = directory(db, (q.get("q") or "").strip()[:50], offset)
            for row in rows:
                relationship = relation(db, uid, row["id"])
                row["friendship"] = contact_out(db, relationship, uid)["state"] if relationship else ""
                row["canReport"] = bool(relationship and relationship["accepted_at"])
            return {"users": rows[:30], "more": len(rows) > 30}
        if path == "/social/contacts" and method == "GET":
            return {"contacts": contacts(db, uid), "invites": inbox(db, uid)}
        match = re.fullmatch(r"/social/contacts/([0-9]+)(/report)?", path)
        if match:
            peer = integer(match[1])
            if method == "POST" and match[2]:
                return report(db, uid, peer, body)
            if match[2] is None and method == "GET":
                row = relation(db, uid, peer)
                return contact_out(db, row, uid) if row else {
                    "user": {"id": peer}, "state": "", "canReport": False,
                }
            if match[2] is None and method == "PUT":
                return contact_change(db, uid, peer, body.get("action"))
        match = re.fullmatch(r"/social/conversations/([0-9]+)(?:/([^/]+)(?:/([^/]+))?)?", path)
        if not match:
            raise Error(404, "接口不存在")
        cid, section, item = integer(match[1]), match[2], match[3]
        if section == "invites" and method == "PUT" and item is None:
            answer = body.get("status")
            if answer not in ("accepted", "declined"):
                raise Error(400, "请选择接受或拒绝")
            answer_invite(db, cid, uid, answer)
            return {"ok": True}
        room = conversation(db, cid)
        if section is None and method == "DELETE" and room["kind"] == "group":
            leave(db, room, uid)
            return {"ok": True}
        access(db, room, uid)
        if section is None:
            if method == "GET":
                return {"conversation": summary(db, room, uid, staff)}
            if method == "PUT":
                return {"conversation": summary(db, update_group(db, room, uid, body), uid, staff)}
            if method == "DELETE":
                raise Error(403, "当前身份无法退出此群")
        if section == "invites" and method == "POST" and item is None:
            group_invite(db, room, uid, integer(body.get("userId")))
            return {"ok": True}
        if section == "members":
            if method == "GET" and item is None:
                offset = integer(q.get("offset"), maximum=100000)
                rows = members(db, room, uid, (q.get("q") or "").strip()[:50], offset,
                               removed=q.get("removed") == "1")
                return {"members": rows[:30], "more": len(rows) > 30}
            if method == "PUT" and item:
                member_change(db, room, uid, integer(item), body)
                return {"ok": True}
        if section == "read" and method == "PUT" and item is None:
            mark_read(db, room, uid, integer(body.get("messageId")))
            return {"ok": True}
        if cid == WORLD:
            raise Error(404, "世界聊天消息使用 /chat 接口")
        if section == "messages":
            if method == "GET" and item is None:
                return {"messages": list_messages(db, room, integer(q.get("before")),
                                                  max(1, integer(q.get("limit"), 30, 50))),
                        "latestSeq": db.execute("SELECT COALESCE(MAX(seq),0) FROM social_events "
                                                "WHERE conversation_id=?", (cid,)).fetchone()[0]}
            if method == "POST" and item is None:
                return send_private(db, room, uid, body)
            if method == "DELETE" and item:
                delete_private(db, room, uid, integer(item))
                return {"ok": True}
        if section == "events" and method == "GET" and item is None:
            return events(db, cid, integer(q.get("since")))
        if section == "images":
            if method == "POST" and item is None:
                return upload_image(db, room, uid, body)
            if method == "GET" and item and re.fullmatch(r"[0-9a-f]{32}\.(?:jpg|png|gif|webp)", item):
                return load_image(db, room, uid, item)
    raise Error(404, "接口不存在")


def world_mentions(db, uids):
    """Public events mention only eligible public participants and the service bot."""
    if not uids:
        return []
    marks = ",".join("?" for _ in uids)
    return [row[0] for row in db.execute(f"""SELECT u.id FROM users u
        LEFT JOIN social_members m ON m.user_id=u.id AND m.conversation_id=?
        WHERE u.id IN ({marks}) AND u.disabled=0 AND u.username!='__internal__'
          AND (u.public_square=1 OR u.is_bot=1) AND COALESCE(m.removed,0)=0""", (WORLD, *uids))]
