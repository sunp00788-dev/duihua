"""SQLite storage. One file, safe to copy with the app to another PC."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB_PATH = DATA / "duihua.sqlite"
LOCK = threading.Lock()


def now() -> int:
    return int(time.time())


def new_id() -> str:
    return uuid.uuid4().hex


def connect() -> sqlite3.Connection:
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "images").mkdir(exist_ok=True)
    (DATA / "uploads").mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    with LOCK:
        conn = connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    salt TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL,
                    disabled INTEGER NOT NULL DEFAULT 0,
                    daily_limit INTEGER,
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS auth_sessions (
                    token TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    expires_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chats (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    chat_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    body TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    message_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    upstream_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS kv (
                    k TEXT PRIMARY KEY,
                    v TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS usage_daily (
                    user_id TEXT NOT NULL,
                    day TEXT NOT NULL,
                    count INTEGER NOT NULL,
                    PRIMARY KEY (user_id, day)
                );
                CREATE TABLE IF NOT EXISTS use_daily (
                    user_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    day TEXT NOT NULL,
                    count INTEGER NOT NULL,
                    PRIMARY KEY (user_id, kind, day)
                );
                CREATE TABLE IF NOT EXISTS mindmaps (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    source_text TEXT NOT NULL,
                    outline TEXT NOT NULL,
                    tree TEXT NOT NULL,
                    theme TEXT NOT NULL,
                    spacing INTEGER NOT NULL DEFAULT 34,
                    layout TEXT NOT NULL DEFAULT 'right',
                    revision INTEGER NOT NULL DEFAULT 1,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                """
            )
            for statement in (
                "CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_id, user_id, created_at)",
                "CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, updated_at)",
                "CREATE INDEX IF NOT EXISTS idx_jobs_message ON jobs(message_id)",
                "CREATE INDEX IF NOT EXISTS idx_mindmaps_user ON mindmaps(user_id, updated_at)",
                "CREATE INDEX IF NOT EXISTS idx_auth_user ON auth_sessions(user_id)",
            ):
                try:
                    conn.execute(statement)
                except sqlite3.OperationalError:
                    pass
            for statement in (
                "ALTER TABLE mindmaps ADD COLUMN spacing INTEGER NOT NULL DEFAULT 34",
                "ALTER TABLE mindmaps ADD COLUMN layout TEXT NOT NULL DEFAULT 'right'",
                "ALTER TABLE mindmaps ADD COLUMN revision INTEGER NOT NULL DEFAULT 1",
                "ALTER TABLE chats ADD COLUMN memory TEXT NOT NULL DEFAULT '{}'",
            ):
                try:
                    conn.execute(statement)
                except sqlite3.OperationalError:
                    pass
            conn.commit()
        finally:
            conn.close()


def _one(conn, sql, args=()):
    row = conn.execute(sql, args).fetchone()
    return dict(row) if row else None


def _all(conn, sql, args=()):
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def user_public(row: dict | None) -> dict | None:
    if not row:
        return None
    return {
        "id": row["id"],
        "username": row["username"],
        "role": row["role"],
        "disabled": bool(row["disabled"]),
        "daily_limit": row["daily_limit"],
        "created_at": row["created_at"],
    }


def get_user_by_name(username: str) -> dict | None:
    with LOCK:
        conn = connect()
        try:
            return _one(conn, "SELECT * FROM users WHERE username = ?", (username,))
        finally:
            conn.close()


def get_user(user_id: str) -> dict | None:
    with LOCK:
        conn = connect()
        try:
            return _one(conn, "SELECT * FROM users WHERE id = ?", (user_id,))
        finally:
            conn.close()


def create_user(username, salt, password_hash, role="employee", daily_limit=None) -> dict:
    row = {
        "id": new_id(),
        "username": username,
        "salt": salt,
        "password_hash": password_hash,
        "role": role,
        "disabled": 0,
        "daily_limit": daily_limit,
        "created_at": now(),
    }
    with LOCK:
        conn = connect()
        try:
            conn.execute(
                """INSERT INTO users (id, username, salt, password_hash, role, disabled, daily_limit, created_at)
                   VALUES (:id, :username, :salt, :password_hash, :role, :disabled, :daily_limit, :created_at)""",
                row,
            )
            conn.commit()
        finally:
            conn.close()
    return row


def list_users() -> list[dict]:
    with LOCK:
        conn = connect()
        try:
            return _all(conn, "SELECT * FROM users ORDER BY created_at")
        finally:
            conn.close()


