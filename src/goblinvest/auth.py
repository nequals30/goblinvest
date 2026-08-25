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
    db.insert_session(conn, token, user_id, settings().session_ttl_days, db.new_csrf_token())
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
    session = db.find_session(conn, token)
    if session is None:
        return None
    # Stashed here so `templates.page()` can put it in every form without each
    # route having to carry it through. Nothing else reads request.state.
    request.state.csrf_token = session.csrf_token
    return session.user


MaybeUser = Annotated["db.User | None", Depends(current_user)]


def require_user(user: MaybeUser) -> db.User:
    if user is None:
        raise NotAuthenticated
    return user


CurrentUser = Annotated[db.User, Depends(require_user)]


# --- CSRF -------------------------------------------------------------------
#
# Every form POSTed by a signed-in browser carries a hidden field holding the
# session's own CSRF token; `csrf_guard` runs app-wide and refuses the request
# when it doesn't match. `SameSite=Lax` already blocks a cross-site form post
# from carrying the cookie in current browsers, so this is the second lock, not
# the only one.
#
# /login and /signup are exempt: they have no session yet, so there is no token
# to check. The residue is login-CSRF (an attacker making a victim's browser log
# in as *them*), which needs a pre-session cookie to close and isn't worth one
# here — nothing is written under the attacker's account that the victim can't
# see they're in.

CSRF_FIELD = "csrf_token"
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
CSRF_EXEMPT = frozenset({"/login", "/signup"})


class CsrfInvalid(Exception):
    """Raised by `csrf_guard`; `main.py` turns it into a 403 page."""


async def csrf_guard(request: Request, conn: Conn) -> None:
    """Reject an unsafe request whose form doesn't carry the session's token.

    Async because it has to `await request.form()`; Starlette caches the parsed
    form on the request, so the endpoint's own `Form()` parameters still see it.
    """
    if request.method not in UNSAFE_METHODS or request.url.path in CSRF_EXEMPT:
        return
    cookie = request.cookies.get(SESSION_COOKIE)
    if not cookie:
        # Not signed in: there's no session to ride, so nothing to protect.
        return
    session = db.find_session(conn, cookie)
    if session is None:
        return  # a stale cookie; the route will send them to /login anyway

    submitted = (await request.form()).get(CSRF_FIELD, "")
    if not isinstance(submitted, str) or not hmac.compare_digest(submitted, session.csrf_token):
        raise CsrfInvalid
