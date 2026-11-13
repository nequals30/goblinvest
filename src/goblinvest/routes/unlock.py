"""Unlocking an encrypted vault: /unlock and /lock.

An encrypted vault needs its password once per process (see `keyring.py`), so
a page that wants vault data and can't get at it raises `VaultLocked`, and
`main.py` sends the browser here — exactly the shape `/login` already has for
`NotAuthenticated`. The difference is that you are signed in by the time you
get here, so this is a normal page with the usual chrome, and its POST carries
the usual CSRF token.
"""

from typing import Annotated
from urllib.parse import urlparse

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import RedirectResponse

from goblinvest import keyring, vaults
from goblinvest.auth import CurrentUser
from goblinvest.nav import Nav
from goblinvest.templates import page

router = APIRouter()

UNLOCK_URL = "/unlock"


def safe_next(target: str) -> str:
    """A `?next=` reduced to somewhere on this site, or the welcome page.

    `//host` is a protocol-relative URL, so "starts with a slash" is not on its
    own enough to keep an open redirect out.
    """
    if target.startswith("/") and not target.startswith("//"):
        return target
    return "/"


def next_for(request: Request) -> str:
    """Where to come back to after unlocking, for a request that hit a locked
    vault. A GET is its own answer; anything else can only guess from the page
    the form was on, because the body is lost in the redirect either way."""
    if request.method == "GET":
        query = f"?{request.url.query}" if request.url.query else ""
        return safe_next(request.url.path + query)
    return safe_next(urlparse(request.headers.get("referer", "")).path or "/")


def unlock_url(request: Request) -> str:
    target = next_for(request)
    return UNLOCK_URL if target == "/" else f"{UNLOCK_URL}?next={target}"


def _redirect(to: str) -> RedirectResponse:
    return RedirectResponse(to, status_code=status.HTTP_303_SEE_OTHER)


def _unlock_page(request, user, nav, target: str, *, error: str = "", status_code: int = 200):
    return page(
        request,
        "unlock.html",
        user,
        nav,
        next=target,
        error=error,
        status_code=status_code,
    )


@router.get("/unlock")
def unlock_form(request: Request, user: CurrentUser, nav: Nav, next: str = "/"):
    target = safe_next(next)
    # Nothing to unlock: either the vault is in the clear or it is already
    # open, and in both cases the page they asked for will now render.
    if not vaults.needs_password(user.id) or keyring.is_unlocked(user.id):
        return _redirect(target)
    return _unlock_page(request, user, nav, target)


@router.post("/unlock")
def do_unlock(
    request: Request,
    user: CurrentUser,
    nav: Nav,
    password: Annotated[str, Form()],
    next: Annotated[str, Form()] = "/",
):
    target = safe_next(next)
    if not vaults.unlock(user.id, password):
        return _unlock_page(
            request,
            user,
            nav,
            target,
            error="That password doesn't open this vault.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return _redirect(target)


@router.post("/lock")
def lock(user: CurrentUser, next: Annotated[str, Form()] = "/settings"):
    keyring.forget(user.id)
    return _redirect(safe_next(next))
