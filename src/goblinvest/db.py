"""The users/sessions database (stdlib sqlite3).

One connection per request, opened by the `connection` dependency in
`goblinvest.auth`. Uvicorn runs sync endpoints in a threadpool, so a shared
module-level connection would be handed between threads — hence per-request.
"""

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from goblinvest.config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);
"""


@dataclass(frozen=True)
class User:
    id: int
    username: str
    password_hash: str


def connect(path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or settings().db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn


def init_db() -> None:
    """Create the schema if missing and sweep expired sessions. Idempotent."""
    settings().ensure_dirs()
    # sqlite3's context manager commits but does not close — hence closing().
    with closing(connect()) as conn, conn:
        conn.executescript(SCHEMA)
        conn.execute("DELETE FROM sessions WHERE expires_at <= datetime('now');")


def _user(row: sqlite3.Row | None) -> User | None:
    if row is None:
        return None
    return User(id=row["id"], username=row["username"], password_hash=row["password_hash"])


def find_user_by_username(conn: sqlite3.Connection, username: str) -> User | None:
    row = conn.execute(
        "SELECT id, username, password_hash FROM users WHERE username = ?", (username,)
    ).fetchone()
    return _user(row)


def find_user_by_token(conn: sqlite3.Connection, token: str) -> User | None:
    row = conn.execute(
        """
        SELECT u.id, u.username, u.password_hash
        FROM sessions s JOIN users u ON u.id = s.user_id
        WHERE s.token = ? AND s.expires_at > datetime('now')
        """,
        (token,),
    ).fetchone()
    return _user(row)


def insert_user(conn: sqlite3.Connection, username: str, password_hash: str) -> int:
    cur = conn.execute(
        "INSERT INTO users(username, password_hash) VALUES (?, ?)", (username, password_hash)
    )
    return int(cur.lastrowid)


def delete_user(conn: sqlite3.Connection, user_id: int) -> None:
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))


def insert_session(conn: sqlite3.Connection, token: str, user_id: int, ttl_days: int) -> None:
    conn.execute(
        "INSERT INTO sessions(token, user_id, expires_at) VALUES (?, ?, datetime('now', ?))",
        (token, user_id, f"+{int(ttl_days)} days"),
    )


def delete_session(conn: sqlite3.Connection, token: str) -> None:
    conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
