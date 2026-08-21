"""SQLite-backed vault of accounts, assets, transactions, and prices."""

import datetime
import os
import sqlite3
from collections.abc import Sequence
from pathlib import Path

import pandas as pd

from goblinvest_core._password import _get_password, forget_password
from goblinvest_core.adjustments import (
    _CATEGORIES_FILE,
    _EXCEPTIONS_FILE,
    _RULES_FILE,
    _ensure_files,
    _folder_is_encrypted,
    _write_file,
)
from goblinvest_core.categories import (
    UNCLASSIFIED,
    _canonical_categories,
    _normalize_desc,
    _read_category_file,
    _resolve_categories,
    _strip_dup_suffix,
)

# The first 16 bytes of every unencrypted SQLite file; anything else is
# assumed to be a SQLCipher-encrypted vault.
_SQLITE_MAGIC = b"SQLite format 3\x00"

_SETTINGS_TABLE = """
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT NOT NULL PRIMARY KEY,
        value TEXT
    );"""

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS transactions (
        trans_id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
        account_id INTEGER NOT NULL,
        trans_date DATE NOT NULL,
        trans_desc TEXT NOT NULL,
        amount DECIMAL(7,5) NOT NULL,
        asset_id INTEGER NOT NULL,
        category_id INTEGER,
        UNIQUE(account_id, trans_date, trans_desc, amount, asset_id)
    );""",
    """
    CREATE TABLE IF NOT EXISTS accounts (
        account_id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
        account_name TEXT NOT NULL UNIQUE,
        ownership_share REAL NOT NULL DEFAULT 1,
        account_group_name TEXT
    );""",
    """
    CREATE TABLE IF NOT EXISTS assets (
        asset_id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
        asset_name TEXT NOT NULL UNIQUE
    );""",
    """
    CREATE TABLE IF NOT EXISTS prices (
        asset_id INTEGER NOT NULL,
        price_date DATE NOT NULL,
        price DECIMAL(7,2) NOT NULL,
        UNIQUE(asset_id, price_date)
    );""",
    """
    CREATE TABLE IF NOT EXISTS categories (
        category_id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
        category_name TEXT NOT NULL UNIQUE
    );""",
    _SETTINGS_TABLE,
)

# Where the vault remembers its adjustments folder, stored relative to the
# vault file so moving or cloning the whole finance folder keeps working.
_ADJUSTMENTS_KEY = "adjustments_dir"


def _connect(filepath: Path, password: str | None):
    """Open a DB-API connection, keyed with SQLCipher when a password is given,
    and verify the result is actually readable (catches wrong passwords and
    plain-vs-encrypted mixups, which SQLite only reports on first read)."""
    if password is None:
        conn = sqlite3.connect(filepath)
        db_error = sqlite3.DatabaseError
    else:
        import sqlcipher3

        conn = sqlcipher3.connect(str(filepath))
        # PRAGMA does not support parameter binding; escape by doubling quotes.
        conn.execute("PRAGMA key = '{}'".format(password.replace("'", "''")))
        db_error = sqlcipher3.DatabaseError

    try:
        conn.execute("SELECT count(*) FROM sqlite_master")
    except db_error:
        conn.close()
        raise ValueError(
            f"Cannot read vault at {filepath}: "
            + ("wrong password" if password else "the file is not a vault")
        ) from None
    return conn


def _ids_from_names(
    names: Sequence[str], known_names: pd.Series, known_ids: pd.Series, kind: str
) -> list[int]:
    """Map names to their vault ids, matching case-insensitively; any name not
    in the vault raises."""
    lookup = dict(zip(known_names.str.lower(), known_ids))
    names = pd.Series(list(names), dtype=str)
    ids = names.str.lower().map(lookup)
    if ids.isna().any():
        unknown = sorted(set(names[ids.isna()]))
        raise ValueError(f"These {kind} are not registered in the vault: {', '.join(unknown)}")
    return ids.astype(int).tolist()


def _is_scalar(value) -> bool:
    """Whether an input stands for one value rather than a column of them.
    Strings are one value, and so is anything without a length (a date, a
    number, or None)."""
    return value is None or isinstance(value, str) or not hasattr(value, "__len__")


def _confirmed(question: str) -> bool:
    """Ask at the terminal before deleting something. Only a plain yes counts,
    so an empty line or a stray keystroke cancels."""
    return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")


def _plural(n: int, noun: str) -> str:
    """`3 transactions`, `1 transaction` — for the confirmation prompts."""
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _broadcast(inputs: dict[str, object]) -> dict[str, list]:
    """Line up parallel inputs, repeating any lone scalar to match the rest.
    All scalars means a single row; mismatched lengths raise."""
    lengths = {name: len(value) for name, value in inputs.items() if not _is_scalar(value)}
    if len(set(lengths.values())) > 1:
        raise ValueError(f"Inputs have mismatched lengths: {lengths}")
    n = next(iter(lengths.values()), 1)
    return {
        name: [value] * n if _is_scalar(value) else list(value) for name, value in inputs.items()
    }


class Vault:
    """A personal-finance vault: one SQLite database file holding accounts,
    assets, transactions, and asset prices.

    Do not call ``Vault(...)`` directly — get one from [`Vault.create`][goblinvest_core.Vault.create]
    (new file) or [`Vault.open`][goblinvest_core.Vault.open] (existing file).

    A vault can be used as a context manager so it closes itself:

    ```python
    with Vault.open("~/finance/MyVault.db") as v:
        accounts = v.list_accounts()
    # the vault is closed here, even if an error occurred
    ```

    Examples:
        ```python
        from goblinvest_core import Vault

        v = Vault.create("~/finance/MyVault.db")
        v.add_account("checking", account_group_name="cash")
        v.list_accounts()
        #    account_id account_name  ownership_share account_group_name
        # 0           1     checking              1.0               cash
        v.close()
        ```
    """

    def __init__(self, conn, filepath: Path):
        self._conn = conn
        self._filepath = filepath

    @property
    def adjustments_dir(self) -> Path:
        """The folder holding this vault's adjustments files.

        Set when the vault was created, remembered inside it, and pointed
        somewhere else by opening the vault with ``adjustments_dir=``.

        Returns:
            The folder as a `pathlib.Path`.

        Raises:
            FileNotFoundError: The vault does not know where its adjustments
                live — it predates them. Open it once with
                ``Vault.open(path, adjustments_dir=...)`` to tell it.
        """
        # Checked rather than caught: a vault predating adjustments has no
        # settings table at all, and that should read as the same message.
        known = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'settings';"
        ).fetchone()
        row = (
            self._conn.execute(
                "SELECT value FROM settings WHERE key = ?;", (_ADJUSTMENTS_KEY,)
            ).fetchone()
            if known
            else None
        )
        if row is None:
            raise FileNotFoundError(
                f"{self._filepath} does not know where its adjustments folder is. "
                "Open it once with Vault.open(path, adjustments_dir=...) to say where."
            )
        return (self._filepath.parent / row[0]).resolve()

    def _remember_adjustments_dir(self, adjustments_dir: str | Path) -> None:
        """Store the folder relative to the vault file, so the pair can move together."""
        relative = os.path.relpath(
            Path(adjustments_dir).expanduser().resolve(), self._filepath.parent.resolve()
        )
        with self._conn:
            # Also the migration for a vault made before adjustments existed:
            # pointing it at a folder is what gives it the table.
            self._conn.execute(_SETTINGS_TABLE)
            self._conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value;",
                (_ADJUSTMENTS_KEY, relative),
            )

    def _adjustments(self) -> Path:
        """The adjustments folder, with any missing files started for you.
        New kinds of adjustment therefore appear on their own, matching
        whatever encryption the folder already uses."""
        adjustments_dir = self.adjustments_dir
        if not adjustments_dir.is_dir():
            raise FileNotFoundError(
                f"This vault's adjustments folder is missing: {adjustments_dir}. "
                "Put it back, or open the vault with adjustments_dir= to point elsewhere."
            )
        _ensure_files(adjustments_dir, encrypted=_folder_is_encrypted(adjustments_dir))
        return adjustments_dir

    @classmethod
    def create(
        cls,
        filepath: str | Path,
        *,
        default_asset: str = "USD",
        encrypted: bool = False,
        overwrite: bool = False,
        adjustments_dir: str | Path | None = None,
    ) -> "Vault":
        """Create a new vault database file and return a handle to it.

        This also creates the vault's **adjustments folder** — the CSV files
        holding your decisions about the transactions, such as which category
        each belongs to. The vault remembers where that folder is, so no other
        call has to name it.

        Args:
            filepath: Full path of the file to create, e.g. ``"~/finance/MyVault.db"``.
                ``~`` is expanded. The parent directory must already exist.
            default_asset: Name of the base currency, stored as asset 1.
                Transactions that don't specify an asset are in this currency.
            encrypted: If ``True``, the file is encrypted on disk with SQLCipher
                and can only be opened again with the same password. The password
                is taken from [`ask_password`][goblinvest_core.ask_password] if one
                was entered in the last 15 minutes; otherwise you are prompted at
                the terminal — it is never passed in code. If ``False``, the file
                is a normal, unencrypted SQLite database readable by any SQLite tool.
                The adjustments files are encrypted along with the vault.
            overwrite: If ``True``, delete any existing file at ``filepath`` and
                start fresh. If ``False``, an existing file is an error. An
                adjustments folder that is already there is kept as it is.
            adjustments_dir: Where the adjustments files live, e.g.
                ``"~/finance/adjustments"``. Created if it does not exist yet.
                ``None`` (default) puts them in a folder named after the vault
                (``MyVault.db`` → ``MyVault_adjustments``) beside the vault file.

        Returns:
            An open `Vault`.

        Raises:
            FileNotFoundError: The parent directory does not exist, or the
                folder above ``adjustments_dir`` does not.
            FileExistsError: A file already exists at ``filepath`` and
                ``overwrite`` is ``False``.

        Examples:
            ```python
            v = Vault.create("~/finance/MyVault.db")
            v = Vault.create("/tmp/rebuild.db", overwrite=True)   # rebuild-from-scratch scripts
            v = Vault.create("~/secret.db", encrypted=True)       # prompts for a password

            # keep the adjustments with your statements, wherever those live
            v = Vault.create("~/finance/MyVault.db", adjustments_dir="~/statements/adjustments")
            ```
        """
        filepath = Path(filepath).expanduser()
        if not filepath.parent.is_dir():
            raise FileNotFoundError(f"No such directory: {filepath.parent}")
        if filepath.exists() and not overwrite:
            raise FileExistsError(
                f"A file already exists at {filepath} (pass overwrite=True to replace it)"
            )
        if adjustments_dir is None:
            adjustments_dir = filepath.parent / f"{filepath.stem}_adjustments"
        adjustments_dir = Path(adjustments_dir).expanduser()

        # Settle the password before touching the existing file, so a failed or
        # abandoned prompt can't leave the old vault already deleted.
        password = _get_password(confirm=True) if encrypted else None
        if filepath.exists():
            filepath.unlink()

        conn = _connect(filepath, password)
        with conn:
            for stmt in _SCHEMA:
                conn.execute(stmt)
            conn.execute(
                "INSERT INTO assets (asset_id, asset_name) VALUES (1, ?);",
                (default_asset,),
            )
        vault = cls(conn, filepath)
        # Never touches an adjustments folder that is already there, so a
        # rebuild keeps every decision you have made.
        _ensure_files(adjustments_dir, encrypted=encrypted)
        vault._remember_adjustments_dir(adjustments_dir)
        return vault

    @classmethod
    def open(cls, filepath: str | Path, *, adjustments_dir: str | Path | None = None) -> "Vault":
        """Open an existing vault file and return a handle to it.

        Whether the file is encrypted is detected automatically. For an
        encrypted vault, the password is taken from
        [`ask_password`][goblinvest_core.ask_password] if one was entered in
        the last 15 minutes; otherwise you are prompted at the terminal — a
        password is never passed in code. An unencrypted vault opens without
        any prompt.

        Args:
            filepath: Path of an existing vault file. ``~`` is expanded.
            adjustments_dir: Where this vault's adjustments files live. Only
                needed to *change* the answer — to point a vault at a folder
                you have moved, or to give one to a vault made before it had
                any. The new location is remembered from then on.

        Returns:
            An open `Vault`.

        Raises:
            FileNotFoundError: No file exists at ``filepath``.
            ValueError: The password is wrong (it is immediately forgotten, so
                the next attempt prompts again), or the file is not a vault.

        Examples:
            ```python
            v = Vault.open("~/finance/MyVault.db")
            v = Vault.open("~/secret.db")   # encrypted: prompts unless remembered

            # the adjustments folder moved
            v = Vault.open("~/finance/MyVault.db", adjustments_dir="~/statements/adjustments")
            ```
        """
        filepath = Path(filepath).expanduser()
        if not filepath.is_file():
            raise FileNotFoundError(f"No vault exists at {filepath}")
        with filepath.open("rb") as f:
            encrypted = f.read(16) != _SQLITE_MAGIC
        password = _get_password(confirm=False) if encrypted else None
        try:
            vault = cls(_connect(filepath, password), filepath)
        except ValueError:
            if encrypted:
                forget_password()
            raise
        if adjustments_dir is not None:
            adjustments_dir = Path(adjustments_dir).expanduser()
            _ensure_files(adjustments_dir, encrypted=_folder_is_encrypted(adjustments_dir))
            vault._remember_adjustments_dir(adjustments_dir)
        return vault

    def close(self) -> None:
        """Close the vault's database connection. The `Vault` object cannot be
        used afterwards; reopen with [`Vault.open`][goblinvest_core.Vault.open]."""
        self._conn.close()

    def __enter__(self) -> "Vault":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def _read_df(self, sql: str, params: tuple = ()) -> pd.DataFrame:
        # pd.read_sql only recognizes stdlib sqlite3 connections; going through
        # the cursor keeps plain and SQLCipher connections on one code path.
        cur = self._conn.execute(sql, params)
        return pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])

    def add_account(
        self,
        account_name: str,
        *,
        ownership_share: float = 1.0,
        account_group_name: str = "UNCLASSIFIED",
    ) -> None:
        """Register an account, or update its share/group if it already exists.

        Idempotent: adding the same ``account_name`` again never creates a
        duplicate — it updates ``ownership_share`` and ``account_group_name``
        in place, keeping the same ``account_id``.

        Args:
            account_name: Unique name for the account, e.g. ``"checking"``.
            ownership_share: Fraction of the account that belongs to you.
                A 50/50 joint account is ``0.5``; summaries multiply balances
                by this before rolling up to net worth.
            account_group_name: Free-form group label used to bucket accounts
                in summaries, e.g. ``"cash"``, ``"investments"``.

        Returns:
            Nothing.

        Examples:
            ```python
            v.add_account("checking", account_group_name="cash")
            v.add_account("joint-checking", ownership_share=0.5, account_group_name="cash")
            ```
        """
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO accounts (account_name, ownership_share, account_group_name)
                VALUES (?, ?, ?)
                ON CONFLICT (account_name) DO UPDATE SET
                    ownership_share = excluded.ownership_share,
                    account_group_name = excluded.account_group_name
                ;""",
                (account_name, ownership_share, account_group_name),
            )

    def list_accounts(self) -> pd.DataFrame:
        """Return all registered accounts.

        Returns:
            A pandas ``DataFrame`` with one row per account and columns
            ``account_id``, ``account_name``, ``ownership_share``,
            ``account_group_name``, ordered by ``account_id``.

        Examples:
            ```python
            v.list_accounts()
            #    account_id    account_name  ownership_share account_group_name
            # 0           1        checking              1.0               cash
            # 1           2  joint-checking              0.5               cash
            ```
        """
        return self._read_df(
            """
            SELECT account_id, account_name, ownership_share, account_group_name
            FROM accounts
            ORDER BY account_id
            ;"""
        )

    def delete_account(self, account_name: str, *, confirm: bool = True) -> None:
        """Delete an account and every transaction in it.

        You are asked to confirm at the terminal first, since the transactions
        go with it. The vault is rebuildable from your statement CSVs, so the
        way to undo this is to load them again.

        Args:
            account_name: The account to delete (capitalization ignored). It
                must be registered in the vault.
            confirm: If ``True`` (default), print what will be deleted and wait
                for a ``y`` at the terminal; anything else cancels and nothing
                is deleted. Pass ``False`` in a script, which has nobody to ask.

        Returns:
            Nothing.

        Raises:
            ValueError: The account is not registered in the vault.

        Examples:
            ```python
            v.delete_account("old-checking")
            # Delete account "old-checking" and its 412 transactions? [y/N]

            v.delete_account("old-checking", confirm=False)   # unattended
            ```
        """
        accounts = self.list_accounts()
        [account_id] = _ids_from_names(
            [account_name], accounts["account_name"], accounts["account_id"], "accounts"
        )
        name = accounts.loc[accounts["account_id"] == account_id, "account_name"].iloc[0]
        n_transactions = self._conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE account_id = ?;", (account_id,)
        ).fetchone()[0]
        if confirm and not _confirmed(
            f'Delete account "{name}" and its {_plural(n_transactions, "transaction")}?'
        ):
            return
        with self._conn:
            self._conn.execute("DELETE FROM transactions WHERE account_id = ?;", (account_id,))
            self._conn.execute("DELETE FROM accounts WHERE account_id = ?;", (account_id,))

    def add_asset(self, asset_name: str) -> None:
        """Register an asset — anything you can hold an amount of.

        The base currency (asset 1, named when the vault is created) is already
        registered; add tickers, other currencies, or anything else that
        transactions will be denominated in, e.g. ``"VTI"``, ``"EUR"``, ``"BTC"``.

        Idempotent: adding the same ``asset_name`` again never creates a
        duplicate and keeps the same ``asset_id``.

        Args:
            asset_name: Unique name for the asset, e.g. ``"VTI"``.

        Returns:
            Nothing.

        Examples:
            ```python
            v.add_asset("VTI")
            v.add_asset("EUR")
            ```
        """
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO assets (asset_name)
                VALUES (?)
                ON CONFLICT (asset_name) DO UPDATE SET
                    asset_name = excluded.asset_name
                ;""",
                (asset_name,),
            )

    def list_assets(self) -> pd.DataFrame:
        """Return all registered assets.

        Returns:
            A pandas ``DataFrame`` with one row per asset and columns
            ``asset_id``, ``asset_name``, ordered by ``asset_id``. Asset 1 is
            the vault's base currency.

        Examples:
            ```python
            v.list_assets()
            #    asset_id asset_name
            # 0         1        USD
            # 1         2        VTI
            ```
        """
        return self._read_df(
            """
            SELECT asset_id, asset_name
            FROM assets
            ORDER BY asset_id
            ;"""
        )

    def delete_asset(self, asset_name: str, *, confirm: bool = True) -> None:
        """Delete an asset, every transaction denominated in it, and its stored
        prices.

        You are asked to confirm at the terminal first, since the transactions
        go with it. The vault is rebuildable from your statement CSVs, so the
        way to undo this is to load them again.

        The base currency (asset 1) cannot be deleted — every other asset is
        valued in it.

        Args:
            asset_name: The asset to delete (capitalization ignored). It must
                be registered in the vault.
            confirm: If ``True`` (default), print what will be deleted and wait
                for a ``y`` at the terminal; anything else cancels and nothing
                is deleted. Pass ``False`` in a script, which has nobody to ask.

        Returns:
            Nothing.

        Raises:
            ValueError: The asset is not registered in the vault, or it is the
                base currency.

        Examples:
            ```python
            v.delete_asset("VTI")
            # Delete asset "VTI", its 37 transactions, and 2,410 stored prices? [y/N]
            ```
        """
        assets = self.list_assets()
        [asset_id] = _ids_from_names(
            [asset_name], assets["asset_name"], assets["asset_id"], "assets"
        )
        name = assets.loc[assets["asset_id"] == asset_id, "asset_name"].iloc[0]
        if asset_id == 1:
            raise ValueError(
                f"{name} is this vault's base currency, so it cannot be deleted: "
                "every other asset is valued in it."
            )
        n_transactions = self._conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE asset_id = ?;", (asset_id,)
        ).fetchone()[0]
        n_prices = self._conn.execute(
            "SELECT COUNT(*) FROM prices WHERE asset_id = ?;", (asset_id,)
        ).fetchone()[0]
        if confirm and not _confirmed(
            f'Delete asset "{name}", its {_plural(n_transactions, "transaction")}, '
            f"and {_plural(n_prices, 'stored price')}?"
        ):
            return
        with self._conn:
            self._conn.execute("DELETE FROM transactions WHERE asset_id = ?;", (asset_id,))
            self._conn.execute("DELETE FROM prices WHERE asset_id = ?;", (asset_id,))
            self._conn.execute("DELETE FROM assets WHERE asset_id = ?;", (asset_id,))

    def add_transactions(
        self,
        accounts: str | Sequence[str],
        dates: Sequence[datetime.date | str],
        descriptions: Sequence[str],
        amounts: Sequence[float],
        assets: str | Sequence[str] | None = None,
    ) -> None:
        """Record transactions in the ledger — one row per amount of one asset
        moving in or out of one account.

        A $40 grocery charge is one row (``-40.00`` of the base currency). A
        brokerage purchase is two rows on the same date: the money leaving
        (``-1000.00``, asset ``"USD"``) and the shares arriving (``+3.2``,
        asset ``"VTI"``). A transfer between two of your accounts is two
        ordinary rows, one per account.

        Idempotent: loading the same transactions again (for example,
        re-running a script over a whole statement CSV) never double-counts —
        rows that already exist in the vault are left as they are. Rows that
        are *identical within one call* are treated as genuinely distinct
        transactions (two identical coffee purchases on the same day) and are
        kept apart by suffixing the repeats' descriptions with ``" (2)"``,
        ``" (3)"``, ...

        Args:
            accounts: Account name for each transaction. A single string
                applies to all of them. Names must already be registered with
                [`add_account`][goblinvest_core.Vault.add_account] (matched
                case-insensitively) — unknown names raise.
            dates: Date of each transaction, as ``datetime.date`` objects or
                ``"YYYY-MM-DD"`` strings.
            descriptions: Free-form description of each transaction, e.g. the
                statement's own text.
            amounts: Signed amount of each transaction: positive into the
                account, negative out of it. Denominated in the transaction's
                asset (dollars for USD, shares for a ticker).
            assets: Asset name for each transaction, or a single string for
                all of them. Names must already be registered with
                [`add_asset`][goblinvest_core.Vault.add_asset] (matched
                case-insensitively). ``None`` (default) means the vault's base
                currency.

        Returns:
            Nothing.

        Raises:
            ValueError: The inputs have mismatched lengths, or an account or
                asset name is not registered in the vault.

        Examples:
            ```python
            # two grocery charges, base currency
            v.add_transactions(
                "checking",
                ["2026-07-01", "2026-07-03"],
                ["WHOLEFDS #123", "TRADER JOE'S"],
                [-40.00, -23.17],
            )

            # a brokerage buy: dollars out, shares in
            v.add_transactions(
                "brokerage",
                ["2026-07-02", "2026-07-02"],
                ["buy VTI", "buy VTI"],
                [-1000.00, 3.2],
                assets=["USD", "VTI"],
            )
            ```
        """
        n = len(dates)
        if isinstance(accounts, str):
            accounts = [accounts] * n
        if isinstance(assets, str):
            assets = [assets] * n

        lengths = {
            "accounts": len(accounts),
            "dates": n,
            "descriptions": len(descriptions),
            "amounts": len(amounts),
        }
        if assets is not None:
            lengths["assets"] = len(assets)
        if len(set(lengths.values())) > 1:
            raise ValueError(f"Inputs have mismatched lengths: {lengths}")

        accounts_df = self.list_accounts()
        account_ids = _ids_from_names(
            accounts, accounts_df["account_name"], accounts_df["account_id"], "accounts"
        )
        if assets is None:
            asset_ids = [1] * n
        else:
            assets_df = self.list_assets()
            asset_ids = _ids_from_names(
                assets, assets_df["asset_name"], assets_df["asset_id"], "assets"
            )

        df = pd.DataFrame(
            {
                "account_id": account_ids,
                "date": pd.to_datetime(list(dates)).strftime("%Y-%m-%d"),
                "description": pd.Series(list(descriptions), dtype=str),
                "amount": pd.Series(list(amounts), dtype=float),
                "asset_id": asset_ids,
            }
        )

        # Repeats of an identical row within this call get " (2)", " (3)", ...
        # appended to their descriptions so the ledger keeps them all.
        occurrence = df.groupby(list(df.columns)).cumcount()
        df["description"] = df["description"].where(
            occurrence == 0,
            df["description"] + " (" + (occurrence + 1).astype(str) + ")",
        )

        rows = zip(
            df["account_id"].tolist(),
            df["date"].tolist(),
            df["description"].tolist(),
            df["amount"].tolist(),
            df["asset_id"].tolist(),
        )
        with self._conn:
            self._conn.executemany(
                """
                INSERT INTO transactions (account_id, trans_date, trans_desc, amount, asset_id)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (account_id, trans_date, trans_desc, amount, asset_id)
                DO UPDATE SET amount = excluded.amount
                ;""",
                rows,
            )

    def list_transactions(
        self,
        *,
        start_date: datetime.date | str | None = None,
        end_date: datetime.date | str | None = None,
    ) -> pd.DataFrame:
        """Return the ledger, with account and asset details joined in.

        Without arguments this returns every transaction. Give a start date, an
        end date, or both to narrow it to a date range.

        Args:
            start_date: Earliest date to include, as a ``datetime.date`` object
                or a ``"YYYY-MM-DD"`` string. Included in the result.
            end_date: Latest date to include, same formats. Included in the
                result.

        Returns:
            A pandas ``DataFrame`` with one row per transaction, sorted by
            ``date`` then ``account_name``, with columns:

            - ``transaction_id`` — unique id of the transaction
            - ``account_name`` — account the transaction belongs to
            - ``date`` — transaction date (pandas datetime)
            - ``description`` — free-form description
            - ``amount`` — signed amount, in the transaction's asset
            - ``asset`` — name of the asset the amount is denominated in
            - ``category`` — the transaction's category, ``"unclassified"`` if none
            - ``ownership_share`` — your fraction of the account
            - ``account_group_name`` — the account's group label

        Examples:
            ```python
            v.list_transactions()
            #    transaction_id account_name       date    description   amount asset      category  ownership_share account_group_name
            # 0               1     checking 2026-07-01  WHOLEFDS #123   -40.00   USD     groceries              1.0               cash
            # 1               2    brokerage 2026-07-02        buy VTI -1000.00   USD  unclassified              1.0        investments
            # 2               3    brokerage 2026-07-02        buy VTI     3.20   VTI  unclassified              1.0        investments

            v.list_transactions(start_date="2026-07-02")
            #    transaction_id account_name       date description   amount asset      category  ownership_share account_group_name
            # 0               2    brokerage 2026-07-02     buy VTI -1000.00   USD  unclassified              1.0        investments
            # 1               3    brokerage 2026-07-02     buy VTI     3.20   VTI  unclassified              1.0        investments
            ```
        """
        # Dates are stored as "YYYY-MM-DD" text, which sorts and compares
        # correctly as-is, so the bounds only need the same formatting.
        where = []
        params: list[str] = []
        if start_date is not None:
            where.append("trans_date >= ?")
            params.append(pd.Timestamp(start_date).strftime("%Y-%m-%d"))
        if end_date is not None:
            where.append("trans_date <= ?")
            params.append(pd.Timestamp(end_date).strftime("%Y-%m-%d"))

        df = self._read_df(
            f"""
            SELECT trans_id, account_name, trans_date, trans_desc, amount,
                   asset_name, category_name, ownership_share, account_group_name
            FROM transactions
            LEFT JOIN accounts ON accounts.account_id = transactions.account_id
            LEFT JOIN assets ON assets.asset_id = transactions.asset_id
            LEFT JOIN categories ON categories.category_id = transactions.category_id
            {"WHERE " + " AND ".join(where) if where else ""}
            ORDER BY trans_date, account_name
            ;""",
            tuple(params),
        )
        df.columns = [
            "transaction_id",
            "account_name",
            "date",
            "description",
            "amount",
            "asset",
            "category",
            "ownership_share",
            "account_group_name",
        ]
        df["date"] = pd.to_datetime(df["date"])
        df["category"] = df["category"].fillna(UNCLASSIFIED)
        return df

    def add_category(self, categories: str | Sequence[str]) -> None:
        """Define a category that rules and exceptions can then use.

        Rules and exceptions may only use categories defined here, so a
        misspelled category is an error rather than a new category.

        Already-defined categories are skipped, so this is safe to run
        repeatedly. Capitalization does not make a second category: once
        ``"groceries"`` is defined, ``"Groceries"`` means the same thing, and
        the spelling you defined is the one that gets used.

        Args:
            categories: The category name, or a list of them, e.g.
                ``"groceries"``. Free-form text; ``"unclassified"`` is reserved
                because it means "no category".

        Returns:
            Nothing.

        Raises:
            FileNotFoundError: The adjustments folder is missing.
            ValueError: A category is empty or the reserved name
                ``"unclassified"``.

        Examples:
            ```python
            v.add_category("groceries")
            v.add_category(["rent", "travel", "dining"])
            ```
        """
        adjustments_dir = self._adjustments()
        names = [categories] if isinstance(categories, str) else list(categories)
        new = pd.Series(names, dtype=str).str.strip()
        if (new == "").any():
            raise ValueError("category cannot be empty")
        if (new.str.lower() == UNCLASSIFIED).any():
            raise ValueError(f'"{UNCLASSIFIED}" is reserved: it means no category')

        defined, _ = _read_category_file(adjustments_dir, _CATEGORIES_FILE, "categories")
        known = set(defined["category"].str.lower())
        lowered = new.str.lower()
        new = new[~lowered.duplicated(keep="first") & ~lowered.isin(known)]
        if not new.empty:
            _write_file(
                adjustments_dir,
                _CATEGORIES_FILE,
                pd.concat([defined, pd.DataFrame({"category": new})], ignore_index=True),
            )

    def delete_category(self, categories: str | Sequence[str], *, confirm: bool = True) -> None:
        """Delete a category, and every rule and exception that hands it out.

        No transaction is deleted: the ones that had the category become
        ``"unclassified"``, as if it had never been defined. Your adjustments
        files are rewritten without it, so a rebuild does not bring it back.

        You are asked to confirm at the terminal first, since the rules that go
        with it are decisions you made by hand.

        Args:
            categories: The category to delete, or a list of them
                (capitalization ignored). Each must already be defined with
                [`add_category`][goblinvest_core.Vault.add_category].
            confirm: If ``True`` (default), print what will be deleted and wait
                for a ``y`` at the terminal; anything else cancels and nothing
                is deleted. Pass ``False`` in a script, which has nobody to ask.

        Returns:
            Nothing.

        Raises:
            FileNotFoundError: The adjustments folder is missing.
            ValueError: A category is not defined.

        Examples:
            ```python
            v.delete_category("streaming")
            # Delete category "streaming", 3 rules, 1 exception, and unclassify
            # 84 transactions? [y/N]

            v.delete_category(["streaming", "fun"], confirm=False)
            ```
        """
        adjustments_dir = self._adjustments()
        names = [categories] if isinstance(categories, str) else list(categories)
        wanted = pd.Series(names, dtype=str).str.strip()

        defined, _ = _read_category_file(adjustments_dir, _CATEGORIES_FILE, "categories")
        canonical = _canonical_categories(defined)
        lowered = wanted.str.lower()
        unknown = sorted(set(wanted[~lowered.isin(canonical)]))
        if unknown:
            raise ValueError(f"These categories are not defined: {', '.join(unknown)}")
        doomed = set(lowered)

        rules, _ = _read_category_file(adjustments_dir, _RULES_FILE, "category rules")
        exceptions, _ = _read_category_file(
            adjustments_dir, _EXCEPTIONS_FILE, "category exceptions"
        )
        drop_rules = rules["category"].str.lower().isin(doomed)
        drop_exceptions = exceptions["category"].str.lower().isin(doomed)

        counts = self.list_categories()
        n_transactions = int(
            counts.loc[counts["category_name"].str.lower().isin(doomed), "n_transactions"].sum()
        )
        if confirm:
            spelled = ", ".join(f'"{canonical[name]}"' for name in lowered.drop_duplicates())
            kind = "category" if len(doomed) == 1 else "categories"
            if not _confirmed(
                f"Delete {kind} {spelled}, {_plural(int(drop_rules.sum()), 'rule')}, "
                f"{_plural(int(drop_exceptions.sum()), 'exception')}, and unclassify "
                f"{_plural(n_transactions, 'transaction')}?"
            ):
                return

        # Only rewrite what actually changes, so a folder under version control
        # stays quiet, and re-encryption is not paid for nothing.
        _write_file(
            adjustments_dir,
            _CATEGORIES_FILE,
            defined[~defined["category"].str.lower().isin(doomed)],
        )
        if drop_rules.any():
            _write_file(adjustments_dir, _RULES_FILE, rules[~drop_rules])
        if drop_exceptions.any():
            _write_file(adjustments_dir, _EXCEPTIONS_FILE, exceptions[~drop_exceptions])
        self.apply_categories()

    def apply_categories(self) -> pd.DataFrame:
        """Recompute every transaction's category from your adjustments files.

        The vault's category state is rewritten from the files each time, so
        this is safe to run as often as you like. The files live in the folder
        the vault remembers — see
        [`adjustments_dir`][goblinvest_core.Vault.adjustments_dir].

        **The categories file** (``categories.csv``, column ``category``) lists
        the categories defined with
        [`add_category`][goblinvest_core.Vault.add_category]. The other
        two files may only use categories from this list.

        **The rules file** (``category_rules.csv``, columns
        ``pattern,category``) does the bulk. A transaction takes a rule's
        category when its description equals the pattern, ignoring
        capitalization. When two rules share a pattern, the one nearer the
        bottom of the file wins. Same-day duplicate transactions (stored as
        ``"Netflix (2)"``) match their base description's rule.

        **The exceptions file** (``category_exceptions.csv``, columns
        ``account,date,description,amount,asset,category``) categorizes one
        exact transaction, and beats every rule. Its first five columns must
        match the transaction as `list_transactions` shows it (``asset`` may be
        left blank for the base currency); account and asset names ignore
        capitalization.

        Transactions no file claims are ``"unclassified"`` — see
        [`list_uncategorized`][goblinvest_core.Vault.list_uncategorized].

        Returns:
            A pandas ``DataFrame`` of exception rows that matched no
            transaction (same columns as the file), so a typo or a reworded
            bank description never fails silently. Empty means all clean.

        Raises:
            FileNotFoundError: The adjustments folder is missing, or the vault
                does not know where it is.
            ValueError: A file is malformed — missing columns, an empty
                pattern or category, the reserved category name
                ``"unclassified"``, or a category that has not been defined
                with [`add_category`][goblinvest_core.Vault.add_category].

        Examples:
            ```python
            orphans = v.apply_categories()
            if not orphans.empty:
                print("These exceptions no longer match anything:", orphans)
            ```
        """
        adjustments_dir = self._adjustments()
        defined, _ = _read_category_file(adjustments_dir, _CATEGORIES_FILE, "categories")
        rules, rules_path = _read_category_file(adjustments_dir, _RULES_FILE, "category rules")
        exceptions, exceptions_path = _read_category_file(
            adjustments_dir, _EXCEPTIONS_FILE, "category exceptions"
        )
        bad = rules["pattern"].isna() | (rules["pattern"].str.strip() == "")
        if bad.any():
            raise ValueError(f"{rules_path} has {int(bad.sum())} row(s) with an empty pattern")

        # Both files may only hand out categories you have defined, and they
        # hand them out under the spelling you defined them with.
        canonical = _canonical_categories(defined)
        rules["category"] = _resolve_categories(rules["category"], canonical, source=rules_path)
        exceptions["category"] = _resolve_categories(
            exceptions["category"], canonical, source=exceptions_path
        )

        ledger = self._read_df(
            """
            SELECT trans_id, account_name, trans_date, trans_desc, amount, asset_name
            FROM transactions
            LEFT JOIN accounts ON accounts.account_id = transactions.account_id
            LEFT JOIN assets ON assets.asset_id = transactions.asset_id
            ;"""
        )

        # A dict keeps the last value per key, which is exactly last-rule-wins.
        rule_of = dict(zip(_normalize_desc(rules["pattern"]), rules["category"]))
        category = _normalize_desc(ledger["trans_desc"].astype(str)).map(rule_of)

        # Exceptions: normalize the file's key columns to the ledger's form,
        # keep the last row per key, and match with an exact merge.
        base_asset = self._conn.execute(
            "SELECT asset_name FROM assets WHERE asset_id = 1;"
        ).fetchone()[0]
        try:
            exc_amount = exceptions["amount"].astype(float)
        except ValueError:
            raise ValueError(f"{exceptions_path} has a non-numeric amount") from None
        exc_asset = exceptions["asset"].fillna("").str.strip()
        exc = pd.DataFrame(
            {
                "account": exceptions["account"].fillna("").str.strip().str.lower(),
                "date": pd.to_datetime(exceptions["date"], errors="coerce").dt.strftime("%Y-%m-%d"),
                "description": exceptions["description"].fillna(""),
                "amount": exc_amount,
                "asset": exc_asset.where(exc_asset != "", base_asset).str.lower(),
                "category": exceptions["category"],
            }
        )
        key = ["account", "date", "description", "amount", "asset"]
        exc = exc[~exc.duplicated(subset=key, keep="last")]

        matched: set[int] = set()
        if not exc.empty and not ledger.empty:
            led = pd.DataFrame(
                {
                    "account": ledger["account_name"].str.lower(),
                    "date": ledger["trans_date"],
                    "description": ledger["trans_desc"],
                    "amount": ledger["amount"].astype(float),
                    "asset": ledger["asset_name"].str.lower(),
                }
            )
            m = led.merge(exc.reset_index(names="_row"), on=key, how="left")
            category = category.where(m["category"].isna(), m["category"])
            matched = set(m["_row"].dropna().astype(int))
        orphans = exceptions.loc[sorted(set(exc.index) - matched)].reset_index(drop=True)

        # Every category you have defined goes in, used or not, so a count of
        # zero is visible rather than invisible.
        names = sorted(set(canonical.values()))
        id_of = {name: i for i, name in enumerate(names, start=1)}
        assigned = category.notna()
        with self._conn:
            self._conn.execute("UPDATE transactions SET category_id = NULL;")
            self._conn.execute("DELETE FROM categories;")
            self._conn.executemany(
                "INSERT INTO categories (category_id, category_name) VALUES (?, ?);",
                [(i, name) for name, i in id_of.items()],
            )
            self._conn.executemany(
                "UPDATE transactions SET category_id = ? WHERE trans_id = ?;",
                zip(
                    category[assigned].map(id_of).tolist(),
                    ledger.loc[assigned, "trans_id"].tolist(),
                ),
            )
        return orphans

    def set_category_rule(
        self,
        descriptions: str | Sequence[str],
        categories: str | Sequence[str],
    ) -> None:
        """Categorize every transaction with a given description.

        Each decision is appended as a rule in your rules file (the description
        becomes the pattern), then your adjustments are re-applied. The
        category covers every transaction with that description, including ones
        in statements you load later. To categorize a single transaction
        without touching its look-alikes, use
        [`set_category_exception`][goblinvest_core.Vault.set_category_exception]
        instead.

        One description or many: pass parallel lists to record a batch of
        decisions at once, and a lone string applies to all of them. A batch
        costs one re-apply rather than one per decision.

        Nothing is appended for a description the file already gives that
        category. Giving a different category appends a line, which wins
        because later rules beat earlier ones.

        Args:
            descriptions: Transaction description, or a list of them, exactly
                as `list_transactions` shows them. A same-day duplicate suffix
                like ``" (2)"`` is stripped for you.
            categories: Category name for each description, or a single one
                for all of them. Each must already be defined with
                [`add_category`][goblinvest_core.Vault.add_category]
                (capitalization ignored). Naming the same description twice in
                one call keeps the last category given.

        Returns:
            Nothing.

        Raises:
            FileNotFoundError: The adjustments folder is missing.
            ValueError: The inputs have mismatched lengths, a description or
                category is empty, a category is the reserved name
                ``"unclassified"``, or a category has not been defined.
                Nothing is written when this happens.

        Examples:
            ```python
            v.set_category_rule("Netflix", "streaming")

            # one category across many descriptions
            v.set_category_rule(["Trader Joe's", "SAFEWAY #1042", "COSTCO WHSE"], "groceries")

            # or clear the whole to-do list in one go
            v.set_category_rule(v.list_uncategorized()["description"], "misc")
            ```
        """
        columns = _broadcast({"descriptions": descriptions, "categories": categories})
        new = pd.DataFrame(
            {
                "pattern": pd.Series(columns["descriptions"], dtype=str),
                "category": pd.Series(columns["categories"], dtype=str),
            }
        )
        new["category"] = new["category"].str.strip()
        if (new["category"] == "").any():
            raise ValueError("category cannot be empty")
        reserved = new["category"].str.lower() == UNCLASSIFIED
        if reserved.any():
            raise ValueError(f'"{UNCLASSIFIED}" is reserved: it means no category')
        new["pattern"] = _strip_dup_suffix(new["pattern"].str.strip()).str.strip()
        if (new["pattern"] == "").any():
            raise ValueError("description cannot be empty")

        # An undefined category is refused before the file is touched.
        adjustments_dir = self._adjustments()
        defined, _ = _read_category_file(adjustments_dir, _CATEGORIES_FILE, "categories")
        new["category"] = _resolve_categories(new["category"], _canonical_categories(defined))

        rules, _ = _read_category_file(adjustments_dir, _RULES_FILE, "category rules")
        rule_of = dict(
            zip(_normalize_desc(rules["pattern"].fillna("")), rules["category"].str.lower())
        )
        # One decision per description (the last one given), and only the ones
        # the file doesn't already make.
        key = _normalize_desc(new["pattern"])
        new = new[~key.duplicated(keep="last") & (key.map(rule_of) != new["category"].str.lower())]
        if not new.empty:
            _write_file(adjustments_dir, _RULES_FILE, pd.concat([rules, new], ignore_index=True))
        self.apply_categories()

    def set_category_exception(
        self,
        accounts: str | Sequence[str],
        dates: datetime.date | str | Sequence[datetime.date | str],
        descriptions: str | Sequence[str],
        amounts: float | Sequence[float],
        categories: str | None | Sequence[str | None],
        *,
        assets: str | None | Sequence[str | None] = None,
    ) -> None:
        """Categorize exact transactions, without affecting their look-alikes.

        Where [`set_category_rule`][goblinvest_core.Vault.set_category_rule]
        categorizes every transaction with a description, this categorizes one
        transaction whatever the rules say. Each decision is recorded in your
        exceptions file (replacing any earlier exception for the same
        transaction), then your adjustments are re-applied.

        One transaction or many: pass parallel lists — the same shape
        [`add_transactions`][goblinvest_core.Vault.add_transactions] takes —
        with a lone value applying to all of them.

        Args:
            accounts: Account name for each transaction, or one for all of
                them (capitalization ignored).
            dates: Date of each transaction, as ``datetime.date`` objects or
                ``"YYYY-MM-DD"`` strings.
            descriptions: The description of each, exactly as
                `list_transactions` shows it — including any ``" (2)"``
                suffix, which is what pins down one of two otherwise-identical
                transactions.
            amounts: The signed amount of each transaction.
            categories: Category name for each, or one for all of them, or
                ``None`` to remove an existing exception (the transaction falls
                back to the rules on the re-apply). A list may mix names and
                ``None`` to set some and clear others. Each name must already
                be defined with
                [`add_category`][goblinvest_core.Vault.add_category].
            assets: Asset name for each transaction (capitalization ignored),
                or one for all. ``None`` (default) means the vault's base
                currency.

        Returns:
            Nothing.

        Raises:
            FileNotFoundError: The adjustments folder is missing.
            ValueError: The inputs have mismatched lengths, no transaction in
                the vault matches a row (every unmatched row is named), or a
                category is empty, undefined, or the reserved name
                ``"unclassified"``. Nothing is written when this happens.

        Examples:
            ```python
            v.set_category_exception(
                "checking",
                "2026-02-14",
                "CHECK # 1145",
                -500.00,
                "gifts",
            )

            # a batch: two checks that were really gifts
            v.set_category_exception(
                "checking",
                ["2026-02-14", "2026-03-14"],
                ["CHECK # 1145", "CHECK # 1146"],
                [-500.00, -200.00],
                "gifts",
            )
            ```
        """
        columns = _broadcast(
            {
                "accounts": accounts,
                "dates": dates,
                "descriptions": descriptions,
                "amounts": amounts,
                "categories": categories,
                "assets": assets,
            }
        )
        base_asset = self._conn.execute(
            "SELECT asset_name FROM assets WHERE asset_id = 1;"
        ).fetchone()[0]

        category = pd.Series(columns["categories"], dtype=object)
        given = category.notna()
        category = category.where(~given, category[given].astype(str).str.strip())
        if (category[given] == "").any():
            raise ValueError("category cannot be empty (pass None to remove an exception)")
        if (category[given].str.lower() == UNCLASSIFIED).any():
            raise ValueError(f'"{UNCLASSIFIED}" is reserved: it means no category')
        # An undefined category is refused before the file is touched.
        adjustments_dir = self._adjustments()
        defined, _ = _read_category_file(adjustments_dir, _CATEGORIES_FILE, "categories")
        category = _resolve_categories(category, _canonical_categories(defined))

        asset = pd.Series(columns["assets"], dtype=object).fillna(base_asset)
        new = pd.DataFrame(
            {
                "account": pd.Series(columns["accounts"], dtype=str).str.strip(),
                "date": pd.to_datetime(list(columns["dates"])).strftime("%Y-%m-%d"),
                "description": pd.Series(columns["descriptions"], dtype=str),
                "amount": pd.Series(columns["amounts"], dtype=float),
                "asset": asset.astype(str).str.strip(),
                "category": category,
            }
        )

        # Every row must name a real transaction, and all the bad ones are
        # reported at once rather than one call at a time.
        ledger = self._read_df(
            """
            SELECT account_name, trans_date, trans_desc, amount, asset_name
            FROM transactions
            LEFT JOIN accounts ON accounts.account_id = transactions.account_id
            LEFT JOIN assets ON assets.asset_id = transactions.asset_id
            ;"""
        )
        key = ["account", "date", "description", "amount", "asset"]
        led = pd.DataFrame(
            {
                "account": ledger["account_name"].str.lower(),
                "date": ledger["trans_date"],
                "description": ledger["trans_desc"],
                "amount": ledger["amount"].astype(float),
                "asset": ledger["asset_name"].str.lower(),
            }
        ).drop_duplicates()
        lowered = new[key].assign(
            account=new["account"].str.lower(), asset=new["asset"].str.lower()
        )
        unmatched = lowered.merge(led, on=key, how="left", indicator=True)["_merge"] == "left_only"
        if unmatched.any():
            missing = new[unmatched.to_numpy()]
            shown = "\n".join(
                f"  account={r.account!r}, date={r.date}, description={r.description!r}, "
                f"amount={r.amount}, asset={r.asset!r}"
                for r in missing.head(5).itertuples()
            )
            more = f"\n  ... and {len(missing) - 5} more" if len(missing) > 5 else ""
            raise ValueError(f"No transaction matches:\n{shown}{more}")

        # One exception per transaction (the last one given for it).
        new = new[~lowered.duplicated(keep="last").to_numpy()]

        exceptions, _ = _read_category_file(
            adjustments_dir, _EXCEPTIONS_FILE, "category exceptions"
        )
        exc_asset = exceptions["asset"].fillna("").str.strip()
        old = pd.DataFrame(
            {
                "account": exceptions["account"].fillna("").str.strip().str.lower(),
                "date": pd.to_datetime(exceptions["date"], errors="coerce").dt.strftime("%Y-%m-%d"),
                "description": exceptions["description"].fillna(""),
                "amount": pd.to_numeric(exceptions["amount"], errors="coerce"),
                "asset": exc_asset.where(exc_asset != "", base_asset).str.lower(),
            }
        )
        # Drop whatever the file already said about these transactions; rows
        # given a category come back, rows given None stay gone.
        superseded = pd.MultiIndex.from_frame(old).isin(
            pd.MultiIndex.from_frame(lowered.loc[new.index])
        )
        exceptions = exceptions[~superseded]
        keep = new[new["category"].notna()]
        if not keep.empty:
            exceptions = pd.concat([exceptions, keep], ignore_index=True)
        _write_file(adjustments_dir, _EXCEPTIONS_FILE, exceptions)
        self.apply_categories()

    def list_categories(self) -> pd.DataFrame:
        """Return every defined category and how many transactions it covers.

        Returns:
            A pandas ``DataFrame`` with one row per defined category and
            columns ``category_id``, ``category_name``, ``n_transactions``,
            ordered by ``category_id``. Categories nothing has reached yet are
            listed too, with ``n_transactions`` of zero.

        Examples:
            ```python
            v.list_categories()
            #    category_id category_name  n_transactions
            # 0            1        dining              84
            # 1            2     groceries             311
            ```
        """
        return self._read_df(
            """
            SELECT categories.category_id, category_name, COUNT(trans_id) AS n_transactions
            FROM categories
            LEFT JOIN transactions ON transactions.category_id = categories.category_id
            GROUP BY categories.category_id, category_name
            ORDER BY categories.category_id
            ;"""
        )

    def list_uncategorized(self) -> pd.DataFrame:
        """Return the transactions that still need categorizing.

        They are grouped by description (same-day duplicate suffixes like
        ``" (2)"`` are ignored), so each row is one candidate rule for the
        rules file, sorted so the row covering the most transactions comes
        first.

        Returns:
            A pandas ``DataFrame`` with columns ``account_name``,
            ``description``, ``n_transactions``, ``total_amount``, sorted by
            ``n_transactions`` descending. Empty when everything is
            categorized.

        Examples:
            ```python
            v.list_uncategorized()
            #   account_name    description  n_transactions  total_amount
            # 0  credit-card   Trader Joe's              41      -3105.22
            # 1     checking  ATM withdrawal             12       -840.00
            ```
        """
        columns = ["account_name", "description", "n_transactions", "total_amount"]
        raw = self._read_df(
            """
            SELECT account_name, trans_desc, amount
            FROM transactions
            LEFT JOIN accounts ON accounts.account_id = transactions.account_id
            WHERE transactions.category_id IS NULL
            ;"""
        )
        if raw.empty:
            return pd.DataFrame(columns=columns)
        out = (
            raw.assign(description=_strip_dup_suffix(raw["trans_desc"].astype(str)))
            .groupby(["account_name", "description"], sort=False)
            .agg(n_transactions=("amount", "size"), total_amount=("amount", "sum"))
            .reset_index()
        )
        out["total_amount"] = out["total_amount"].round(2)
        out = out.sort_values(
            ["n_transactions", "account_name", "description"], ascending=[False, True, True]
        )
        return out.reset_index(drop=True)[columns]

    def populate_yfinance_prices(self, assets: str | Sequence[str]) -> None:
        """Fetch daily prices from Yahoo Finance and store them in the vault.

        For each asset named, daily closing prices are fetched from the date of
        that asset's first transaction through today and stored in the vault.
        Re-running fills in the days since the last run. Requires internet
        access.

        Stored prices are what the asset traded at on the day, so shares held ×
        stored price matches the statement from that date. That means two
        departures from what Yahoo displays:

        - **Splits are un-adjusted.** Yahoo rewrites history after a stock
          split (after a 10-for-1 split, a pre-split $1,200 close is served as
          $120); those rewrites are undone here.
        - **Dividends are not deducted.** A dividend lands in the ledger as a
          cash transaction when you load the statement CSV, so prices must not
          also account for it.

        Args:
            assets: Asset name(s) to price; each must be a ticker symbol Yahoo
                Finance recognizes, e.g. ``"VTI"``. A single string works.
                Names must already be registered with
                [`add_asset`][goblinvest_core.Vault.add_asset] (matched
                case-insensitively).

        Returns:
            Nothing.

        Raises:
            ValueError: An asset is not registered in the vault, has no
                transactions (so there is no date to fetch from), or Yahoo
                Finance returns no prices for it (not a real ticker, or
                delisted).

        Examples:
            ```python
            v.populate_yfinance_prices(["VTI", "NVDA"])
            ```
        """
        if isinstance(assets, str):
            assets = [assets]
        assets_df = self.list_assets()
        asset_ids = _ids_from_names(
            assets, assets_df["asset_name"], assets_df["asset_id"], "assets"
        )

        # Imported here because yfinance takes ~1s to import; keeps
        # `import goblinvest_core` fast for everyone not fetching prices.
        import yfinance

        for name, asset_id in zip(assets, asset_ids):
            first_date = self._conn.execute(
                "SELECT min(trans_date) FROM transactions WHERE asset_id = ?",
                (asset_id,),
            ).fetchone()[0]
            if first_date is None:
                raise ValueError(
                    f"No transactions involve {name}, so there is no date to "
                    "fetch prices from. Load its transactions first."
                )

            history = yfinance.Ticker(name).history(
                start=first_date, interval="1d", auto_adjust=False, actions=True
            )
            if history.empty:
                raise ValueError(f"Yahoo Finance returned no prices for {name}")

            # Yahoo serves split-adjusted closes; multiply each close back up
            # by the ratios of all splits dated after it to recover the price
            # as it traded that day.
            ratios = history["Stock Splits"].replace(0.0, 1.0)
            unadjust = ratios.iloc[::-1].cumprod().iloc[::-1].shift(-1, fill_value=1.0)
            prices = (history["Close"] * unadjust).dropna()

            rows = zip(
                [asset_id] * len(prices),
                prices.index.strftime("%Y-%m-%d").tolist(),
                prices.tolist(),
            )
            with self._conn:
                self._conn.executemany(
                    """
                    INSERT INTO prices (asset_id, price_date, price)
                    VALUES (?, ?, ?)
                    ON CONFLICT (asset_id, price_date) DO UPDATE SET
                        price = excluded.price
                    ;""",
                    rows,
                )

    def get_asset_prices(
        self,
        dates: Sequence[datetime.date | str],
        assets: str | Sequence[str],
        *,
        fill_missing_with_stale: bool = True,
    ) -> pd.DataFrame:
        """Return a grid of prices: one row per requested date, one column per
        requested asset.

        The base currency (asset 1) is always exactly ``1.0``. Dates with no
        stored quote (weekends, holidays) carry the last known price forward;
        dates before an asset's first stored price are ``NaN`` regardless.

        Args:
            dates: Dates to price, as ``datetime.date`` objects or
                ``"YYYY-MM-DD"`` strings.
            assets: Asset name(s) for the columns. A single string works.
                Names must be registered in the vault (matched
                case-insensitively).
            fill_missing_with_stale: If ``True`` (default), a date with no
                stored quote gets the most recent stored price before it. If
                ``False``, only exact-date quotes are returned and everything
                else is ``NaN``.

        Returns:
            A pandas ``DataFrame`` indexed by the requested dates, with one
            ``float`` column per requested asset, named as you gave them, in
            the same order. Unknown prices are ``NaN``.

        Raises:
            ValueError: An asset name is not registered in the vault.

        Examples:
            ```python
            v.get_asset_prices(["2026-07-03", "2026-07-04"], ["USD", "NVDA"])
            #                USD    NVDA
            # date
            # 2026-07-03    1.0  159.34
            # 2026-07-04    1.0  159.34   # market closed: carried forward
            ```
        """
        if isinstance(assets, str):
            assets = [assets]
        assets_df = self.list_assets()
        asset_ids = _ids_from_names(
            assets, assets_df["asset_name"], assets_df["asset_id"], "assets"
        )

        req_dates = pd.to_datetime(list(dates))
        unique_ids = list(dict.fromkeys(asset_ids))
        stored = self._read_df(
            f"""
            SELECT asset_id, price_date, price
            FROM prices
            WHERE asset_id IN ({",".join("?" * len(unique_ids))})
              AND price_date <= ?
            ;""",
            (*unique_ids, req_dates.max().strftime("%Y-%m-%d")),
        )

        wide = stored.pivot(index="price_date", columns="asset_id", values="price")
        wide.index = pd.to_datetime(wide.index)
        if fill_missing_with_stale:
            # Weave the requested dates in between the stored ones, so each
            # inherits the last stored price at or before it.
            wide = wide.reindex(wide.index.union(req_dates)).ffill()

        out = wide.reindex(index=req_dates, columns=asset_ids).astype(float)
        out.columns = list(assets)
        out.index.name = "date"
        out.iloc[:, [j for j, i in enumerate(asset_ids) if i == 1]] = 1.0
        return out

    def accumulate_mv(self, group_by: str | None = None) -> pd.DataFrame:
        """Compute the market value of every position, day by day.

        For every day from your first transaction through today: the units
        held that day (accumulated from the ledger, weighted by ownership
        share) times that day's price, in the base currency. Days without a
        stored quote use the last known price, so the series runs through
        weekends and up to the present.

        Total net worth is the row sum: ``v.accumulate_mv().sum(axis=1)``.

        Args:
            group_by: How to bucket the columns:

                - ``None`` (default) — one column per account/asset pair,
                  named ``"account::asset"``
                - ``"account_name"`` — one column per account
                - ``"asset"`` — one column per asset
                - ``"account_group_name"`` — one column per account group

        Returns:
            A pandas ``DataFrame`` with one row per calendar day (date
            index) and ``float`` market values in the base currency. A
            position you don't hold is exactly ``0.0``. A cell is ``NaN``
            when the asset *was* held that day but has no stored price at
            all — run
            [`populate_yfinance_prices`][goblinvest_core.Vault.populate_yfinance_prices]
            for it. (Inside a ``group_by`` bucket, such unpriced holdings
            count as 0.) Empty on an empty vault.

        Raises:
            ValueError: ``group_by`` is not one of the values above.

        Examples:
            ```python
            v.accumulate_mv()
            #             checking::USD  brokerage::USD  brokerage::NVDA
            # date
            # 2026-07-01        1000.00         -240.00           240.00
            # 2026-07-02        1000.00         -240.00           250.00

            v.accumulate_mv(group_by="account_group_name")
            #                cash  investments
            # date
            # 2026-07-01  1000.00         0.00
            # 2026-07-02  1000.00        10.00
            ```
        """
        valid = (None, "account_name", "asset", "account_group_name")
        if group_by not in valid:
            raise ValueError(f"group_by must be one of {valid}, got {group_by!r}")

        ledger = self.list_transactions()
        if ledger.empty:
            return pd.DataFrame(index=pd.DatetimeIndex([], name="date"))

        today = pd.Timestamp.today().normalize()
        all_dates = pd.date_range(ledger["date"].min(), max(today, ledger["date"].max()))

        # Daily grid of units held, one column per (account, asset). Rounding
        # kills float dust so a closed position is exactly 0, while preserving
        # any real fractional share count.
        units = (
            ledger.assign(_weighted=ledger["amount"] * ledger["ownership_share"])
            .pivot_table(
                index="date", columns=["account_name", "asset"], values="_weighted", aggfunc="sum"
            )
            .reindex(all_dates)
            .fillna(0.0)
            .cumsum()
            .round(8)
        )

        prices = self.get_asset_prices(all_dates, list(units.columns.get_level_values("asset")))
        prices.columns = units.columns
        # Zero units are worth exactly 0 even when the price is unknown; NaN
        # is reserved for "held but never priced".
        mv = (units * prices).where(units != 0, 0.0).round(2)

        if group_by is None:
            mv.columns = [f"{account}::{asset}" for account, asset in mv.columns]
        else:
            if group_by == "account_name":
                keys = list(mv.columns.get_level_values("account_name"))
            elif group_by == "asset":
                keys = list(mv.columns.get_level_values("asset"))
            else:
                accounts_df = self.list_accounts()
                group_of = dict(
                    zip(
                        accounts_df["account_name"],
                        accounts_df["account_group_name"].fillna("UNCLASSIFIED"),
                    )
                )
                keys = [group_of[a] for a in mv.columns.get_level_values("account_name")]
            mv = mv.T.groupby(keys, sort=False).sum().T

        mv.index.name = "date"
        mv.columns.name = None
        return mv

    def summarize_accounts(self) -> pd.DataFrame:
        """Summarize what you hold right now: one row per account/asset pair
        with a non-zero balance, valued at the latest known price.

        Positions of less than 0.01 units (closed positions, rounding dust)
        are dropped.

        Returns:
            A pandas ``DataFrame`` sorted by account then asset, with columns:

            - ``account_name``, ``account_group_name`` — as registered
            - ``asset`` — what is held
            - ``units`` — how much of it: shares for a ticker, dollars for cash
            - ``price`` — latest known price: ``1.0`` for the base currency, ``NaN`` if never priced
            - ``price_date`` — date of that price (``NaT`` for the base currency), exposing stale quotes
            - ``ownership_share`` — your fraction of the account
            - ``market_value`` — units × price × ownership share
            - ``last_transaction`` — date of the account/asset's newest transaction

            Empty on an empty vault.

        Examples:
            ```python
            v.summarize_accounts()
            #   account_name account_group_name asset    units  price price_date  ownership_share  market_value last_transaction
            # 0    brokerage        investments  NVDA      2.0  125.0 2026-07-10              1.0         250.0       2026-07-02
            # 1    brokerage        investments   USD   -240.0    1.0        NaT              1.0        -240.0       2026-07-02
            # 2     checking               cash   USD  1000.00    1.0        NaT              1.0        1000.0       2026-07-01
            ```
        """
        columns = [
            "account_name",
            "account_group_name",
            "asset",
            "units",
            "price",
            "price_date",
            "ownership_share",
            "market_value",
            "last_transaction",
        ]
        ledger = self.list_transactions()
        held = (
            ledger.groupby(
                ["account_name", "account_group_name", "asset"], dropna=False, sort=False
            )
            .agg(
                units=("amount", "sum"),
                ownership_share=("ownership_share", "first"),
                last_transaction=("date", "max"),
            )
            .reset_index()
        )
        held["units"] = held["units"].round(8)
        held = held[held["units"].abs() >= 0.01]
        if held.empty:
            return pd.DataFrame(columns=columns)

        today = pd.Timestamp.today().normalize()
        held["price"] = self.get_asset_prices([today], held["asset"].tolist()).iloc[0].to_numpy()

        assets_df = self.list_assets()
        asset_ids = held["asset"].map(dict(zip(assets_df["asset_name"], assets_df["asset_id"])))
        latest = self._read_df(
            "SELECT asset_id, MAX(price_date) AS price_date FROM prices GROUP BY asset_id;"
        )
        held["price_date"] = pd.to_datetime(
            asset_ids.map(dict(zip(latest["asset_id"], latest["price_date"])))
        )
        held.loc[asset_ids == 1, "price_date"] = pd.NaT

        held["market_value"] = (held["units"] * held["price"] * held["ownership_share"]).round(2)
        return held.sort_values(["account_name", "asset"]).reset_index(drop=True)[columns]

    def summarize_vault(self) -> dict[str, object]:
        """Return how much the vault holds and the dates its ledger spans.

        Returns:
            A dict with keys:

            - ``n_transactions`` — rows in the ledger
            - ``n_accounts`` — registered accounts, including any with no transactions
            - ``n_assets`` — registered assets, the base currency included
            - ``n_categories`` — defined categories, as ``list_categories()`` reports them
            - ``n_uncategorized`` — transactions still ``"unclassified"``
            - ``first_transaction`` — date of the earliest transaction
            - ``last_transaction`` — date of the most recent transaction

            On an empty vault the counts are zero and both dates are ``NaT``.

        Examples:
            ```python
            v.summarize_vault()
            # {'n_transactions': 1215,
            #  'n_accounts': 6,
            #  'n_assets': 9,
            #  'n_categories': 12,
            #  'n_uncategorized': 84,
            #  'first_transaction': Timestamp('2016-01-04 00:00:00'),
            #  'last_transaction': Timestamp('2026-08-18 00:00:00')}
            ```
        """
        row = self._read_df(
            """
            SELECT (SELECT COUNT(*) FROM transactions) AS n_transactions,
                   (SELECT COUNT(*) FROM accounts) AS n_accounts,
                   (SELECT COUNT(*) FROM assets) AS n_assets,
                   (SELECT COUNT(*) FROM categories) AS n_categories,
                   (SELECT COUNT(*) FROM transactions WHERE category_id IS NULL)
                       AS n_uncategorized,
                   (SELECT MIN(trans_date) FROM transactions) AS first_transaction,
                   (SELECT MAX(trans_date) FROM transactions) AS last_transaction
            ;"""
        ).iloc[0]
        # int() because sqlite counts arrive as numpy integers.
        summary: dict[str, object] = {
            name: int(row[name]) for name in row.index if name.startswith("n_")
        }
        summary["first_transaction"] = pd.to_datetime(row["first_transaction"])
        summary["last_transaction"] = pd.to_datetime(row["last_transaction"])
        return summary
