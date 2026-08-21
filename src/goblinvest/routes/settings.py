"""Settings: the account page and left-pane organization.

All form posts, no JS — reordering is up/down buttons rather than drag-and-drop,
and every POST answers 303 so a refresh doesn't repost.
"""

from typing import Annotated

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import RedirectResponse

from goblinvest import auth, db
from goblinvest import nav as navmod
from goblinvest.auth import Conn, CurrentUser
from goblinvest.nav import Nav
from goblinvest.routes import vault_settings
from goblinvest.routes.auth import MIN_PASSWORD
from goblinvest.templates import page

router = APIRouter(prefix="/settings")

NAV_URL = "/settings/nav"
ACCOUNT_URL = "/settings/account"


def _redirect(to: str) -> RedirectResponse:
    return RedirectResponse(to, status_code=status.HTTP_303_SEE_OTHER)


@router.get("")
def index(request: Request, user: CurrentUser, nav: Nav):
    return page(request, "settings.html", user, nav, kinds=list(vault_settings.KINDS.values()))


# --- account ----------------------------------------------------------------


@router.get("/account")
def account(request: Request, user: CurrentUser, nav: Nav, changed: bool = False):
    return page(
        request,
        "settings_account.html",
        user,
        nav,
        changed=changed,
        min_password=MIN_PASSWORD,
    )


def _password_problem(user: db.User, current: str, new: str, confirm: str) -> str | None:
    if not auth.verify_password(current, user.password_hash):
        return "Current password is incorrect."
    if len(new) < MIN_PASSWORD:
        return f"New password must be at least {MIN_PASSWORD} characters."
    if new != confirm:
        return "New passwords don't match."
    if new == current:
        return "New password must differ from the current one."
    return None


@router.post("/account/password")
def change_password(
    request: Request,
    conn: Conn,
    user: CurrentUser,
    nav: Nav,
    current_password: Annotated[str, Form()],
    new_password: Annotated[str, Form()],
    confirm_password: Annotated[str, Form()],
):
    problem = _password_problem(user, current_password, new_password, confirm_password)
    if problem is not None:
        return page(
            request,
            "settings_account.html",
            user,
            nav,
            error=problem,
            min_password=MIN_PASSWORD,
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    db.update_password(conn, user.id, auth.hash_password(new_password))
    token = request.cookies.get(auth.SESSION_COOKIE) or ""
    db.delete_other_sessions(conn, user.id, token)
    return _redirect(f"{ACCOUNT_URL}?changed=1")


# --- left pane --------------------------------------------------------------


def _nav_page(request: Request, conn, user, nav, error: str = "", status_code: int = 200):
    return page(
        request,
        "settings_nav.html",
        user,
        nav,
        items=navmod.all_items(conn, user.id),
        max_label=navmod.MAX_LABEL,
        error=error,
        status_code=status_code,
    )


@router.get("/nav")
def nav_settings(request: Request, conn: Conn, user: CurrentUser, nav: Nav):
    return _nav_page(request, conn, user, nav)


@router.post("/nav/{item_id}/move")
def move_item(conn: Conn, user: CurrentUser, item_id: int, direction: Annotated[str, Form()]):
    navmod.move(conn, user.id, item_id, -1 if direction == "up" else 1)
    return _redirect(NAV_URL)


@router.post("/nav/{item_id}/visibility")
def set_visibility(conn: Conn, user: CurrentUser, item_id: int, hidden: Annotated[int, Form()]):
    navmod.set_hidden(conn, user.id, item_id, bool(hidden))
    return _redirect(NAV_URL)


@router.post("/nav/{item_id}/delete")
def delete_item(conn: Conn, user: CurrentUser, item_id: int):
    navmod.delete(conn, user.id, item_id)
    return _redirect(NAV_URL)


@router.post("/nav/dashboards")
def create_dashboard(
    request: Request, conn: Conn, user: CurrentUser, nav: Nav, label: Annotated[str, Form()]
):
    if not label.strip():
        return _nav_page(
            request, conn, user, nav, "Give the dashboard a name.", status.HTTP_400_BAD_REQUEST
        )
    navmod.create_dashboard(conn, user.id, label)
    return _redirect(NAV_URL)
