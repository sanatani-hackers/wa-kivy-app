"""SQLite WAL client database (`wa_client.sqlite3`) shared safely between Service and UI."""
import json
import os
import sqlite3
import threading
import time
from typing import Any, Optional

_INSTANCES: dict[str, "ClientDB"] = {}
_INSTANCE_LOCK = threading.Lock()

_RECEIPT_RANK = {
    "": 0,
    "pending": 0,
    "sent": 1,
    "delivered": 2,
    "read": 3,
    "played": 4,
}


def normalize_jid(jid: str) -> str:
    """Normalize a WhatsApp JID string by stripping device suffix (:NN) while preserving user@server."""
    s = str(jid or "").strip()
    if "@" not in s:
        return s
    user_part, server = s.split("@", 1)
    user_clean = user_part.split(":", 1)[0]
    return f"{user_clean}@{server}"


class ClientDB:
    """Thread-safe SQLite WAL client store for chats, messages, and contacts."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._local = threading.local()
        self._write_lock = threading.Lock()
        self._ensure_schema()

    @classmethod
    def get(cls, private_dir: str) -> "ClientDB":
        """Return singleton ClientDB bound to private_dir/wa_client.sqlite3."""
        db_path = os.path.join(private_dir, "wa_client.sqlite3")
        with _INSTANCE_LOCK:
            inst = _INSTANCES.get(db_path)
            if inst is None:
                inst = cls(db_path)
                _INSTANCES[db_path] = inst
            return inst

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=15.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA busy_timeout=5000;")
            self._local.conn = conn
        return conn

    def _ensure_schema(self):
        with self._write_lock:
            c = self._conn()
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS chats (
                    jid          TEXT PRIMARY KEY,
                    name         TEXT DEFAULT '',
                    is_group     INTEGER DEFAULT 0,
                    last_msg_id  TEXT DEFAULT '',
                    last_preview TEXT DEFAULT '',
                    last_ts      INTEGER DEFAULT 0,
                    unread_count INTEGER DEFAULT 0,
                    muted_until  INTEGER DEFAULT 0,
                    pinned       INTEGER DEFAULT 0,
                    archived     INTEGER DEFAULT 0,
                    avatar_path  TEXT DEFAULT '',
                    updated_at   INTEGER DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_chats_sort
                    ON chats(archived ASC, pinned DESC, last_ts DESC);

                CREATE TABLE IF NOT EXISTS messages (
                    id             TEXT PRIMARY KEY,
                    chat_id        TEXT NOT NULL,
                    sender_id      TEXT DEFAULT '',
                    from_me        INTEGER DEFAULT 0,
                    ts             INTEGER DEFAULT 0,
                    msg_type       TEXT DEFAULT 'text',
                    body           TEXT DEFAULT '',
                    media_path     TEXT DEFAULT '',
                    media_mime     TEXT DEFAULT '',
                    quoted_id      TEXT DEFAULT '',
                    reactions_json TEXT DEFAULT '{}',
                    receipt_status TEXT DEFAULT 'sent',
                    is_deleted     INTEGER DEFAULT 0,
                    original_body  TEXT DEFAULT '',
                    is_edited      INTEGER DEFAULT 0,
                    is_view_once   INTEGER DEFAULT 0,
                    raw_json       TEXT DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_msg_chat_ts
                    ON messages(chat_id, ts ASC, id);

                CREATE TABLE IF NOT EXISTS contacts (
                    jid           TEXT PRIMARY KEY,
                    phone         TEXT DEFAULT '',
                    push_name     TEXT DEFAULT '',
                    full_name     TEXT DEFAULT '',
                    business_name TEXT DEFAULT '',
                    about         TEXT DEFAULT '',
                    avatar_path   TEXT DEFAULT '',
                    is_online     INTEGER DEFAULT 0,
                    is_typing     INTEGER DEFAULT 0,
                    last_seen_ts  INTEGER DEFAULT 0,
                    updated_at    INTEGER DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_contacts_name
                    ON contacts(full_name, push_name, phone);
                """
            )
            c.commit()

    # ------------------------------------------------------------------ Chats
    def upsert_chat(
        self,
        jid: str,
        *,
        name: Optional[str] = None,
        is_group: Optional[bool] = None,
        last_msg_id: Optional[str] = None,
        last_preview: Optional[str] = None,
        last_ts: Optional[int] = None,
        unread_count: Optional[int] = None,
        unread_delta: int = 0,
        muted_until: Optional[int] = None,
        pinned: Optional[bool] = None,
        archived: Optional[bool] = None,
        avatar_path: Optional[str] = None,
    ):
        jid = normalize_jid(jid)
        if not jid:
            return
        now = int(time.time())
        group_flag = int(is_group) if is_group is not None else (1 if jid.endswith("@g.us") else 0)
        init_unread = int(unread_count) if unread_count is not None else max(0, int(unread_delta))

        with self._write_lock:
            c = self._conn()
            cur = c.execute("SELECT * FROM chats WHERE jid = ?", (jid,))
            row = cur.fetchone()
            if row is None:
                c.execute(
                    """
                    INSERT INTO chats (
                        jid, name, is_group, last_msg_id, last_preview, last_ts,
                        unread_count, muted_until, pinned, archived, avatar_path, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        jid,
                        str(name or ""),
                        group_flag,
                        str(last_msg_id or ""),
                        str(last_preview or ""),
                        int(last_ts or 0),
                        init_unread,
                        int(muted_until or 0),
                        int(bool(pinned)) if pinned is not None else 0,
                        int(bool(archived)) if archived is not None else 0,
                        str(avatar_path or ""),
                        now,
                    ),
                )
            else:
                new_unread = row["unread_count"]
                if unread_count is not None:
                    new_unread = max(0, int(unread_count))
                elif unread_delta:
                    new_unread = max(0, int(row["unread_count"] or 0) + int(unread_delta))

                new_ts = int(row["last_ts"] or 0)
                new_mid = row["last_msg_id"] or ""
                new_prev = row["last_preview"] or ""
                if last_ts is not None and int(last_ts) >= new_ts:
                    new_ts = int(last_ts)
                    if last_msg_id is not None:
                        new_mid = str(last_msg_id)
                    if last_preview is not None:
                        new_prev = str(last_preview)
                elif last_preview is not None and not new_prev:
                    new_prev = str(last_preview)

                c.execute(
                    """
                    UPDATE chats SET
                        name = ?,
                        is_group = ?,
                        last_msg_id = ?,
                        last_preview = ?,
                        last_ts = ?,
                        unread_count = ?,
                        muted_until = ?,
                        pinned = ?,
                        archived = ?,
                        avatar_path = ?,
                        updated_at = ?
                    WHERE jid = ?
                    """,
                    (
                        str(name) if (name is not None and str(name).strip()) else row["name"],
                        int(is_group) if is_group is not None else row["is_group"],
                        new_mid,
                        new_prev,
                        new_ts,
                        new_unread,
                        int(muted_until) if muted_until is not None else row["muted_until"],
                        int(bool(pinned)) if pinned is not None else row["pinned"],
                        int(bool(archived)) if archived is not None else row["archived"],
                        str(avatar_path) if avatar_path else row["avatar_path"],
                        now,
                        jid,
                    ),
                )
            c.commit()

    def clear_unread(self, jid: str):
        jid = normalize_jid(jid)
        with self._write_lock:
            c = self._conn()
            c.execute("UPDATE chats SET unread_count = 0, updated_at = ? WHERE jid = ?", (int(time.time()), jid))
            c.commit()

    def set_chat_flags(
        self,
        jid: str,
        *,
        pinned: Optional[bool] = None,
        archived: Optional[bool] = None,
        muted_until: Optional[int] = None,
    ):
        jid = normalize_jid(jid)
        self.upsert_chat(jid, pinned=pinned, archived=archived, muted_until=muted_until)

    def get_chat(self, jid: str) -> Optional[dict[str, Any]]:
        jid = normalize_jid(jid)
        cur = self._conn().execute(
            """
            SELECT ch.*,
                   COALESCE(NULLIF(ch.name, ''), NULLIF(ct.full_name, ''), NULLIF(ct.push_name, ''), ch.jid) AS display_name,
                   COALESCE(ct.is_online, 0) AS is_online,
                   COALESCE(ct.is_typing, 0) AS is_typing,
                   COALESCE(ct.last_seen_ts, 0) AS last_seen_ts,
                   COALESCE(NULLIF(ch.avatar_path, ''), ct.avatar_path, '') AS resolved_avatar
            FROM chats ch
            LEFT JOIN contacts ct ON ct.jid = ch.jid
            WHERE ch.jid = ?
            """,
            (jid,),
        )
        r = cur.fetchone()
        return dict(r) if r else None

    def get_chats(self, filter_mode: str = "all", search: str = "", limit: int = 150) -> list[dict[str, Any]]:
        where = []
        params: list[Any] = []
        mode = (filter_mode or "all").lower()
        if mode == "archived":
            where.append("ch.archived = 1")
        else:
            where.append("ch.archived = 0")
            if mode == "unread":
                where.append("ch.unread_count > 0")
            elif mode == "groups":
                where.append("ch.is_group = 1")

        q = (search or "").strip()
        if q:
            like = f"%{q}%"
            where.append(
                "(ch.jid LIKE ? OR ch.name LIKE ? OR ct.full_name LIKE ? OR ct.push_name LIKE ? OR ch.last_preview LIKE ?)"
            )
            params.extend([like, like, like, like, like])

        sql = f"""
            SELECT ch.*,
                   COALESCE(NULLIF(ch.name, ''), NULLIF(ct.full_name, ''), NULLIF(ct.push_name, ''), ch.jid) AS display_name,
                   COALESCE(ct.is_online, 0) AS is_online,
                   COALESCE(ct.is_typing, 0) AS is_typing,
                   COALESCE(ct.last_seen_ts, 0) AS last_seen_ts,
                   COALESCE(NULLIF(ch.avatar_path, ''), ct.avatar_path, '') AS resolved_avatar
            FROM chats ch
            LEFT JOIN contacts ct ON ct.jid = ch.jid
            WHERE {' AND '.join(where)}
            ORDER BY ch.pinned DESC, ch.last_ts DESC, ch.updated_at DESC
            LIMIT ?
        """
        params.append(max(1, int(limit)))
        cur = self._conn().execute(sql, params)
        return [dict(r) for r in cur.fetchall()]

    # ------------------------------------------------------------------ Messages
    def upsert_message(
        self,
        msg_id: str,
        chat_id: str,
        *,
        sender_id: str = "",
        from_me: bool = False,
        ts: int = 0,
        msg_type: str = "text",
        body: str = "",
        media_path: str = "",
        media_mime: str = "",
        quoted_id: str = "",
        reactions_json: str = "{}",
        receipt_status: str = "sent",
        is_deleted: bool = False,
        original_body: str = "",
        is_edited: bool = False,
        is_view_once: bool = False,
        raw_json: str = "",
    ):
        if not msg_id or not chat_id:
            return
        chat_id = normalize_jid(chat_id)
        sender_id = normalize_jid(sender_id)
        ts_val = int(ts or time.time())

        with self._write_lock:
            c = self._conn()
            cur = c.execute(
                "SELECT id, receipt_status, media_path, body, original_body, is_deleted, is_edited FROM messages WHERE id = ?",
                (msg_id,),
            )
            existing = cur.fetchone()
            if existing is None:
                c.execute(
                    """
                    INSERT INTO messages (
                        id, chat_id, sender_id, from_me, ts, msg_type, body, media_path,
                        media_mime, quoted_id, reactions_json, receipt_status,
                        is_deleted, original_body, is_edited, is_view_once, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        msg_id,
                        chat_id,
                        sender_id,
                        int(bool(from_me)),
                        ts_val,
                        msg_type or "text",
                        body or "",
                        media_path or "",
                        media_mime or "",
                        quoted_id or "",
                        reactions_json or "{}",
                        receipt_status or "sent",
                        int(bool(is_deleted)),
                        original_body or "",
                        int(bool(is_edited)),
                        int(bool(is_view_once)),
                        raw_json or "",
                    ),
                )
            else:
                cur_rank = _RECEIPT_RANK.get(existing["receipt_status"] or "sent", 1)
                new_rank = _RECEIPT_RANK.get(receipt_status or "sent", 1)
                final_status = receipt_status if new_rank >= cur_rank else existing["receipt_status"]
                final_media = media_path or existing["media_path"]
                final_body = body if (body and not existing["is_deleted"]) else existing["body"]
                c.execute(
                    """
                    UPDATE messages SET
                        media_path = ?,
                        media_mime = CASE WHEN ? != '' THEN ? ELSE media_mime END,
                        receipt_status = ?,
                        body = ?,
                        is_view_once = MAX(is_view_once, ?)
                    WHERE id = ?
                    """,
                    (final_media, media_mime, media_mime, final_status, final_body, int(bool(is_view_once)), msg_id),
                )
            c.commit()

    def get_message(self, msg_id: str, chat_id: Optional[str] = None) -> Optional[dict[str, Any]]:
        if chat_id:
            cur = self._conn().execute(
                "SELECT * FROM messages WHERE id = ? AND chat_id = ?", (msg_id, normalize_jid(chat_id))
            )
        else:
            cur = self._conn().execute("SELECT * FROM messages WHERE id = ?", (msg_id,))
        r = cur.fetchone()
        return dict(r) if r else None

    def get_messages(self, chat_id: str, limit: int = 120, offset: int = 0) -> list[dict[str, Any]]:
        chat_id = normalize_jid(chat_id)
        cur = self._conn().execute(
            """
            SELECT * FROM (
                SELECT m.*, q.body AS quoted_body, q.sender_id AS quoted_sender
                FROM messages m
                LEFT JOIN messages q ON q.id = m.quoted_id AND m.quoted_id != ''
                WHERE m.chat_id = ?
                ORDER BY m.ts DESC, m.rowid DESC
                LIMIT ? OFFSET ?
            ) sub
            ORDER BY sub.ts ASC, sub.id ASC
            """,
            (chat_id, max(1, int(limit)), max(0, int(offset))),
        )
        return [dict(r) for r in cur.fetchall()]

    def get_unread_msg_ids(self, chat_id: str, limit: int = 50) -> list[tuple[str, str]]:
        """Return [(msg_id, sender_id)] for incoming messages not yet locally read."""
        chat_id = normalize_jid(chat_id)
        cur = self._conn().execute(
            """
            SELECT id, sender_id FROM messages
            WHERE chat_id = ? AND from_me = 0 AND receipt_status != 'read'
            ORDER BY ts DESC LIMIT ?
            """,
            (chat_id, max(1, int(limit))),
        )
        return [(r["id"], r["sender_id"]) for r in cur.fetchall()]

    def mark_message_deleted(self, msg_id: str, chat_id: Optional[str] = None) -> str:
        """Preserve original body and flag row as deleted inline. Returns chat_id if matched."""
        with self._write_lock:
            c = self._conn()
            cur = c.execute("SELECT chat_id, body, original_body FROM messages WHERE id = ?", (msg_id,))
            row = cur.fetchone()
            if row is None:
                return normalize_jid(chat_id or "")
            orig = row["original_body"] or row["body"] or ""
            c.execute(
                "UPDATE messages SET is_deleted = 1, original_body = ? WHERE id = ?",
                (orig, msg_id),
            )
            c.commit()
            return row["chat_id"]

    def mark_message_edited(self, msg_id: str, new_body: str, chat_id: Optional[str] = None) -> str:
        """Preserve first original body, update current body, and set is_edited=1."""
        with self._write_lock:
            c = self._conn()
            cur = c.execute("SELECT chat_id, body, original_body FROM messages WHERE id = ?", (msg_id,))
            row = cur.fetchone()
            if row is None:
                return normalize_jid(chat_id or "")
            orig = row["original_body"] or row["body"] or ""
            c.execute(
                "UPDATE messages SET is_edited = 1, original_body = ?, body = ? WHERE id = ?",
                (orig, new_body, msg_id),
            )
            c.commit()
            return row["chat_id"]

    def add_reaction(self, msg_id: str, sender_id: str, emoji: str) -> str:
        """Update reaction emoji per sender inside reactions_json."""
        sender_clean = normalize_jid(sender_id)
        with self._write_lock:
            c = self._conn()
            cur = c.execute("SELECT chat_id, reactions_json FROM messages WHERE id = ?", (msg_id,))
            row = cur.fetchone()
            if row is None:
                return ""
            try:
                rx = json.loads(row["reactions_json"] or "{}")
                if not isinstance(rx, dict):
                    rx = {}
            except Exception:
                rx = {}
            if emoji:
                rx[sender_clean] = emoji
            else:
                rx.pop(sender_clean, None)
            c.execute(
                "UPDATE messages SET reactions_json = ? WHERE id = ?",
                (json.dumps(rx), msg_id),
            )
            c.commit()
            return row["chat_id"]

    def update_receipt(self, chat_id: str, msg_ids: list[str], status: str):
        if not msg_ids:
            return
        chat_id = normalize_jid(chat_id)
        new_rank = _RECEIPT_RANK.get(status, 0)
        with self._write_lock:
            c = self._conn()
            for mid in msg_ids:
                cur = c.execute("SELECT receipt_status FROM messages WHERE id = ? AND chat_id = ?", (str(mid), chat_id))
                row = cur.fetchone()
                if row is not None:
                    cur_rank = _RECEIPT_RANK.get(row["receipt_status"] or "sent", 1)
                    if new_rank > cur_rank:
                        c.execute("UPDATE messages SET receipt_status = ? WHERE id = ?", (status, str(mid)))
            c.commit()

    def update_media_path(self, msg_id: str, media_path: str, media_mime: str = ""):
        with self._write_lock:
            c = self._conn()
            c.execute(
                """
                UPDATE messages SET media_path = ?,
                    media_mime = CASE WHEN ? != '' THEN ? ELSE media_mime END
                WHERE id = ?
                """,
                (media_path, media_mime, media_mime, msg_id),
            )
            c.commit()

    def mark_incoming_chat_read_local(self, chat_id: str):
        chat_id = normalize_jid(chat_id)
        with self._write_lock:
            c = self._conn()
            c.execute(
                "UPDATE messages SET receipt_status = 'read' WHERE chat_id = ? AND from_me = 0",
                (chat_id,),
            )
            c.execute("UPDATE chats SET unread_count = 0, updated_at = ? WHERE jid = ?", (int(time.time()), chat_id))
            c.commit()

    # ------------------------------------------------------------------ Contacts
    def upsert_contact(
        self,
        jid: str,
        *,
        phone: str = "",
        push_name: str = "",
        full_name: str = "",
        business_name: str = "",
        about: str = "",
        avatar_path: str = "",
    ):
        jid = normalize_jid(jid)
        if not jid:
            return
        if not phone and "@" in jid and not jid.endswith("@g.us"):
            phone = jid.split("@", 1)[0]
        now = int(time.time())
        with self._write_lock:
            c = self._conn()
            c.execute(
                """
                INSERT INTO contacts (
                    jid, phone, push_name, full_name, business_name, about, avatar_path, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(jid) DO UPDATE SET
                    phone = CASE WHEN excluded.phone != '' THEN excluded.phone ELSE phone END,
                    push_name = CASE WHEN excluded.push_name != '' THEN excluded.push_name ELSE push_name END,
                    full_name = CASE WHEN excluded.full_name != '' THEN excluded.full_name ELSE full_name END,
                    business_name = CASE WHEN excluded.business_name != '' THEN excluded.business_name ELSE business_name END,
                    about = CASE WHEN excluded.about != '' THEN excluded.about ELSE about END,
                    avatar_path = CASE WHEN excluded.avatar_path != '' THEN excluded.avatar_path ELSE avatar_path END,
                    updated_at = excluded.updated_at
                """,
                (jid, phone, push_name, full_name, business_name, about, avatar_path, now),
            )
            c.commit()

    def set_presence(self, jid: str, *, is_online: Optional[bool] = None, last_seen_ts: Optional[int] = None):
        jid = normalize_jid(jid)
        if not jid:
            return
        self.upsert_contact(jid)
        with self._write_lock:
            c = self._conn()
            if is_online is not None and last_seen_ts is not None:
                c.execute(
                    "UPDATE contacts SET is_online = ?, last_seen_ts = ?, updated_at = ? WHERE jid = ?",
                    (int(bool(is_online)), int(last_seen_ts), int(time.time()), jid),
                )
            elif is_online is not None:
                c.execute(
                    "UPDATE contacts SET is_online = ?, updated_at = ? WHERE jid = ?",
                    (int(bool(is_online)), int(time.time()), jid),
                )
            elif last_seen_ts is not None:
                c.execute(
                    "UPDATE contacts SET last_seen_ts = ?, updated_at = ? WHERE jid = ?",
                    (int(last_seen_ts), int(time.time()), jid),
                )
            c.commit()

    def set_chat_typing(self, chat_id: str, typing: bool):
        chat_id = normalize_jid(chat_id)
        if not chat_id:
            return
        self.upsert_contact(chat_id)
        with self._write_lock:
            c = self._conn()
            c.execute(
                "UPDATE contacts SET is_typing = ?, updated_at = ? WHERE jid = ?",
                (int(bool(typing)), int(time.time()), chat_id),
            )
            c.commit()

    def get_contacts(self, search: str = "", groups_only: Optional[bool] = None, limit: int = 500) -> list[dict[str, Any]]:
        where = ["1=1"]
        params: list[Any] = []
        if groups_only is True:
            where.append("jid LIKE '%@g.us'")
        elif groups_only is False:
            where.append("jid NOT LIKE '%@g.us'")
        q = (search or "").strip()
        if q:
            like = f"%{q}%"
            where.append("(full_name LIKE ? OR push_name LIKE ? OR phone LIKE ? OR jid LIKE ?)")
            params.extend([like, like, like, like])
        sql = f"""
            SELECT *,
                   COALESCE(NULLIF(full_name, ''), NULLIF(push_name, ''), NULLIF(business_name, ''), NULLIF(phone, ''), jid) AS display_name,
                   CASE WHEN jid LIKE '%@g.us' THEN 1 ELSE 0 END AS is_group
            FROM contacts
            WHERE {' AND '.join(where)}
            ORDER BY is_group ASC, LOWER(display_name) ASC
            LIMIT ?
        """
        params.append(max(1, int(limit)))
        cur = self._conn().execute(sql, params)
        return [dict(r) for r in cur.fetchall()]
