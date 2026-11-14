"""The signed-in pages. Month View and Find Transactions are real; the rest are
still placeholders.

No finance logic here — these are thin renderers over what core returns.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
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
            # `date` is the date the transaction *counts* on and `original_date`
            # the one the statement gave it — the same when nothing has been
            # moved. Core keys every adjustment on the original, so that's the
            # one the row's forms send back; `date` is only what's displayed.
            "date": row.date.date(),
            "original_date": row.original_date.date(),
            "account": row.account_name,
            "description": row.description,
            "amount": float(row.amount),
            # The account's ownership share, which core hands over on every row.
            # It's the amount column's footnote while the share toggle is on:
            # core multiplies the market value and leaves the amount whole, so a
            # joint row's two numbers stop matching and this says why.
            "share": float(row.ownership_share),
            "asset": row.asset,
            "market_value": _number(row.market_value),
            "category": row.category,
        }
        for row in frame.itertuples()
    ]


def _ordered(rows: list[dict]) -> list[dict]:
    """The month's rows, by date and then by how big they are.

    Core's order is date then account name; the date stays primary and the rest
    is replaced by the size of the market value, biggest first. Offsetting
    transactions are the same size — a fridge bought for 1,000 and the refund
    that took it back — so they land next to each other instead of a column
    apart (user, 2026-08-30). The magnitude is never displayed and never added
    to anything: this is which order to list rows in, not a number about money.
    An unpriced row has no size, so it sits at the bottom of its date.
    """
    return sorted(rows, key=lambda row: (row["date"], -abs(row["market_value"] or 0.0)))


def _total(rows: list[dict]) -> float:
    """The market values of the rows on screen, added up.

    Money arithmetic in this layer, which is normally core's alone — the user's
    call (2026-08-30), and the reason is the filter: the number has to answer to
    which rows are being *shown*, and what a UI filter is showing is not
    something core is in a position to know. Unpriced rows have no value to
    contribute and sit the sum out.
    """
    return sum(row["market_value"] or 0.0 for row in rows)


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


def _page_qs(cats: Sequence[str], accts: Sequence[str], share: bool = False) -> str:
    """How the page is being looked at — the filter picks and the ownership
    share toggle — as a query string, for every link and form that has to carry
    it across a page load."""
    pairs = [("cat", c) for c in cats] + [("acct", a) for a in accts]
    if share:
        pairs.append(("share", "1"))
    return urlencode(pairs)


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
    redate: int | None = None,
    moved: str = "",
    cats: Sequence[str] = (),
    accts: Sequence[str] = (),
    share: bool = False,
    error: str = "",
    status_code: int = 200,
):
    """Month View, rendered from scratch. The GET calls this; so does any POST
    that has something to say instead of redirecting.

    `edit` is the transaction whose category cell is open for editing. Rows that
    are still unclassified always render the editor; a classified row renders
    its category as text until its pencil is clicked, which is a plain GET back
    to this page. That keeps one `<select>` on the page instead of one per row.

    `redate` is the same idea for the date cell: the one transaction whose date
    is open for editing. Every date is a link to this page with its own id in
    `?redate=`, so the affordance costs no column width.

    `moved` is a `"YYYY-MM"` key: the month a transaction just landed in when it
    was moved *out* of the one being shown, so the page can say where it went
    instead of the row silently vanishing.

    `cats` and `accts` are the filter picks, straight from the query string.

    `share` is the ownership-share toggle: core values a joint account's
    transaction whole, and this asks it for your fraction of each one instead.
    Off by default — the whole transaction is what the statement said.
    """
    try:
        with vaults.open_vault(user.id) as vault:
            summary = vaults.summary(vault)
            available = months.between(summary["first_transaction"], summary["last_transaction"])
            selected = _selected_month(available, y, mo, step)
            rows = (
                _ordered(
                    _rows(
                        vault.list_transactions(
                            start_date=selected.first_day,
                            end_date=selected.last_day,
                            apply_ownership_share=share,
                        )
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
            page_qs="",
        )

    shown = [row for row in rows if _keep(row, cats, accts)]
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
        rows=shown,
        total=_total(shown),
        n_total=len(rows),
        filters=_filters(rows, cats, accts),
        filtering=bool(cats or accts),
        page_qs=_page_qs(cats, accts, share),
        share=share,
        base_asset=base_asset,
        categories=categories,
        edit=edit,
        redate=redate,
        moved_month=months.parse(moved),
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
    redate: int | None = None,
    moved: str = "",
    share: bool = False,
):
    return _month_page(
        request,
        user,
        nav,
        y,
        mo,
        step,
        edit=edit,
        redate=redate,
        moved=moved,
        cats=cat,
        accts=acct,
        share=share,
    )


# --- categorizing from the month table ---------------------------------------
#
# Two buttons per row, two core calls. "all like this" is a rule keyed on the
# description; "just this one" is an exception keyed on the whole transaction.
# Which one runs is which button was pressed — nothing here decides it, and
# nothing here knows what a category means.
#
# Picking "unclassified" is how a decision is *taken back*: core reads a
# category of None as "remove the rule" / "remove the exception", and
# "unclassified" is the name it reserves for a transaction no file claims. So
# the option and the None are the same thing said twice, once to the user and
# once to core.

UNCLASSIFIED = "unclassified"


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
    share: bool = False,
    # Defaulted, not required: an empty <select> submits as a *missing* field,
    # and a 422 is the wrong answer to "you didn't pick one".
    category: Annotated[str, Form()] = "",
    tid: Annotated[int, Form()] = 0,
):
    # The row's id only steers the page: it lands the browser back on the row
    # (#t<id>), and on an error it keeps that row's editor open.
    qs = _page_qs(cat, acct, share)
    back = f"/month?y={y}&mo={mo}" + (f"&{qs}" if qs else "") + (f"#t{tid}" if tid else "")
    error = _categorize(user.id, scope, account, date, description, amount, asset, category)
    if error:
        return _month_page(
            request,
            user,
            nav,
            y,
            mo,
            edit=tid,
            cats=cat,
            accts=acct,
            share=share,
            error=error,
            status_code=400,
        )
    return _redirect(back)


def _categorize(
    user_id: int,
    scope: str,
    account: str,
    date: str,
    description: str,
    amount: float,
    asset: str,
    category: str,
) -> str:
    """One row's category decision, made in the vault — for Month View and Find
    Transactions alike. Returns the error to show, or "" when there's nothing to
    say and the page should redirect."""
    picked = category.strip()
    if not picked:
        return "Pick a category first."
    # "unclassified" is the one option that isn't a category — it's the absence
    # of one, which core spells `None`.
    wanted = None if picked.lower() == UNCLASSIFIED else picked
    try:
        with vaults.open_vault(user_id) as vault:
            if scope == "all":
                vault.set_category_rule(description, wanted)
            else:
                # The natural key of one transaction, straight back out of the
                # row it was rendered from. The asset goes with it: core reads
                # `assets=None` as the base currency, not "any".
                vault.set_category_exception(
                    account, date, description, amount, wanted, assets=asset
                )
    except vaults.VaultMissing:
        return ""
    except vaults.CORE_ERRORS as exc:
        return vaults.explain(exc)
    return ""


# --- moving a transaction to the date it belongs on --------------------------
#
# Same shape as categorizing: the row's natural key rides in hidden inputs and
# core does the rest. "save" records an effective date, "reset" drops the
# decision and the transaction goes back to the date its statement gave it.
# Nothing here decides what a date means — the two buttons *are* the choice
# between passing a date and passing None.


@router.post("/month/set-date")
def set_date(
    request: Request,
    user: CurrentUser,
    nav: Nav,
    y: Annotated[int, Form()],
    mo: Annotated[int, Form()],
    scope: Annotated[str, Form()],
    account: Annotated[str, Form()],
    # The transaction's *original* date, which is what core names it by even
    # after it has been moved — sending the effective one would match nothing
    # the second time a row is edited.
    date: Annotated[str, Form()],
    description: Annotated[str, Form()],
    amount: Annotated[float, Form()],
    asset: Annotated[str, Form()],
    cat: Annotated[list[str], Query(default_factory=list)],
    acct: Annotated[list[str], Query(default_factory=list)],
    share: bool = False,
    # Defaulted for the same reason as `category` in categorize: a control that
    # submits nothing should get an answer, not a 422.
    effective: Annotated[str, Form()] = "",
    tid: Annotated[int, Form()] = 0,
):
    qs = _page_qs(cat, acct, share)
    params = f"y={y}&mo={mo}" + (f"&{qs}" if qs else "")
    error = _set_date(user.id, scope, account, date, description, amount, asset, effective)
    if error:
        return _month_page(
            request,
            user,
            nav,
            y,
            mo,
            redate=tid,
            cats=cat,
            accts=acct,
            share=share,
            error=error,
            status_code=400,
        )
    wanted = "" if scope == "clear" else effective.strip()
    # A row moved out of the month on screen is simply gone from it, so the page
    # it lands back on says where it went. Core accepts any date pandas can
    # parse, so an unrecognised one just means no notice.
    landed = months.parse((wanted or date)[: months.KEY_LENGTH])
    if landed is not None and landed != months.Month(y, mo):
        return _redirect(f"/month?{params}&moved={landed.key}")
    return _redirect(f"/month?{params}" + (f"#d{tid}" if tid else ""))


def _set_date(
    user_id: int,
    scope: str,
    account: str,
    date: str,
    description: str,
    amount: float,
    asset: str,
    effective: str,
) -> str:
    """One row's effective date, recorded in the vault — or dropped, for
    "reset". Returns the error to show, or "" when the page should redirect."""
    wanted = "" if scope == "clear" else effective.strip()
    if scope != "clear" and not wanted:
        return "Pick a date first."
    try:
        with vaults.open_vault(user_id) as vault:
            vault.set_effective_date(
                account, date, description, amount, wanted or None, assets=asset
            )
    except vaults.VaultMissing:
        return ""
    except vaults.CORE_ERRORS as exc:
        return vaults.explain(exc)
    return ""


# --- Find Transactions --------------------------------------------------------
#
# A search over the whole ledger. Unlike a month, the answer can be every
# transaction there is, which is past the ~1-2k rows a page can carry whole
# (see Tables in CLAUDE.md) — so this table is paged on the server, and because
# a sort has to see every row to be right, it's sorted on the server too, with
# the column and direction in the URL. Core does the filtering: it is asked
# for exactly the rows that match. Sorting and slicing those is ordering and
# selection, not arithmetic.

FIND_PAGE_SIZE = 100

# Sortable column -> the key a row sorts by. Text sorts ignoring case, the way
# the client-side sort does. A key of None (an unpriced market value) sorts
# after everything, whichever way the column is going.
SORT_KEYS = {
    "date": lambda row: row["date"],
    "account": lambda row: row["account"].lower(),
    "description": lambda row: row["description"].lower(),
    "amount": lambda row: row["amount"],
    "asset": lambda row: row["asset"].lower(),
    "mv": lambda row: row["market_value"],
    "category": lambda row: row["category"].lower(),
}


@dataclass(frozen=True)
class Search:
    """What the Find form asked for — everything that decides which rows match.
    Empty means "don't narrow by this", the same as an untouched control."""

    accts: tuple[str, ...] = ()
    cats: tuple[str, ...] = ()
    assets: tuple[str, ...] = ()
    desc: str = ""
    start: str = ""
    end: str = ""
    share: bool = False

    def pairs(self) -> list[tuple[str, str]]:
        """As query-string pairs, `search=1` first: that's what says the form
        was submitted at all, so a search for nothing still lists everything."""
        pairs = [("search", "1")]
        pairs += [("acct", a) for a in self.accts]
        pairs += [("cat", c) for c in self.cats]
        pairs += [("asset", a) for a in self.assets]
        for name in ("desc", "start", "end"):
            if value := getattr(self, name):
                pairs.append((name, value))
        if self.share:
            pairs.append(("share", "1"))
        return pairs

    def core_filters(self) -> dict:
        """As `list_transactions` arguments. An empty pick is left out rather
        than passed, because core reads an empty list as "match nothing"."""
        return {
            "accounts": list(self.accts) or None,
            "categories": list(self.cats) or None,
            "assets": list(self.assets) or None,
            "descriptions": [self.desc] if self.desc else None,
            "start_date": self.start or None,
            "end_date": self.end or None,
            "apply_ownership_share": self.share,
        }


def _find_qs(search: Search, sort: str = "", desc: bool = False, page: int = 1) -> str:
    """The Find page as it's being looked at — the search, the sort, the page —
    as a query string. Leaves out whatever is at its default."""
    pairs = search.pairs()
    if sort:
        pairs.append(("sort", sort))
        if desc:
            pairs.append(("dir", "desc"))
    if page > 1:
        pairs.append(("page", str(page)))
    return urlencode(pairs)


def _sorted(rows: list[dict], sort: str, desc: bool) -> list[dict]:
    """The rows in the order a column header asked for. Ties keep the order
    they came in, which is `_ordered()`'s — date, then size."""
    key = SORT_KEYS.get(sort)
    if key is None:
        return rows
    present = [row for row in rows if key(row) is not None]
    missing = [row for row in rows if key(row) is None]
    return sorted(present, key=key, reverse=desc) + missing


