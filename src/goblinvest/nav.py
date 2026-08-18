"""The left pane: what's in it, in what order, and what's hidden.

Pure UI organization — no finance logic lives here. Every user gets the same
built-in items seeded into their `nav_items` rows, then reorders, hides, and
adds dashboards of their own on top. Seeding is lazy (`ensure_builtins`), so a
user who existed before an item was invented picks it up on the next page load
rather than needing a migration.

A user's dashboards are just nav items of kind `dashboard`; their page lives at
`/dashboards/<slug>` and the slug is unique per user, not globally.
"""

import re
import sqlite3
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends

from goblinvest import db
from goblinvest.auth import Conn, CurrentUser

BUILTIN = "builtin"
DASHBOARD = "dashboard"

# slug, label, url — order here is the order a new user first sees.
BUILTINS: tuple[tuple[str, str, str], ...] = (
    ("csv-import", "CSV Import", "/import"),
    ("month-view", "Month View", "/month"),
    ("main-dashboard", "Main Dashboard", "/dashboard"),
)

_BUILTIN_URLS = {slug: url for slug, _, url in BUILTINS}

MAX_LABEL = 40


@dataclass(frozen=True)
class NavItem:
    id: int
    slug: str
    label: str
    kind: str
    position: int
    hidden: bool

    @property
    def url(self) -> str:
        if self.kind == BUILTIN:
            return _BUILTIN_URLS.get(self.slug, "/")
        return f"/dashboards/{self.slug}"

    @property
    def deletable(self) -> bool:
        return self.kind == DASHBOARD


def _item(row: sqlite3.Row) -> NavItem:
    return NavItem(
        id=row["id"],
        slug=row["slug"],
        label=row["label"],
        kind=row["kind"],
        position=row["position"],
        hidden=bool(row["hidden"]),
    )


def ensure_builtins(conn: sqlite3.Connection, user_id: int) -> None:
    """Append any built-in item this user doesn't have yet. Idempotent."""
    have = {row["slug"] for row in db.list_nav_rows(conn, user_id) if row["kind"] == BUILTIN}
    position = db.next_nav_position(conn, user_id)
    for slug, label, _url in BUILTINS:
        if slug in have:
            continue
        db.insert_nav_item(conn, user_id, slug, label, BUILTIN, position)
        position += 1


def all_items(conn: sqlite3.Connection, user_id: int) -> list[NavItem]:
    """Every nav item in order, hidden ones included — for the settings page."""
    ensure_builtins(conn, user_id)
    return [_item(row) for row in db.list_nav_rows(conn, user_id)]


def visible_items(conn: sqlite3.Connection, user_id: int) -> list[NavItem]:
    """What the left pane actually renders."""
    return [item for item in all_items(conn, user_id) if not item.hidden]


def find(conn: sqlite3.Connection, user_id: int, item_id: int) -> NavItem | None:
    row = db.find_nav_row(conn, user_id, item_id)
    return _item(row) if row is not None else None


def find_dashboard(conn: sqlite3.Connection, user_id: int, slug: str) -> NavItem | None:
    row = db.find_nav_row_by_slug(conn, user_id, slug)
    if row is None or row["kind"] != DASHBOARD:
        return None
    return _item(row)


# --- mutations --------------------------------------------------------------


def slugify(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
    return slug or "dashboard"


def _unique_slug(conn: sqlite3.Connection, user_id: int, base: str) -> str:
    slug, n = base, 2
    while db.find_nav_row_by_slug(conn, user_id, slug) is not None:
        slug = f"{base}-{n}"
        n += 1
    return slug


def create_dashboard(conn: sqlite3.Connection, user_id: int, label: str) -> NavItem:
    label = label.strip()[:MAX_LABEL]
    slug = _unique_slug(conn, user_id, slugify(label))
    position = db.next_nav_position(conn, user_id)
    item_id = db.insert_nav_item(conn, user_id, slug, label, DASHBOARD, position)
    return NavItem(item_id, slug, label, DASHBOARD, position, hidden=False)


def move(conn: sqlite3.Connection, user_id: int, item_id: int, delta: int) -> None:
    """Swap an item with its neighbour. A no-op at either end.

    Positions are rewritten densely first, so items seeded at equal positions
    (or left gappy by a delete) still move one predictable step at a time.
    """
    items = all_items(conn, user_id)
    index = next((i for i, item in enumerate(items) if item.id == item_id), None)
    if index is None:
        return
    target = index + delta
    if not 0 <= target < len(items):
        return
    items[index], items[target] = items[target], items[index]
    for position, item in enumerate(items):
        db.set_nav_position(conn, user_id, item.id, position)


def set_hidden(conn: sqlite3.Connection, user_id: int, item_id: int, hidden: bool) -> None:
    db.set_nav_hidden(conn, user_id, item_id, hidden)


def delete(conn: sqlite3.Connection, user_id: int, item_id: int) -> bool:
    """Remove a user-made dashboard. Built-ins can only be hidden, not deleted."""
    item = find(conn, user_id, item_id)
    if item is None or not item.deletable:
        return False
    db.delete_nav_item(conn, user_id, item_id)
    return True


# --- dependency -------------------------------------------------------------


def current_nav(user: CurrentUser, conn: Conn) -> list[NavItem]:
    return visible_items(conn, user.id)


Nav = Annotated[list[NavItem], Depends(current_nav)]
