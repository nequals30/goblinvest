"""Settings for what's registered in the vault: accounts, assets, categories.

Three near-identical pages — a table, an add form, and a delete that goes
through a confirmation page first. One set of handlers serves all three, with
the per-kind differences (columns, how a row is built, which core call runs)
gathered in `KINDS` and the small dispatch tables under it.

Deleting is two requests on purpose. Core's `delete_*` methods ask for a `y` at
the terminal, and a request handler has no terminal, so they're called with
`confirm=False` — which means the confirmation has to happen here instead. The
GET renders the warning; only the POST that follows it touches the vault. No
`window.confirm()`: with JavaScript off, an interstitial page is the only
confirmation there is.

No finance logic: every row comes from a core `list_*` call and every change is
a core `add_*` / `delete_*` call.
"""

from dataclasses import dataclass
from typing import Annotated, Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import RedirectResponse

from goblinvest import vaults
from goblinvest.auth import CurrentUser
from goblinvest.nav import Nav
from goblinvest.templates import page

router = APIRouter(prefix="/settings/vault")

Slug = Literal["accounts", "assets", "categories"]

LIST_TEMPLATE = "settings_vault.html"
DELETE_TEMPLATE = "settings_vault_delete.html"


@dataclass(frozen=True)
class Column:
    label: str
    type: str = "text"


@dataclass(frozen=True)
class Kind:
    slug: str
    title: str
    noun: str
    blurb: str  # the line under it on the settings menu
    empty: str  # what the table says when there's nothing in it
    columns: tuple[Column, ...]
    consequences: tuple[str, ...]  # the warning, straight from what core does

    @property
    def url(self) -> str:
        return f"/settings/vault/{self.slug}"


KINDS: dict[str, Kind] = {
    "accounts": Kind(
        slug="accounts",
        title="Accounts",
        noun="account",
        blurb="The accounts your transactions belong to.",
        empty="No accounts yet.",
        columns=(Column("account"), Column("group"), Column("share", "number")),
        consequences=(
            "Every transaction in this account is deleted with it.",
            (
                "Category rules and exceptions are left alone — they still apply "
                "to these transactions if a statement brings them back."
            ),
            "raw_data is the source of truth: re-importing the account's statements rebuilds it.",
        ),
    ),
    "assets": Kind(
        slug="assets",
        title="Assets",
        noun="asset",
        blurb="Currencies, tickers, anything you hold an amount of.",
        empty="No assets yet.",
        columns=(Column("asset"), Column("note")),
        consequences=(
            "Every transaction denominated in this asset is deleted with it.",
            "Its stored market prices are deleted too, and would have to be fetched again.",
            "Category rules and exceptions are left alone.",
        ),
    ),
    "categories": Kind(
        slug="categories",
        title="Categories",
        noun="category",
        blurb="The categories rules and exceptions can hand out.",
        empty="No categories yet.",
        columns=(Column("category"), Column("transactions", "number")),
        consequences=(
            "No transactions are deleted — the ones in this category become unclassified.",
            (
                "Every category rule and exception that hands out this category "
                "is removed from your adjustments files."
            ),
            (
                "Those files are yours and are edited in place, so rebuilding the "
                "vault will not bring the category back."
            ),
        ),
    ),
}


def _redirect(to: str) -> RedirectResponse:
    return RedirectResponse(to, status_code=status.HTTP_303_SEE_OTHER)


def _cell(text: Any, *, sort: Any = None, num: bool = False) -> dict:
    """One table cell: what's displayed, and the machine-readable key the
    sorting script uses instead of the displayed text."""
    return {"text": text, "sort": text if sort is None else sort, "num": num}


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


# --- one row builder per kind ------------------------------------------------
#
# Each returns rows of {name, cells, deletable, note}. `name` is core's own
# spelling, which is what the delete calls are given back. `note` is the extra
# line the confirmation page shows above the warning.


def _account_rows(vault: Any) -> list[dict]:
    rows = []
    for row in vault.list_accounts().itertuples():
        share = float(row.ownership_share)
        rows.append(
            {
                "name": row.account_name,
                "cells": [
                    _cell(row.account_name),
                    _cell(row.account_group_name),
                    _cell(f"{share:g}", sort=share, num=True),
                ],
                "deletable": True,
                "note": "",
            }
        )
    return rows


def _asset_rows(vault: Any) -> list[dict]:
    rows = []
    for row in vault.list_assets().itertuples():
        base = row.asset_id == 1
        rows.append(
            {
                "name": row.asset_name,
                "cells": [
                    _cell(row.asset_name),
                    _cell("base currency" if base else ""),
                ],
                # Core refuses to delete asset 1: everything else is valued in it.
                "deletable": not base,
                "note": "",
            }
        )
    return rows


def _category_rows(vault: Any) -> list[dict]:
    rows = []
    for row in vault.list_categories().itertuples():
        count = int(row.n_transactions)
        rows.append(
            {
                "name": row.category_name,
                "cells": [
                    _cell(row.category_name),
                    _cell(count, num=True),
                ],
                "deletable": True,
                "note": f"{_plural(count, 'transaction')} in it right now.",
            }
        )
    return rows