def update_user(user_id: str, **fields) -> None:
    allowed = {"disabled", "daily_limit", "salt", "password_hash", "role"}
    sets = []
    args = []
    for key, value in fields.items():
        if key not in allowed:
            continue
        sets.append(f"{key} = ?")
        args.append(value)
    if not sets:
        return
    args.append(user_id)
    with LOCK:
        conn = connect()
        try:
            conn.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = ?", args)
            conn.commit()
        finally:
            conn.close()


def create_auth(user_id: str, days: int = 14) -> str:
    token = uuid.uuid4().hex + uuid.uuid4().hex
    expires = now() + days * 86400
    with LOCK:
        conn = connect()
        try:
            conn.execute(
                "INSERT INTO auth_sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
                (token, user_id, expires),
            )
            conn.commit()
        finally:
            conn.close()
    return token


def user_for_token(token: str) -> dict | None:
    if not token:
        return None
    with LOCK:
        conn = connect()
        try:
            row = _one(
                conn,
                """SELECT u.* FROM auth_sessions s
                   JOIN users u ON u.id = s.user_id
                   WHERE s.token = ? AND s.expires_at > ? AND u.disabled = 0""",
                (token, now()),
            )
            return row
        finally:
            conn.close()


def delete_auth(token: str) -> None:
    with LOCK:
        conn = connect()
        try:
            conn.execute("DELETE FROM auth_sessions WHERE token = ?", (token,))
            conn.commit()
        finally:
            conn.close()


def create_chat(user_id: str, title: str) -> dict:
    row = {
        "id": new_id(),
        "user_id": user_id,
        "title": title[:40] or "新对话",
        "created_at": now(),
        "updated_at": now(),
    }
    with LOCK:
        conn = connect()
        try:
            conn.execute(
                "INSERT INTO chats (id, user_id, title, created_at, updated_at) VALUES (:id, :user_id, :title, :created_at, :updated_at)",
                row,
            )
            conn.commit()
        finally:
            conn.close()
    return row


def list_chats(user_id: str) -> list[dict]:
    with LOCK:
        conn = connect()
        try:
            return _all(
                conn,
                "SELECT id, title, created_at, updated_at FROM chats WHERE user_id = ? ORDER BY updated_at DESC LIMIT 80",
                (user_id,),
            )
        finally:
            conn.close()


def get_chat(user_id: str, chat_id: str) -> dict | None:
    with LOCK:
        conn = connect()
        try:
            return _one(conn, "SELECT * FROM chats WHERE id = ? AND user_id = ?", (chat_id, user_id))
        finally:
            conn.close()


def read_memory(chat_id: str) -> str:
    with LOCK:
        conn = connect()
        try:
            row = _one(conn, "SELECT memory FROM chats WHERE id = ?", (chat_id,))
        finally:
            conn.close()
    if not row:
        return "{}"
    return row.get("memory") or "{}"


def write_memory(chat_id: str, raw: str) -> None:
    with LOCK:
        conn = connect()
        try:
            conn.execute("UPDATE chats SET memory = ? WHERE id = ?", (raw, chat_id))
            conn.commit()
        finally:
            conn.close()


def touch_chat(chat_id: str, title: str | None = None) -> None:
    with LOCK:
        conn = connect()
        try:
            if title:
                conn.execute(
                    "UPDATE chats SET updated_at = ?, title = ? WHERE id = ?",
                    (now(), title[:40], chat_id),
                )
            else:
                conn.execute("UPDATE chats SET updated_at = ? WHERE id = ?", (now(), chat_id))
            conn.commit()
        finally:
            conn.close()


def delete_chat(user_id: str, chat_id: str) -> bool:
    with LOCK:
        conn = connect()
        try:
            chat = _one(conn, "SELECT id FROM chats WHERE id = ? AND user_id = ?", (chat_id, user_id))
            if not chat:
                return False
            conn.execute("DELETE FROM jobs WHERE chat_id = ?", (chat_id,))
            conn.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
            conn.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
            conn.commit()
            return True
        finally:
            conn.close()


def add_message(chat_id: str, user_id: str, role: str, body: dict) -> dict:
    row = {
        "id": new_id(),
        "chat_id": chat_id,
        "user_id": user_id,
        "role": role,
        "body": json.dumps(body, ensure_ascii=False),
        "created_at": now(),
    }
    with LOCK:
        conn = connect()
        try:
            conn.execute(
                "INSERT INTO messages (id, chat_id, user_id, role, body, created_at) VALUES (:id, :chat_id, :user_id, :role, :body, :created_at)",
                row,
            )
            conn.execute("UPDATE chats SET updated_at = ? WHERE id = ?", (now(), chat_id))
            conn.commit()
        finally:
            conn.close()
    row["body"] = body
    return row