def _headers(search: Search, sort: str, desc: bool) -> list[dict]:
    """Each column header's link: sort by it, or flip it if it's the current
    one. A fresh sort goes back to page 1. With no sort asked for, the date
    reads as ascending, because that's the order the rows are in."""
    current = sort if sort in SORT_KEYS else "date"
    out = []
    for col, label, css in (
        ("date", "date", "c-date"),
        ("account", "account", ""),
        ("description", "description", ""),
        ("amount", "amount", "num"),
        ("asset", "asset", ""),
        ("mv", "market value", "num"),
        ("category", "category", ""),
    ):
        on = col == current
        flip = on and not desc
        out.append(
            {
                "label": label,
                "css": css,
                "aria": ("descending" if desc else "ascending") if on else "none",
                "href": "/find?" + _find_qs(search, col, flip),
            }
        )
    return out


def _find_page(
    request: Request,
    user,
    nav,
    search: Search | None,
    *,
    sort: str = "",
    desc: bool = False,
    page_no: int = 1,
    edit: int | None = None,
    redate: int | None = None,
    error: str = "",
    status_code: int = 200,
):
    """Find Transactions, rendered from scratch. `search` is None until the form
    has been submitted — the page opens on the form alone, not on the whole
    ledger. The GET calls this; so does a POST that has an error to show."""
    search_error = ""
    rows: list[dict] = []
    try:
        with vaults.open_vault(user.id) as vault:
            accounts = [str(n) for n in vault.list_accounts()["account_name"]]
            categories = [str(n) for n in vault.list_categories()["category_name"]]
            assets = [str(n) for n in vault.list_assets()["asset_name"]]
            base_asset = vaults.base_asset(vault)
            if search is not None:
                try:
                    rows = _ordered(_rows(vault.list_transactions(**search.core_filters())))
                except vaults.CORE_ERRORS as exc:
                    # A misspelled name in a hand-edited URL, a deleted account
                    # in a bookmark, a date pandas can't read: core names it.
                    search_error = vaults.explain(exc)
    except vaults.VaultMissing:
        return page(request, "find.html", user, nav, no_vault=True, filters=[], search=None)

    picks = search or Search()
    rows = _sorted(rows, sort, desc)
    n_pages = max(1, math.ceil(len(rows) / FIND_PAGE_SIZE))
    page_no = min(max(page_no, 1), n_pages)
    first = (page_no - 1) * FIND_PAGE_SIZE
    shown = rows[first : first + FIND_PAGE_SIZE]
    here = _find_qs(picks, sort, desc, page_no)
    return page(
        request,
        "find.html",
        user,
        nav,
        search=search,
        picks=picks,
        # Every name the vault has, not only those in the results: this is
        # where a search starts, and "unclassified" is a category core accepts.
        # A pick the vault no longer has stays on offer so it can be unticked.
        filters=[
            {
                "name": name,
                "label": label,
                "options": sorted(set(options) | set(picked), key=str.lower),
                "picked": set(picked),
            }
            for name, label, options, picked in (
                ("acct", "account", accounts, picks.accts),
                ("cat", "category", [UNCLASSIFIED, *categories], picks.cats),
                ("asset", "asset", assets, picks.assets),
            )
        ],
        headers=_headers(picks, sort, desc),
        rows=shown,
        n_total=len(rows),
        first=first,
        page_no=page_no,
        n_pages=n_pages,
        prev_url=f"/find?{_find_qs(picks, sort, desc, page_no - 1)}" if page_no > 1 else "",
        next_url=f"/find?{_find_qs(picks, sort, desc, page_no + 1)}" if page_no < n_pages else "",
        # Every matching row, not the page's: a total of one page out of
        # several answers no question anyone asked. With more than one page the
        # label says so (user, 2026-11-14).
        total=_total(rows),
        here=here,
        share=picks.share,
        base_asset=base_asset,
        categories=categories,
        edit=edit,
        redate=redate,
        error=error or search_error,
        status_code=400 if search_error else status_code,
    )


