"""The signed-in pages. Month View is real; the rest are still placeholders.

No finance logic here — these are thin renderers over what core returns.
"""

from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse

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


def _redirect(to: str) -> RedirectResponse:
    return RedirectResponse(to, status_code=status.HTTP_303_SEE_OTHER)


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
            "id": int(row.transaction_id),
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


def _month_page(
    request: Request,
    user,
    nav,
    y: int | None = None,
    mo: int | None = None,
    step: int = 0,
    *,
    edit: int | None = None,
    error: str = "",
    status_code: int = 200,
):
    """Month View, rendered from scratch. The GET calls this; so does any POST
    that has something to say instead of redirecting.

    `edit` is the transaction whose category cell is open for editing. Rows that
    are still unclassified always render the editor; a classified row renders
    its category as text until its pencil is clicked, which is a plain GET back
    to this page. That keeps one `<select>` on the page instead of one per row.
    """
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
            # Every defined category, for the dropdown in each row.
            categories = [str(name) for name in vault.list_categories()["category_name"]]
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
        categories=categories,
        edit=edit,
        error=error,
        status_code=status_code,
    )


@router.get("/month")
def month_view(
    request: Request,
    user: CurrentUser,
    nav: Nav,
    y: int | None = None,
    mo: int | None = None,
    step: int = 0,
    edit: int | None = None,
):
    return _month_page(request, user, nav, y, mo, step, edit=edit)


# --- categorizing from the month table ---------------------------------------
#
# Two buttons per row, two core calls. "all like this" is a rule keyed on the
# description; "just this one" is an exception keyed on the whole transaction.
# Which one runs is which button was pressed — nothing here decides it, and
# nothing here knows what a category means.


@router.post("/month/categorize")
def categorize(
    request: Request,
    user: CurrentUser,
    nav: Nav,
    y: Annotated[int, Form()],
    mo: Annotated[int, Form()],
    scope: Annotated[str, Form()],
    account: Annotated[str, Form()],
    date: Annotated[str, Form()],
    description: Annotated[str, Form()],
    amount: Annotated[float, Form()],
    asset: Annotated[str, Form()],
    # Defaulted, not required: an empty <select> submits as a *missing* field,
    # and a 422 is the wrong answer to "you didn't pick one".
    category: Annotated[str, Form()] = "",
    tid: Annotated[int, Form()] = 0,
):
    # The row's id only steers the page: it lands the browser back on the row
    # (#t<id>), and on an error it keeps that row's editor open.
    back = f"/month?y={y}&mo={mo}" + (f"#t{tid}" if tid else "")
    if not category.strip():
        return _month_page(
            request, user, nav, y, mo, edit=tid, error="Pick a category first.", status_code=400
        )
    try:
        with vaults.open_vault(user.id) as vault:
            if scope == "all":
                vault.set_category_rule(description, category)
            else:
                # The natural key of one transaction, straight back out of the
                # row it was rendered from. The asset goes with it: core reads
                # `assets=None` as the base currency, not "any".
                vault.set_category_exception(
                    account, date, description, amount, category, assets=asset
                )
    except vaults.VaultMissing:
        return _redirect(back)
    except vaults.CORE_ERRORS as exc:
        return _month_page(
            request, user, nav, y, mo, edit=tid, error=vaults.explain(exc), status_code=400
        )
    return _redirect(back)


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
