"""Signup, login, logout — plain HTML form posts, no JSON, no JS."""

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import RedirectResponse

from goblinvest import auth, db, keyring, nav, storage, vaults
from goblinvest.auth import Conn, MaybeUser
from goblinvest.templates import templates

router = APIRouter()

MIN_USERNAME = 1
MAX_USERNAME = 64
MIN_PASSWORD = 8

Username = Annotated[str, Form()]
Password = Annotated[str, Form()]


def _form_error(request: Request, template: str, message: str, username: str = ""):
    return templates.TemplateResponse(
        request,
        template,
        {"error": message, "username": username},
        status_code=status.HTTP_400_BAD_REQUEST,
    )


def _redirect(to: str) -> RedirectResponse:
    # 303 so the browser follows a POST with a GET.
    return RedirectResponse(to, status_code=status.HTTP_303_SEE_OTHER)


def _validate(username: str, password: str) -> str | None:
    username = username.strip()
    if not (MIN_USERNAME <= len(username) <= MAX_USERNAME):
        return f"Username must be {MIN_USERNAME}-{MAX_USERNAME} characters."
    if len(password) < MIN_PASSWORD:
        return f"Password must be at least {MIN_PASSWORD} characters."
    return None


@router.get("/login")
def login_form(request: Request, user: MaybeUser):
    if user is not None:
        return _redirect("/")
    return templates.TemplateResponse(request, "login.html", {})


@router.post("/login")
def login(request: Request, conn: Conn, username: Username, password: Password):
    username = username.strip()
    user = db.find_user_by_username(conn, username)
    if user is None or not auth.verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Invalid username or password.", "username": username},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    # An encrypted vault made at signup uses the account password, so logging
    # in is normally the whole unlock — one password, no second prompt. A vault
    # with a password of its own (one brought from a REPL, or made before a
    # password change) just fails here silently and gets /unlock on the first
    # page that wants it.
    if vaults.needs_password(user.id) and not keyring.is_unlocked(user.id):
        vaults.unlock(user.id, password)

    token = auth.create_session(conn, user.id)
    response = _redirect("/")
    auth.set_session_cookie(response, token)
    return response


@router.get("/signup")
def signup_form(request: Request, user: MaybeUser):
    if user is not None:
        return _redirect("/")
    return templates.TemplateResponse(request, "signup.html", {})


@router.post("/signup")
def signup(
    request: Request,
    conn: Conn,
    username: Username,
    password: Password,
    encrypt: Annotated[bool, Form()] = False,
):
    username = username.strip()
    problem = _validate(username, password)
    if problem is not None:
        return _form_error(request, "signup.html", problem, username)

    try:
        user_id = db.insert_user(conn, username, auth.hash_password(password))
    except sqlite3.IntegrityError:
        return _form_error(request, "signup.html", "That username is taken.", username)

    try:
        storage.provision_user_storage(user_id)
        # The account password doubles as the vault password, so there is only
        # one to remember and logging in unlocks the vault. Core has no way to
        # change a vault's password, so an account password change leaves the
        # vault on the old one — see /settings/account. Also creates
        # vault_adjustments/, armored to match.
        vaults.create_vault(user_id, password=password if encrypt else None)
    except OSError:
        # Don't leave a user who has no home on disk.
        db.delete_user(conn, user_id)
        conn.commit()
        keyring.forget(user_id)
        return _form_error(
            request, "signup.html", "Could not set up your files. Try again.", username
        )

    nav.ensure_builtins(conn, user_id)

    token = auth.create_session(conn, user_id)
    response = _redirect("/")
    auth.set_session_cookie(response, token)
    return response


@router.post("/logout")
def logout(request: Request, conn: Conn, user: MaybeUser):
    token = request.cookies.get(auth.SESSION_COOKIE)
    if token:
        db.delete_session(conn, token)
    if user is not None:
        # Logging out puts the key away too. It also locks this user's other
        # sessions, which is the safe direction to be wrong in.
        keyring.forget(user.id)
    response = _redirect("/login")
    auth.clear_session_cookie(response)
    return response
