"""/unlock: the vault password, for a vault whose password isn't the account's.

Logged in means unlocked, and normally the login password *is* the vault
password, so this page is never seen. It is for the one case where they differ
— a vault brought from a REPL, or one left on its old password by an account
password change, which core can't re-key. Login sends the browser here when its
password didn't open the vault.
"""

from typing import Annotated

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import RedirectResponse

from goblinvest import keyring, vaults
from goblinvest.auth import CurrentUser
from goblinvest.nav import Nav
from goblinvest.templates import page

router = APIRouter()


def safe_next(target: str) -> str:
    """A `?next=` reduced to somewhere on this site, or the welcome page.

    `//host` is a protocol-relative URL, so "starts with a slash" is not on its
    own enough to keep an open redirect out.
    """
    if target.startswith("/") and not target.startswith("//"):
        return target
    return "/"


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
