"""Password hashing, server-side sessions, and the FastAPI dependencies.

Sessions are opaque random tokens in an HttpOnly cookie, looked up in the
`sessions` table — nothing about the user is stored client-side. Passwords are
PBKDF2-HMAC-SHA256 via stdlib `hashlib` (no dependency needed; argon2 would be
the upgrade if this ever faces the open internet).
"""

import base64
import hashlib
import hmac
import secrets
import sqlite3
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request, Response

from goblinvest import db
from goblinvest.config import settings

SESSION_COOKIE = "gv_session"
_ALGO = "pbkdf2_sha256"
_SALT_BYTES = 16
_DKLEN = 32


class NotAuthenticated(Exception):
    """Raised by `require_user`; `main.py` turns it into a redirect to /login."""


# --- passwords --------------------------------------------------------------


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def hash_password(password: str, *, iters: int | None = None) -> str:
    iters = iters if iters is not None else settings().pbkdf2_iters
    salt = secrets.token_bytes(_SALT_BYTES)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iters, _DKLEN)
    return f"{_ALGO}${iters}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters_s, salt_s, dk_s = stored.split("$")
        if algo != _ALGO:
            return False
        salt, expected = _unb64(salt_s), _unb64(dk_s)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iters_s), len(expected))
    except (ValueError, TypeError, base64.binascii.Error):
        return False
    return hmac.compare_digest(actual, expected)


# --- sessions ---------------------------------------------------------------


def create_session(conn: sqlite3.Connection, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    db.insert_session(conn, token, user_id, settings().session_ttl_days)
    return token


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings().session_ttl_days * 24 * 60 * 60,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings().is_prod,
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        SESSION_COOKIE, path="/", httponly=True, samesite="lax", secure=settings().is_prod
    )


# --- dependencies -----------------------------------------------------------


def connection() -> Iterator[sqlite3.Connection]:
    conn = db.connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


Conn = Annotated[sqlite3.Connection, Depends(connection)]


def current_user(request: Request, conn: Conn) -> db.User | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    return db.find_user_by_token(conn, token)


MaybeUser = Annotated["db.User | None", Depends(current_user)]


def require_user(user: MaybeUser) -> db.User:
    if user is None:
        raise NotAuthenticated
    return user


CurrentUser = Annotated[db.User, Depends(require_user)]