# The search, as query parameters. Shared by the GET and by the two row POSTs,
# which carry it on their action URL the way Month View's carry its filter.
SearchAcct = Annotated[list[str], Query(alias="acct", default_factory=list)]
SearchCat = Annotated[list[str], Query(alias="cat", default_factory=list)]
SearchAsset = Annotated[list[str], Query(alias="asset", default_factory=list)]


def _search(
    searched: bool,
    acct: list[str],
    cat: list[str],
    asset: list[str],
    desc: str,
    start: str,
    end: str,
    share: bool,
) -> Search | None:
    if not searched:
        return None
    return Search(
        tuple(acct), tuple(cat), tuple(asset), desc.strip(), start.strip(), end.strip(), share
    )


@router.get("/find")
def find_transactions(
    request: Request,
    user: CurrentUser,
    nav: Nav,
    acct: SearchAcct,
    cat: SearchCat,
    asset: SearchAsset,
    search: bool = False,
    desc: str = "",
    start: str = "",
    end: str = "",
    share: bool = False,
    sort: str = "",
    dir: str = "",
    page: int = 1,
    edit: int | None = None,
    redate: int | None = None,
):
    return _find_page(
        request,
        user,
        nav,
        _search(search, acct, cat, asset, desc, start, end, share),
        sort=sort if sort in SORT_KEYS else "",
        desc=dir == "desc",
        page_no=page,
        edit=edit,
        redate=redate,
    )