def update_message(message_id: str, body: dict) -> None:
    with LOCK:
        conn = connect()
        try:
            conn.execute(
                "UPDATE messages SET body = ? WHERE id = ?",
                (json.dumps(body, ensure_ascii=False), message_id),
            )
            conn.commit()
        finally:
            conn.close()


def get_message(user_id: str, message_id: str) -> dict | None:
    with LOCK:
        conn = connect()
        try:
            row = _one(
                conn,
                "SELECT * FROM messages WHERE id = ? AND user_id = ?",
                (message_id, user_id),
            )
        finally:
            conn.close()
    if not row:
        return None
    row["body"] = json.loads(row["body"])
    return row


def list_messages(user_id: str, chat_id: str) -> list[dict]:
    with LOCK:
        conn = connect()
        try:
            rows = _all(
                conn,
                "SELECT * FROM messages WHERE chat_id = ? AND user_id = ? ORDER BY created_at",
                (chat_id, user_id),
            )
        finally:
            conn.close()
    for row in rows:
        row["body"] = json.loads(row["body"])
    return rows


def create_job(message_id, chat_id, user_id, upstream_id) -> dict:
    row = {
        "id": new_id(),
        "message_id": message_id,
        "chat_id": chat_id,
        "user_id": user_id,
        "upstream_id": upstream_id,
        "status": "pending",
        "updated_at": now(),
    }
    with LOCK:
        conn = connect()
        try:
            conn.execute(
                """INSERT INTO jobs (id, message_id, chat_id, user_id, upstream_id, status, updated_at)
                   VALUES (:id, :message_id, :chat_id, :user_id, :upstream_id, :status, :updated_at)""",
                row,
            )
            conn.commit()
        finally:
            conn.close()
    return row


def pending_jobs() -> list[dict]:
    with LOCK:
        conn = connect()
        try:
            return _all(conn, "SELECT * FROM jobs WHERE status = 'pending' ORDER BY updated_at")
        finally:
            conn.close()


def get_job(job_id: str) -> dict | None:
    with LOCK:
        conn = connect()
        try:
            return _one(conn, "SELECT * FROM jobs WHERE id = ?", (job_id,))
        finally:
            conn.close()


def supersede_jobs(message_id: str) -> None:
    """Older upstream tasks must stop writing into a message that was resubmitted."""
    with LOCK:
        conn = connect()
        try:
            conn.execute(
                "UPDATE jobs SET status = 'replaced', updated_at = ? WHERE message_id = ? AND status = 'pending'",
                (now(), message_id),
            )
            conn.commit()
        finally:
            conn.close()


def update_job(job_id: str, status: str) -> None:
    with LOCK:
        conn = connect()
        try:
            conn.execute(
                "UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?",
                (status, now(), job_id),
            )
            conn.commit()
        finally:
            conn.close()


def pending_count(user_id: str) -> int:
    with LOCK:
        conn = connect()
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM jobs WHERE user_id = ? AND status = 'pending'",
                (user_id,),
            ).fetchone()
            return int(row["n"])
        finally:
            conn.close()


def kv_get(key: str, default=None):
    with LOCK:
        conn = connect()
        try:
            row = _one(conn, "SELECT v FROM kv WHERE k = ?", (key,))
        finally:
            conn.close()
    if not row:
        return default
    try:
        return json.loads(row["v"])
    except json.JSONDecodeError:
        return row["v"]


def kv_set(key: str, value) -> None:
    raw = json.dumps(value, ensure_ascii=False)
    with LOCK:
        conn = connect()
        try:
            conn.execute(
                "INSERT INTO kv (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v",
                (key, raw),
            )
            conn.commit()
        finally:
            conn.close()


def today() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


def usage_today(user_id: str) -> int:
    with LOCK:
        conn = connect()
        try:
            row = _one(
                conn,
                "SELECT count FROM usage_daily WHERE user_id = ? AND day = ?",
                (user_id, today()),
            )
            return int(row["count"]) if row else 0
        finally:
            conn.close()


def use_today(user_id: str, kind: str) -> int:
    with LOCK:
        conn = connect()
        try:
            row = _one(
                conn,
                "SELECT count FROM use_daily WHERE user_id = ? AND kind = ? AND day = ?",
                (user_id, kind, today()),
            )
            return int(row["count"]) if row else 0
        finally:
            conn.close()


