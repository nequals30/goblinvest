"""SQLite-backed vault of accounts, assets, transactions, and prices."""

import sqlite3
from pathlib import Path

import pandas as pd

from goblinvest_core._password import _get_password, forget_password

# The first 16 bytes of every unencrypted SQLite file; anything else is
# assumed to be a SQLCipher-encrypted vault.
_SQLITE_MAGIC = b"SQLite format 3\x00"

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS transactions (
        trans_id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
        account_id INTEGER NOT NULL,
        trans_date DATE NOT NULL,
        trans_desc TEXT NOT NULL,
        amount DECIMAL(7,5) NOT NULL,
        asset_id INTEGER NOT NULL,
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
)


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

    def __init__(self, conn):
        self._conn = conn

    @classmethod
    def create(
        cls,
        filepath: str | Path,
        *,
        default_asset: str = "USD",
        encrypted: bool = False,
        overwrite: bool = False,
    ) -> "Vault":
        """Create a new vault database file and return a handle to it.

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
            overwrite: If ``True``, delete any existing file at ``filepath`` and
                start fresh. If ``False``, an existing file is an error.

        Returns:
            An open `Vault`.

        Raises:
            FileNotFoundError: The parent directory does not exist.
            FileExistsError: A file already exists at ``filepath`` and
                ``overwrite`` is ``False``.

        Examples:
            ```python
            v = Vault.create("~/finance/MyVault.db")
            v = Vault.create("/tmp/rebuild.db", overwrite=True)   # rebuild-from-scratch scripts
            v = Vault.create("~/secret.db", encrypted=True)       # prompts for a password
            ```
        """
        filepath = Path(filepath).expanduser()
        if not filepath.parent.is_dir():
            raise FileNotFoundError(f"No such directory: {filepath.parent}")
        if filepath.exists() and not overwrite:
            raise FileExistsError(
                f"A file already exists at {filepath} (pass overwrite=True to replace it)"
            )
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
        return cls(conn)

    @classmethod
    def open(cls, filepath: str | Path) -> "Vault":
        """Open an existing vault file and return a handle to it.

        Whether the file is encrypted is detected automatically. For an
        encrypted vault, the password is taken from
        [`ask_password`][goblinvest_core.ask_password] if one was entered in
        the last 15 minutes; otherwise you are prompted at the terminal — a
        password is never passed in code. An unencrypted vault opens without
        any prompt.

        Args:
            filepath: Path of an existing vault file. ``~`` is expanded.

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
            ```
        """
        filepath = Path(filepath).expanduser()
        if not filepath.is_file():
            raise FileNotFoundError(f"No vault exists at {filepath}")
        with filepath.open("rb") as f:
            encrypted = f.read(16) != _SQLITE_MAGIC
        password = _get_password(confirm=False) if encrypted else None
        try:
            return cls(_connect(filepath, password))
        except ValueError:
            if encrypted:
                forget_password()
            raise

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
