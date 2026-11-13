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

A vault may be **encrypted** (SQLCipher for the file, GVENC armor for the
adjustments CSVs), in which case opening it needs a password. Core takes that
password from a terminal prompt, which a request handler doesn't have; this
module gets it from `goblinvest.keyring` instead, which holds one per user in
memory as a core `password_scope`. Here it is one `keyring.borrow()` around
the open, a `VaultLocked` when there's nothing to borrow, and dropping a
password core has found to be wrong.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any

from goblinvest import keyring, storage
from goblinvest.keyring import VaultLocked

# The first 16 bytes of every plain SQLite file; core sniffs for exactly this
# to decide whether a vault is encrypted, and so do we — the question "will
# this open need a password?" has to be answered *before* the open.
_SQLITE_MAGIC = b"SQLite format 3\x00"

# The first bytes of a GVENC-armored file, core's format for an encrypted CSV.
# An adjustments folder can be armored while the vault itself is plain, and
# then reading a category file is what needs the password.
_ARMOR_MAGIC = b"GVENC"


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
        return "This vault's adjustments folder is missing, so changes to it can't be saved."
    return str(exc)


def vault_exists(user_id: int) -> bool:
    return storage.vault_path(user_id).is_file()


# --- encryption -------------------------------------------------------------


def _starts_with(path: Path, magic: bytes) -> bool:
    """Whether a file begins with these bytes. Reads the header and no more —
    never the contents."""
    try:
        with path.open("rb") as f:
            return f.read(len(magic)) == magic
    except OSError:
        return False


def is_encrypted(user_id: int) -> bool:
    """Whether this user's vault file is SQLCipher-encrypted."""
    path = storage.vault_path(user_id)
    return path.is_file() and not _starts_with(path, _SQLITE_MAGIC)


def _adjustments_armored(user_id: int) -> bool:
    """Whether any of this user's adjustments CSVs is encrypted.

    Checked separately from the vault because the two can differ: core armors
    the files a vault creates, but a folder can also be encrypted on its own
    with `encrypt_file`, and then reading a category rule needs the password
    even though the vault opens without one.
    """
    folder = storage.adjustments_dir(user_id)
    if not folder.is_dir():
        return False
    return any(_starts_with(path, _ARMOR_MAGIC) for path in folder.glob("*.csv"))


def needs_password(user_id: int) -> bool:
    """Whether anything this user's vault touches is encrypted.

    This is the question `open_vault` asks before it opens anything, and the
    one that decides whether a page redirects to `/unlock`.
    """
    return is_encrypted(user_id) or _adjustments_armored(user_id)


def forbid_password_prompts() -> None:
    """Make core raise instead of prompting for a password at the terminal.

    Core reaches for `getpass` whenever it needs a password it hasn't been
    given. In a server that terminal doesn't exist, and the failure mode is a
    request hung on a read that will never return — so replace the prompt with
    a `VaultLocked`, which the app already knows how to turn into a page asking
    for the password. Called once at boot; it is the backstop for a code path
    that reaches core without going through `keyring.borrow()`. Inside a
    password scope core never prompts, so this only ever fires outside one.

    This is the one place left that touches a private name in core.
    """
    from goblinvest_core import _password

    def _no_terminal(prompt: str = "") -> str:
        raise VaultLocked("goblinvest_core asked for a password at the terminal")

    _password.getpass = _no_terminal


@contextmanager
def _password_held(user_id: int) -> Iterator[None]:
    """This user's password in force for the block, when the vault needs one.

    Core reports a wrong password as a `ValueError` and then refuses every later
    use of that password the same way, so a password that has stopped working
    must be dropped or every page fails identically until the idle timeout. But
    `ValueError` is also how core says "that category is reserved", and those
    are shown on the page with the vault still unlocked. So a `ValueError` here
    is followed by `_check_password()`: if the password still opens what is
    encrypted, the error was about something else and the password stays.
    The check is ~0.5ms with the keys core already holds, and only runs when
    something has already gone wrong.
    """
    if not needs_password(user_id):
        yield
        return
    try:
        with keyring.borrow(user_id):
            yield
    except ValueError:
        try:
            _check_password(user_id)
        except (*CORE_ERRORS, VaultLocked):
            keyring.forget(user_id)
        raise