@router.post("/find/categorize")
def find_categorize(
    request: Request,
    user: CurrentUser,
    nav: Nav,
    scope: Annotated[str, Form()],
    account: Annotated[str, Form()],
    date: Annotated[str, Form()],
    description: Annotated[str, Form()],
    amount: Annotated[float, Form()],
    asset_name: Annotated[str, Form(alias="asset")],
    acct: SearchAcct,
    cat: SearchCat,
    asset: SearchAsset,
    search: bool = False,
    desc: str = "",
    start: str = "",
    end: str = "",
    share: bool = False,
    sort: str = "",
    dir: str = "",
    page: int = 1,
    category: Annotated[str, Form()] = "",
    tid: Annotated[int, Form()] = 0,
):
    found = _search(search, acct, cat, asset, desc, start, end, share)
    sort = sort if sort in SORT_KEYS else ""
    error = _categorize(user.id, scope, account, date, description, amount, asset_name, category)
    if error:
        return _find_page(
            request,
            user,
            nav,
            found,
            sort=sort,
            desc=dir == "desc",
            page_no=page,
            edit=tid,
            error=error,
            status_code=400,
        )
    qs = _find_qs(found or Search(), sort, dir == "desc", page)
    return _redirect(f"/find?{qs}" + (f"#t{tid}" if tid else ""))