def add_use(user_id: str, kind: str, n: int = 1) -> None:
    day = today()
    with LOCK:
        conn = connect()
        try:
            conn.execute(
                """INSERT INTO use_daily (user_id, kind, day, count) VALUES (?, ?, ?, ?)
                   ON CONFLICT(user_id, kind, day) DO UPDATE SET count = count + ?""",
                (user_id, kind, day, n, n),
            )
            conn.commit()
        finally:
            conn.close()


def add_usage(user_id: str, n: int = 1) -> None:
    day = today()
    with LOCK:
        conn = connect()
        try:
            conn.execute(
                """INSERT INTO usage_daily (user_id, day, count) VALUES (?, ?, ?)
                   ON CONFLICT(user_id, day) DO UPDATE SET count = count + ?""",
                (user_id, day, n, n),
            )
            conn.commit()
        finally:
            conn.close()


def _mindmap_row(row: dict | None) -> dict | None:
    if not row:
        return None
    item = dict(row)
    item["tree"] = json.loads(item["tree"])
    return item


def mindmap_public(row: dict | None, notes: list | None = None) -> dict | None:
    if not row:
        return None
    data = {
        "id": row["id"],
        "title": row["title"],
        "source_text": row["source_text"],
        "tree": row["tree"] if isinstance(row["tree"], dict) else json.loads(row["tree"]),
        "theme": row["theme"],
        "spacing": row.get("spacing", 34) if hasattr(row, "get") else 34,
        "layout": row.get("layout", "right") if hasattr(row, "get") else "right",
        "revision": row.get("revision", 1) if hasattr(row, "get") else 1,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if notes:
        data["notes"] = notes
    return data


def create_mindmap(user_id: str, title: str, source_text: str, outline: str, tree: dict, theme: str, spacing: int = 34, layout: str = "right") -> dict:
    row = {
        "id": new_id(),
        "user_id": user_id,
        "title": title[:40] or "未命名",
        "source_text": source_text[:8000],
        "outline": outline[:8000],
        "tree": json.dumps(tree, ensure_ascii=False),
        "theme": theme,
        "spacing": spacing,
        "layout": layout,
        "revision": 1,
        "created_at": now(),
        "updated_at": now(),
    }
    with LOCK:
        conn = connect()
        try:
            conn.execute(
                """INSERT INTO mindmaps
                   (id, user_id, title, source_text, outline, tree, theme, spacing, layout, revision, created_at, updated_at)
                   VALUES (:id, :user_id, :title, :source_text, :outline, :tree, :theme, :spacing, :layout, :revision, :created_at, :updated_at)""",
                row,
            )
            conn.commit()
        finally:
            conn.close()
    row["tree"] = tree
    return row


def list_mindmaps(user_id: str) -> list[dict]:
    with LOCK:
        conn = connect()
        try:
            return _all(
                conn,
                "SELECT id, title, theme, created_at, updated_at FROM mindmaps WHERE user_id = ? ORDER BY updated_at DESC LIMIT 80",
                (user_id,),
            )
        finally:
            conn.close()


def get_mindmap(user_id: str, map_id: str) -> dict | None:
    with LOCK:
        conn = connect()
        try:
            return _mindmap_row(_one(conn, "SELECT * FROM mindmaps WHERE id = ? AND user_id = ?", (map_id, user_id)))
        finally:
            conn.close()


def update_mindmap(user_id: str, map_id: str, title: str, tree: dict, theme: str, spacing: int = 34, layout: str = "right") -> dict | None:
    with LOCK:
        conn = connect()
        try:
            found = _one(conn, "SELECT id FROM mindmaps WHERE id = ? AND user_id = ?", (map_id, user_id))
            if not found:
                return None
            moment = now()
            conn.execute(
                "UPDATE mindmaps SET title = ?, tree = ?, theme = ?, spacing = ?, layout = ?, revision = revision + 1, updated_at = ? WHERE id = ? AND user_id = ?",
                (title[:40] or "未命名", json.dumps(tree, ensure_ascii=False), theme, spacing, layout, moment, map_id, user_id),
            )
            conn.commit()
        finally:
            conn.close()
    return get_mindmap(user_id, map_id)


def delete_mindmap(user_id: str, map_id: str) -> bool:
    with LOCK:
        conn = connect()
        try:
            cur = conn.execute("DELETE FROM mindmaps WHERE id = ? AND user_id = ?", (map_id, user_id))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()
