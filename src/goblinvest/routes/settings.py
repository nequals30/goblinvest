"""Settings: the account page and left-pane organization.

All form posts, no JS — reordering is up/down buttons rather than drag-and-drop,
and every POST answers 303 so a refresh doesn't repost.
"""

from typing import Annotated

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import RedirectResponse

from goblinvest import auth, db, vaults
from goblinvest import nav as navmod
from goblinvest.auth import Conn, CurrentUser
from goblinvest.nav import Nav
from goblinvest.routes import vault_settings
from goblinvest.routes.auth import MIN_PASSWORD
from goblinvest.templates import page

router = APIRouter(prefix="/settings")

INDEX_URL = "/settings"
NAV_URL = "/settings/nav"
ACCOUNT_URL = "/settings/account"


def _redirect(to: str) -> RedirectResponse:
    return RedirectResponse(to, status_code=status.HTTP_303_SEE_OTHER)


def _index_page(request: Request, user, nav, *, notice: str = "", error: str = "", status_code=200):
    return page(
        request,
        "settings.html",
        user,
        nav,
        kinds=list(vault_settings.KINDS.values()),
        notice=notice,
        error=error,
        status_code=status_code,
    )


@router.get("")
def index(
    request: Request,
    user: CurrentUser,
    nav: Nav,
    applied: bool = False,
    cats: int = 0,
    dates: int = 0,
):
    notice = _applied_notice(cats, dates) if applied else ""
    return _index_page(request, user, nav, notice=notice)


# --- the adjustments files ---------------------------------------------------
#
# Categories and effective dates are both recorded in files that are the user's
# own and get edited outside this app; this is the button that makes the vault
# match them again. It sits here rather than on the categories page because it
# is no longer only about categories (user, 2026-08-30).


def _applied_notice(cats: int, dates: int) -> str:
    """Core hands back the file rows that named no transaction — a typo, or a
    description the bank reworded. Saying nothing about them would be the
    silent failure core went out of its way to avoid."""
    unmatched = [
        vault_settings.plural(count, noun)
        for count, noun in ((cats, "exception row"), (dates, "date row"))
        if count
    ]
    if not unmatched:
        return "Adjustments applied."
    return f"Adjustments applied. {' and '.join(unmatched)} matched no transaction."


@router.post("/adjustments/apply")
def apply_adjustments(request: Request, user: CurrentUser, nav: Nav):
    try:
        with vaults.open_vault(user.id) as vault:
            cats, dates = vaults.apply_adjustments(vault)
    except vaults.VaultMissing:
        return _redirect(INDEX_URL)
    except vaults.CORE_ERRORS as exc:
        return _index_page(
            request,
            user,
            nav,
            error=vaults.explain(exc),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return _redirect(f"{INDEX_URL}?applied=1&cats={cats}&dates={dates}")


# --- account ----------------------------------------------------------------


def _account_page(request, user, nav, **context):
    return page(
        request,
        "settings_account.html",
        user,
        nav,
        # An encrypted vault keeps whatever password it was made with: core has
        # no call that re-keys one, and that is deferred (CLAUDE.md, Encryption),
        # so changing the account password here leaves the two different. The
        # page has to say so.
        encrypted=vaults.needs_password(user.id),
        vault_encrypted=vaults.is_encrypted(user.id),
        min_password=MIN_PASSWORD,
        **context,
    )


@router.get("/account")
def account(request: Request, user: CurrentUser, nav: Nav, changed: bool = False):
    return _account_page(request, user, nav, changed=changed)


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
        return _account_page(
            request, user, nav, error=problem, status_code=status.HTTP_400_BAD_REQUEST
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