@router.post("/find/set-date")
def find_set_date(
    request: Request,
    user: CurrentUser,
    nav: Nav,
    scope: Annotated[str, Form()],
    account: Annotated[str, Form()],
    date: Annotated[str, Form()],
    description: Annotated[str, Form()],
    amount: Annotated[float, Form()],
    asset_name: Annotated[str, Form(alias="asset")],
    acct: SearchAcct,
    cat: SearchCat,
    asset: SearchAsset,
    search: bool = False,
    desc: str = "",
    start: str = "",
    end: str = "",
    share: bool = False,
    sort: str = "",
    dir: str = "",
    page: int = 1,
    effective: Annotated[str, Form()] = "",
    tid: Annotated[int, Form()] = 0,
):
    found = _search(search, acct, cat, asset, desc, start, end, share)
    sort = sort if sort in SORT_KEYS else ""
    error = _set_date(user.id, scope, account, date, description, amount, asset_name, effective)
    if error:
        return _find_page(
            request,
            user,
            nav,
            found,
            sort=sort,
            desc=dir == "desc",
            page_no=page,
            redate=tid,
            error=error,
            status_code=400,
        )
    qs = _find_qs(found or Search(), sort, dir == "desc", page)
    return _redirect(f"/find?{qs}" + (f"#d{tid}" if tid else ""))


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
