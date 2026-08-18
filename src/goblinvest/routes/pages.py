"""The signed-in pages. Everything but the welcome page is a placeholder today.

The feature pages hold no finance logic — when goblinvest_core is wired in they
become thin renderers over whatever it returns.
"""

from fastapi import APIRouter, HTTPException, Request, status

from goblinvest.auth import Conn, CurrentUser
from goblinvest.nav import Nav, find_dashboard
from goblinvest.templates import page

router = APIRouter()

# url -> heading, blurb. Mirrors nav.BUILTINS; kept here because it's page copy.
PLACEHOLDERS = {
    "/import": (
        "CSV Import",
        "Drop statement CSVs here to load them into your vault. Not wired up yet.",
    ),
    "/month": (
        "Month View",
        "One month of transactions at a time, with categories. Not wired up yet.",
    ),
    "/dashboard": (
        "Main Dashboard",
        "Net worth, balances and returns at a glance. Not wired up yet.",
    ),
}


def _placeholder(request: Request, user, nav, heading: str, blurb: str):
    return page(request, "placeholder.html", user, nav, heading=heading, blurb=blurb)


@router.get("/")
def home(request: Request, user: CurrentUser, nav: Nav):
    return page(request, "welcome.html", user, nav)


@router.get("/import")
def csv_import(request: Request, user: CurrentUser, nav: Nav):
    return _placeholder(request, user, nav, *PLACEHOLDERS["/import"])


@router.get("/month")
def month_view(request: Request, user: CurrentUser, nav: Nav):
    return _placeholder(request, user, nav, *PLACEHOLDERS["/month"])


@router.get("/dashboard")
def main_dashboard(request: Request, user: CurrentUser, nav: Nav):
    return _placeholder(request, user, nav, *PLACEHOLDERS["/dashboard"])


@router.get("/dashboards/{slug}")
def user_dashboard(request: Request, conn: Conn, user: CurrentUser, nav: Nav, slug: str):
    item = find_dashboard(conn, user.id, slug)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such dashboard.")
    return _placeholder(
        request, user, nav, item.label, "Your dashboard. Empty until you add something to it."
    )


@router.get("/healthz")
def healthz():
    return {"status": "ok"}
