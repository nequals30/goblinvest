"""The signed-in pages. Month View is real; the rest are still placeholders.

No finance logic here — these are thin renderers over what core returns.
"""

from fastapi import APIRouter, HTTPException, Request, status

from goblinvest import months, vaults
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


def _rows(frame) -> list[dict]:
    """Core's DataFrame as plain Python dicts, so the template never touches
    pandas types. No arithmetic here — the numbers pass through untouched."""
    return [
        {
            "date": row.date.date(),
            "account": row.account_name,
            "description": row.description,
            "amount": float(row.amount),
            "asset": row.asset,
            "category": row.category,
        }
        for row in frame.itertuples()
    ]


def _selected_month(available: list[months.Month], y: int | None, mo: int | None, step: int):
    """Which month the picker is showing, given the two dropdowns and ‹/›.

    Anything unusable — a stale bookmark, a year/month pair the vault has no
    data for, stepping off either end — resolves to the nearest month that does
    have data rather than erroring.
    """
    if not available:
        return None
    first, last = available[0], available[-1]
    chosen = last
    if y is not None and mo is not None and 1 <= mo <= 12:
        chosen = months.Month(y, mo)
    return months.clamp(chosen.shifted(step), first, last)


@router.get("/month")
def month_view(
    request: Request,
    user: CurrentUser,
    nav: Nav,
    y: int | None = None,
    mo: int | None = None,
    step: int = 0,
):
    try:
        with vaults.open_vault(user.id) as vault:
            summary = vaults.summary(vault)
            available = months.between(summary["first_transaction"], summary["last_transaction"])
            selected = _selected_month(available, y, mo, step)
            rows = (
                _rows(
                    vault.list_transactions(
                        start_date=selected.first_day, end_date=selected.last_day
                    )
                )
                if selected is not None
                else []
            )
    except vaults.VaultMissing:
        return page(request, "month.html", user, nav, no_vault=True, years=[], rows=[])

    return page(
        request,
        "month.html",
        user,
        nav,
        years=sorted({month.year for month in available}, reverse=True),
        month_names=months.names(),
        selected=selected,
        has_older=selected is not None and selected > available[0],
        has_newer=selected is not None and selected < available[-1],
        rows=rows,
    )


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
