"""The signed-in pages. Month View is real; the rest are still placeholders.

No finance logic here — these are thin renderers over what core returns.
"""

import math
from collections.abc import Sequence
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Query, Request, status
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


def _number(value) -> float | None:
    """A core float as a plain one, or `None` where core had no answer.

    `market_value` is NaN for an asset with no stored price by that date. NaN is
    a pandas-ism, and this is the boundary it stops at.
    """
    number = float(value)
    return None if math.isnan(number) else number


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
            "market_value": _number(row.market_value),
            "category": row.category,
        }
        for row in frame.itertuples()
    ]


# --- filtering the month's rows ----------------------------------------------
#
# Selecting which of the month's rows to show is not finance logic — nothing
# here computes anything, it only decides what the table lists. Core is asked
# for one month (~30 rows), so filtering that in memory costs nothing; a table
# big enough for it to matter is the point at which `list_transactions` should
# take the filter itself (request #6's neighbour).


def _keep(row: dict, cats: Sequence[str], accts: Sequence[str]) -> bool:
    """An empty pick means "all", not "none" — that's what an untouched
    checkbox list submits, and it has to read as no filter at all."""
    return (not cats or row["category"] in cats) and (not accts or row["account"] in accts)


def _options(rows: list[dict], key: str, picked: Sequence[str]) -> list[str]:
    """What one filter offers: the values in this month, plus anything already
    picked that isn't in it — so a filter never disappears from the control
    that is applying it."""
    return sorted({row[key] for row in rows} | set(picked), key=str.lower)


def _filters(rows: list[dict], cats: Sequence[str], accts: Sequence[str]) -> list[dict]:
    return [
        {
            "name": name,
            "label": label,
            "options": _options(rows, key, picked),
            "picked": set(picked),
        }
        # Account first: it's the coarser cut, and the one reached for more.
        for name, label, key, picked in (
            ("acct", "account", "account", accts),
            ("cat", "category", "category", cats),
        )
    ]


def _filter_qs(cats: Sequence[str], accts: Sequence[str]) -> str:
    """The picks as a query string, for every link and form that has to carry
    them across a page load."""
    return urlencode([("cat", c) for c in cats] + [("acct", a) for a in accts])


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
    cats: Sequence[str] = (),
    accts: Sequence[str] = (),
    error: str = "",
    status_code: int = 200,
):
    """Month View, rendered from scratch. The GET calls this; so does any POST
    that has something to say instead of redirecting.

    `edit` is the transaction whose category cell is open for editing. Rows that
    are still unclassified always render the editor; a classified row renders
    its category as text until its pencil is clicked, which is a plain GET back
    to this page. That keeps one `<select>` on the page instead of one per row.

    `cats` and `accts` are the filter picks, straight from the query string.
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
            base_asset = vaults.base_asset(vault)
    except vaults.VaultMissing:
        return page(
            request,
            "month.html",
            user,
            nav,
            no_vault=True,
            years=[],
            rows=[],
            filters=[],
            filter_qs="",
        )

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
        rows=[row for row in rows if _keep(row, cats, accts)],
        n_total=len(rows),
        filters=_filters(rows, cats, accts),
        filter_qs=_filter_qs(cats, accts),
        base_asset=base_asset,
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
    # The filter picks. Repeatable query params — ?cat=rent&cat=groceries — and
    # `default_factory` rather than `= []`, so the absent case is a fresh list
    # per request. They come first only because a parameter with no default
    # can't follow one that has it.
    cat: Annotated[list[str], Query(default_factory=list)],
    acct: Annotated[list[str], Query(default_factory=list)],
    y: int | None = None,
    mo: int | None = None,
    step: int = 0,
    edit: int | None = None,
):
    return _month_page(request, user, nav, y, mo, step, edit=edit, cats=cat, accts=acct)


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
    # The filter picks, read off the form's *action* rather than out of hidden
    # inputs: it's one state for the whole page, and hidden fields would repeat
    # it in every row of the table.
    cat: Annotated[list[str], Query(default_factory=list)],
    acct: Annotated[list[str], Query(default_factory=list)],
    # Defaulted, not required: an empty <select> submits as a *missing* field,
    # and a 422 is the wrong answer to "you didn't pick one".
    category: Annotated[str, Form()] = "",
    tid: Annotated[int, Form()] = 0,
):
    # The row's id only steers the page: it lands the browser back on the row
    # (#t<id>), and on an error it keeps that row's editor open.
    qs = _filter_qs(cat, acct)
    back = f"/month?y={y}&mo={mo}" + (f"&{qs}" if qs else "") + (f"#t{tid}" if tid else "")
    if not category.strip():
        return _month_page(
            request,
            user,
            nav,
            y,
            mo,
            edit=tid,
            cats=cat,
            accts=acct,
            error="Pick a category first.",
            status_code=400,
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
            request,
            user,
            nav,
            y,
            mo,
            edit=tid,
            cats=cat,
            accts=acct,
            error=vaults.explain(exc),
            status_code=400,
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
