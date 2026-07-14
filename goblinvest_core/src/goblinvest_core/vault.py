"""SQLite-backed vault of accounts, assets, transactions, and prices."""

import datetime
import sqlite3
from collections.abc import Sequence
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

        Everything is a transaction: a $40 grocery charge is one row
        (``-40.00`` of the base currency). A brokerage purchase is two rows on
        the same date: the money leaving (``-1000.00``, asset ``"USD"``) and
        the shares arriving (``+3.2``, asset ``"VTI"``). A transfer between two
        of your accounts is two ordinary rows, one per account.

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

    def list_transactions(self) -> pd.DataFrame:
        """Return the whole ledger, with account and asset details joined in.

        Returns:
            A pandas ``DataFrame`` with one row per transaction, sorted by
            ``date`` then ``account_name``, with columns:

            - ``transaction_id`` — unique id of the transaction
            - ``account_name`` — account the transaction belongs to
            - ``date`` — transaction date (pandas datetime)
            - ``description`` — free-form description
            - ``amount`` — signed amount, in the transaction's asset
            - ``asset`` — name of the asset the amount is denominated in
            - ``ownership_share`` — your fraction of the account
            - ``account_group_name`` — the account's group label

        Examples:
            ```python
            v.list_transactions()
            #    transaction_id account_name       date    description   amount asset  ownership_share account_group_name
            # 0               1     checking 2026-07-01  WHOLEFDS #123   -40.00   USD              1.0               cash
            # 1               2    brokerage 2026-07-02        buy VTI -1000.00   USD              1.0        investments
            # 2               3    brokerage 2026-07-02        buy VTI     3.20   VTI              1.0        investments
            ```
        """
        df = self._read_df(
            """
            SELECT trans_id, account_name, trans_date, trans_desc, amount,
                   asset_name, ownership_share, account_group_name
            FROM transactions
            LEFT JOIN accounts ON accounts.account_id = transactions.account_id
            LEFT JOIN assets ON assets.asset_id = transactions.asset_id
            ORDER BY trans_date, account_name
            ;"""
        )
        df.columns = [
            "transaction_id",
            "account_name",
            "date",
            "description",
            "amount",
            "asset",
            "ownership_share",
            "account_group_name",
        ]
        df["date"] = pd.to_datetime(df["date"])
        return df

    def populate_yfinance_prices(self, assets: str | Sequence[str]) -> None:
        """Fetch daily prices from Yahoo Finance and store them in the vault.

        For each asset named, daily closing prices are fetched from the date of
        that asset's first transaction through today and stored in the vault.
        Re-runnable like everything else: refreshing just fills in the days
        since the last run. Requires internet access.

        Stored prices match what your brokerage statement said *at the time*,
        which means two deliberate departures from what Yahoo displays:

        - **Splits are un-adjusted.** Yahoo rewrites history after a stock
          split (after a 10-for-1 split, a pre-split $1,200 close is served as
          $120). Those rewrites are undone, so shares held × stored price on
          any date matches the statement from that date.
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
        """Compute market value over time — what everything was worth, day by day.

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
            - ``price`` — latest known price; always ``1.0`` for the base
              currency, ``NaN`` if the asset has never been priced
            - ``price_date`` — the date that price is from, so a stale quote
              is visible (``NaT`` for the base currency)
            - ``ownership_share`` — your fraction of the account
            - ``market_value`` — units × price × ownership share
            - ``last_transaction`` — date of the account/asset's newest
              transaction

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
            ledger.groupby(["account_name", "account_group_name", "asset"], dropna=False, sort=False)
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