ROWS = {"accounts": _account_rows, "assets": _asset_rows, "categories": _category_rows}


def _add(vault: Any, slug: str, name: str, group: str, share: float) -> None:
    if slug == "accounts":
        extra = {"account_group_name": group} if group else {}
        vault.add_account(name, ownership_share=share, **extra)
    elif slug == "assets":
        vault.add_asset(name)
    else:
        vault.add_category(name)
        # add_category defines it in the adjustments file; the vault's own
        # categories table is written by apply_categories, which is what
        # list_categories reads. Without this the new category isn't on the page
        # it just came from. delete_category re-applies on its own.
        vault.apply_categories()


def _delete(vault: Any, slug: str, name: str) -> None:
    if slug == "accounts":
        vault.delete_account(name, confirm=False)
    elif slug == "assets":
        vault.delete_asset(name, confirm=False)
    else:
        vault.delete_category(name, confirm=False)


# --- shared rendering --------------------------------------------------------

# What core raises when it doesn't like an argument: an unregistered name, the
# base currency, an undefined or reserved category, a missing adjustments folder.
CORE_ERRORS = (ValueError, FileNotFoundError)


def _problem(exc: Exception) -> str:
    if isinstance(exc, FileNotFoundError):
        return "The adjustments folder for this vault is missing, so categories can't change."
    return str(exc)


def _list_page(
    request: Request,
    user: Any,
    nav: Any,
    kind: Kind,
    *,
    error: str = "",
    deleted: str = "",
    status_code: int = 200,
):
    try:
        with vaults.open_vault(user.id) as vault:
            rows = ROWS[kind.slug](vault)
    except vaults.VaultMissing:
        return page(request, LIST_TEMPLATE, user, nav, kind=kind, rows=[], no_vault=True)
    return page(
        request,
        LIST_TEMPLATE,
        user,
        nav,
        kind=kind,
        rows=rows,
        error=error,
        deleted=deleted,
        status_code=status_code,
    )


def _find(rows: list[dict], name: str) -> dict | None:
    """The row a submitted name refers to, matched case-insensitively the way
    core matches names."""
    wanted = name.strip().lower()
    return next((row for row in rows if row["name"].lower() == wanted), None)


# --- routes ------------------------------------------------------------------


@router.get("/{slug}")
def index(request: Request, user: CurrentUser, nav: Nav, slug: Slug, deleted: str = ""):
    return _list_page(request, user, nav, KINDS[slug], deleted=deleted)


@router.post("/{slug}/add")
def add_item(
    request: Request,
    user: CurrentUser,
    nav: Nav,
    slug: Slug,
    name: Annotated[str, Form()],
    group: Annotated[str, Form()] = "",
    share: Annotated[str, Form()] = "1",
):
    kind = KINDS[slug]
    name = name.strip()
    if not name:
        return _list_page(
            request,
            user,
            nav,
            kind,
            error=f"Give the {kind.noun} a name.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    try:
        # Only accounts send this, and the range is the number input's business.
        share_value = float(share)
    except ValueError:
        return _list_page(
            request,
            user,
            nav,
            kind,
            error="Ownership share must be a number, e.g. 1 or 0.5.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    try:
        with vaults.open_vault(user.id) as vault:
            _add(vault, slug, name, group.strip(), share_value)
    except vaults.VaultMissing:
        return _redirect(kind.url)
    except CORE_ERRORS as exc:
        return _list_page(
            request, user, nav, kind, error=_problem(exc), status_code=status.HTTP_400_BAD_REQUEST
        )
    return _redirect(kind.url)


@router.get("/{slug}/delete")
def confirm_delete(request: Request, user: CurrentUser, nav: Nav, slug: Slug, name: str = ""):
    """The warning. Nothing here changes anything — the POST below does."""
    kind = KINDS[slug]
    try:
        with vaults.open_vault(user.id) as vault:
            row = _find(ROWS[slug](vault), name)
    except vaults.VaultMissing:
        return _redirect(kind.url)
    if row is None or not row["deletable"]:
        return _redirect(kind.url)
    # `item_name`, not `name`: page()'s second positional argument is the template's.
    return page(
        request, DELETE_TEMPLATE, user, nav, kind=kind, item_name=row["name"], note=row["note"]
    )


@router.post("/{slug}/delete")
def delete_item(
    request: Request,
    user: CurrentUser,
    nav: Nav,
    slug: Slug,
    name: Annotated[str, Form()],
):
    kind = KINDS[slug]
    try:
        with vaults.open_vault(user.id) as vault:
            _delete(vault, slug, name)
    except vaults.VaultMissing:
        return _redirect(kind.url)
    except CORE_ERRORS as exc:
        return _list_page(
            request, user, nav, kind, error=_problem(exc), status_code=status.HTTP_400_BAD_REQUEST
        )
    return _redirect(f"{kind.url}?deleted={quote(name.strip())}")
