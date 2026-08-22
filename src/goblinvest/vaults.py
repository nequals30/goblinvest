"""Opening each user's vault — paths and lifecycle, nothing else.

This is the only module in this project that imports `goblinvest_core`. It owns
*where* a vault lives and *when* it is open; every question about what's inside
one is core's.

Vaults are opened **inside the request handler**, not in a FastAPI dependency.
Core connects with stdlib defaults (`check_same_thread=True`), and FastAPI runs
a sync `yield` dependency and the sync endpoint on different threadpool threads
once requests overlap — so a vault opened in a dependency would raise
`ProgrammingError` under concurrency. A `with open_vault(uid) as v:` block
inside the handler keeps the open, the queries, and the close on one thread.

Vaults created here are always unencrypted: core can only take a password from
a `getpass` prompt, and a request handler has no terminal (see request #1 in
CLAUDE.md).
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any

from goblinvest import storage


class VaultMissing(Exception):
    """No vault file for this user yet."""


# What core raises when it doesn't like an argument: an unregistered name, the
# base currency, an undefined or reserved category, a transaction that matches
# nothing, a missing adjustments folder. Routes show these rather than 500.
CORE_ERRORS = (ValueError, FileNotFoundError)


def explain(exc: Exception) -> str:
    """A core exception as a line to put in front of the user. Core's own
    ValueError messages already name what was wrong, so they pass through."""
    if isinstance(exc, FileNotFoundError):
        return "This vault's adjustments folder is missing, so category changes can't be saved."
    return str(exc)


def vault_exists(user_id: int) -> bool:
    return storage.vault_path(user_id).is_file()


def create_vault(user_id: int) -> Path:
    """Create a user's vault and its adjustments folder. Called at signup.

    No `adjustments_dir=` is passed on purpose: core defaults to
    `<stem>_adjustments` beside the vault file, which is exactly
    `storage.adjustments_dir(user_id)`.
    """
    from goblinvest_core import Vault

    path = storage.vault_path(user_id)
    Vault.create(path, encrypted=False).close()
    return path


@contextmanager
def open_vault(user_id: int) -> Iterator[Any]:
    """Open a user's vault for the duration of one request handler."""
    from goblinvest_core import Vault

    path = storage.vault_path(user_id)
    if not path.is_file():
        raise VaultMissing(str(path))

    vault = Vault.open(path)
    try:
        yield vault
    finally:
        vault.close()


def define_category(vault: Any, name: str) -> None:
    """Define a category and make it visible to the pages that list them.

    `add_category` writes the adjustments file; the vault's own `categories`
    table — what `list_categories()` reads — is written by `apply_categories`.
    Without the second call a new category isn't on the page it was just added
    from. `delete_category` re-applies on its own, so it needs no counterpart.
    """
    vault.add_category(name)
    vault.apply_categories()


def _as_date(value: Any) -> date | None:
    """A pandas Timestamp (or NaT) as a plain `date`, so the rest of this
    project never has to think about pandas' missing-value types."""
    import pandas as pd

    if value is None or pd.isna(value):
        return None
    return value.date() if hasattr(value, "date") else value


def summary(vault: Any) -> dict[str, Any]:
    """`summarize_vault()` with its two dates converted to plain `date`s."""
    out = dict(vault.summarize_vault())
    out["first_transaction"] = _as_date(out.get("first_transaction"))
    out["last_transaction"] = _as_date(out.get("last_transaction"))
    return out