def _check_password(user_id: int) -> None:
    """Use the held password on whatever is encrypted, and raise if it doesn't
    open it: the vault file, or — for a plain vault whose adjustments folder is
    armored — one adjustments file. Plaintext read here is dropped at once;
    this asks a yes/no question, not for the contents."""
    from goblinvest_core import Vault, read_encrypted_file

    with keyring.borrow(user_id):
        if is_encrypted(user_id):
            Vault.open(storage.vault_path(user_id)).close()
            return
        folder = storage.adjustments_dir(user_id)
        armored = next(p for p in sorted(folder.glob("*.csv")) if _starts_with(p, _ARMOR_MAGIC))
        read_encrypted_file(armored)


def unlock(user_id: int, password: str) -> bool:
    """Try a password against this user's vault; remember it if it works.

    Verification is the real thing — opening the SQLCipher file, or decrypting
    an armored adjustments file when only those are encrypted. A password that
    doesn't work is dropped again, so a failed attempt never leaves a wrong one
    in memory for the next page to fail on.
    """
    if not vault_exists(user_id):
        return False
    if not needs_password(user_id):
        return True  # nothing is encrypted; there is nothing to unlock

    keyring.remember(user_id, password)
    try:
        _check_password(user_id)
    except (*CORE_ERRORS, VaultLocked):
        keyring.forget(user_id)
        return False
    return True


# --- lifecycle --------------------------------------------------------------


def create_vault(user_id: int, password: str | None = None) -> Path:
    """Create a user's vault and its adjustments folder. Called at signup.

    With a `password` the vault file is SQLCipher-encrypted and core armors the
    adjustments CSVs to match — core takes one decision for both — and the
    password is remembered, so the user who just signed up is already unlocked.
    With `None` everything is written in the clear, which is what a demo or a
    vault meant to be readable by other SQLite tools wants.

    No `adjustments_dir=` is passed on purpose: core defaults to
    `<stem>_adjustments` beside the vault file, which is exactly
    `storage.adjustments_dir(user_id)`.
    """
    from goblinvest_core import Vault

    path = storage.vault_path(user_id)
    if password is None:
        Vault.create(path, encrypted=False).close()
        return path

    keyring.remember(user_id, password)
    try:
        with keyring.borrow(user_id):
            Vault.create(path, encrypted=True).close()
    except BaseException:
        keyring.forget(user_id)
        raise
    return path


@contextmanager
def open_vault(user_id: int) -> Iterator[Any]:
    """Open a user's vault for the duration of one request handler.

    Raises `VaultMissing` if there is no vault, and `VaultLocked` if there is
    an encrypted one and no password in memory for it.
    """
    from goblinvest_core import Vault

    path = storage.vault_path(user_id)
    if not path.is_file():
        raise VaultMissing(str(path))

    with _password_held(user_id):
        vault = Vault.open(path)
        try:
            yield vault
        finally:
            vault.close()


def apply_categories(vault: Any) -> int:
    """Recompute every transaction's category from the adjustments files.

    Returns how many exception rows matched no transaction. Core hands those
    back as a DataFrame; counting them here is what keeps pandas types from
    leaving this module.
    """
    return len(vault.apply_categories())


def apply_adjustments(vault: Any) -> tuple[int, int]:
    """Re-run every adjustments file over the vault: dates, then categories.

    The files are the user's own and can be edited outside this app; this is
    what makes the vault match them again. Both core calls hand back the rows
    that named no transaction — a typo, or a description the bank reworded —
    and both counts come back so the page can say so. Dates go first because
    that is the order a rebuild does it in, not because either depends on the
    other: categories are keyed on the date the statement gave a transaction,
    which moving it does not change.
    """
    undated = len(vault.apply_effective_dates())
    uncategorized = len(vault.apply_categories())
    return uncategorized, undated


def define_category(vault: Any, name: str) -> None:
    """Define a category and make it visible to the pages that list them.

    `add_category` writes the adjustments file; the vault's own `categories`
    table — what `list_categories()` reads — is written by `apply_categories`.
    Without the second call a new category isn't on the page it was just added
    from. `delete_category` re-applies on its own, so it needs no counterpart.
    """
    vault.add_category(name)
    apply_categories(vault)


def base_asset(vault: Any) -> str:
    """The vault's base currency, e.g. `"USD"`.

    Asset 1 by core's definition — the currency every market value is expressed
    in. Pages use it to leave the asset column blank on the rows that are simply
    in it, so only the exceptions (VFIAX, EUR) are worth reading.
    """
    assets = vault.list_assets()
    named = assets.loc[assets["asset_id"] == 1, "asset_name"]
    return str(named.iloc[0]) if len(named) else ""


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
