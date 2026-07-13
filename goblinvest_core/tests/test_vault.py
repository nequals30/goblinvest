import sqlite3
import time

import pandas as pd
import pytest

from goblinvest_core import Vault, ask_password, forget_password
from goblinvest_core import _password


@pytest.fixture
def filepath(tmp_path):
    return tmp_path / "PersonalFinanceVault.db"


@pytest.fixture(autouse=True)
def forget_between_tests():
    forget_password()
    yield
    forget_password()


def _typed(monkeypatch, *entries):
    """Make the hidden password prompt 'type' these entries, in order."""
    it = iter(entries)
    monkeypatch.setattr(_password, "getpass", lambda prompt="": next(it))


def _no_prompt(monkeypatch):
    def fail(prompt=""):
        raise AssertionError("password prompt should not have appeared")

    monkeypatch.setattr(_password, "getpass", fail)


def test_create_builds_julia_compatible_schema(filepath):
    v = Vault.create(filepath)
    v.close()

    # Read the file back with plain stdlib sqlite3, as the Julia package would.
    conn = sqlite3.connect(filepath)
    tables = {
        r[0]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"transactions", "accounts", "assets", "prices"} <= tables
    assert conn.execute("SELECT asset_id, asset_name FROM assets").fetchall() == [
        (1, "USD")
    ]
    conn.close()


def test_create_seeds_custom_default_asset(filepath):
    with Vault.create(filepath, default_asset="EUR"):
        pass
    conn = sqlite3.connect(filepath)
    assert conn.execute("SELECT asset_name FROM assets").fetchall() == [("EUR",)]
    conn.close()


def test_create_refuses_existing_file(filepath):
    Vault.create(filepath).close()
    with pytest.raises(FileExistsError):
        Vault.create(filepath)


def test_create_overwrite_replaces_file(filepath):
    v = Vault.create(filepath)
    v.add_account("checking")
    v.close()

    v = Vault.create(filepath, overwrite=True)
    assert len(v.list_accounts()) == 0
    v.close()


def test_create_missing_parent_dir(tmp_path):
    with pytest.raises(FileNotFoundError):
        Vault.create(tmp_path / "nope" / "Vault.db")


def test_open_missing_file(filepath):
    with pytest.raises(FileNotFoundError):
        Vault.open(filepath)


def test_add_account_and_list(filepath):
    v = Vault.create(filepath)
    v.add_account("checking", account_group_name="cash")
    v.add_account("joint-checking", ownership_share=0.5, account_group_name="cash")

    df = v.list_accounts()
    assert list(df.columns) == [
        "account_id",
        "account_name",
        "ownership_share",
        "account_group_name",
    ]
    assert df["account_name"].tolist() == ["checking", "joint-checking"]
    assert df["ownership_share"].tolist() == [1.0, 0.5]
    assert df["account_group_name"].tolist() == ["cash", "cash"]
    v.close()


def test_add_account_is_idempotent_upsert(filepath):
    v = Vault.create(filepath)
    v.add_account("checking")
    account_id = v.list_accounts()["account_id"].iloc[0]

    v.add_account("checking", ownership_share=0.5, account_group_name="cash")

    df = v.list_accounts()
    assert len(df) == 1
    assert df["account_id"].iloc[0] == account_id  # primary key preserved
    assert df["ownership_share"].iloc[0] == 0.5
    assert df["account_group_name"].iloc[0] == "cash"
    v.close()


def test_reopen_sees_data(filepath):
    v = Vault.create(filepath)
    v.add_account("checking")
    v.close()

    with Vault.open(filepath) as v:
        assert v.list_accounts()["account_name"].tolist() == ["checking"]


class TestAssets:
    def test_add_asset_and_list(self, filepath):
        with Vault.create(filepath) as v:
            v.add_asset("VTI")
            v.add_asset("EUR")

            df = v.list_assets()
            assert list(df.columns) == ["asset_id", "asset_name"]
            assert df["asset_id"].tolist() == [1, 2, 3]
            assert df["asset_name"].tolist() == ["USD", "VTI", "EUR"]

    def test_add_asset_is_idempotent(self, filepath):
        with Vault.create(filepath) as v:
            v.add_asset("VTI")
            v.add_asset("VTI")

            df = v.list_assets()
            assert df["asset_name"].tolist() == ["USD", "VTI"]
            assert df["asset_id"].tolist() == [1, 2]  # primary key preserved


class TestTransactions:
    @pytest.fixture
    def vault(self, filepath):
        with Vault.create(filepath) as v:
            v.add_account("checking", account_group_name="cash")
            v.add_account("brokerage", ownership_share=0.5, account_group_name="investments")
            v.add_asset("VTI")
            yield v

    def test_add_and_list(self, vault):
        vault.add_transactions(
            ["checking", "brokerage", "brokerage"],
            ["2026-07-03", "2026-07-01", "2026-07-01"],
            ["groceries", "buy VTI (cash leg)", "buy VTI (share leg)"],
            [-40.00, -1000.00, 3.2],
            assets=["USD", "USD", "VTI"],
        )

        df = vault.list_transactions()
        assert list(df.columns) == [
            "transaction_id",
            "account_name",
            "date",
            "description",
            "amount",
            "asset",
            "ownership_share",
            "account_group_name",
        ]
        # sorted by date, then account_name
        assert df["date"].tolist() == pd.to_datetime(
            ["2026-07-01", "2026-07-01", "2026-07-03"]
        ).tolist()
        assert df["account_name"].tolist() == ["brokerage", "brokerage", "checking"]
        assert df["amount"].tolist() == [-1000.00, 3.2, -40.00]
        assert df["asset"].tolist() == ["USD", "VTI", "USD"]
        assert df["ownership_share"].tolist() == [0.5, 0.5, 1.0]
        assert df["account_group_name"].tolist() == ["investments", "investments", "cash"]

    def test_single_account_and_asset_broadcast(self, vault):
        vault.add_transactions(
            "checking",
            ["2026-07-01", "2026-07-02"],
            ["a", "b"],
            [1.0, 2.0],
            assets="USD",
        )
        assert vault.list_transactions()["account_name"].tolist() == ["checking"] * 2

    def test_assets_default_to_base_currency(self, vault):
        vault.add_transactions("checking", ["2026-07-01"], ["a"], [1.0])
        assert vault.list_transactions()["asset"].tolist() == ["USD"]

    def test_dates_as_date_objects(self, vault):
        import datetime

        vault.add_transactions("checking", [datetime.date(2026, 7, 1)], ["a"], [1.0])
        assert vault.list_transactions()["date"].tolist() == [pd.Timestamp("2026-07-01")]

    def test_names_match_case_insensitively(self, vault):
        vault.add_transactions("CHECKING", ["2026-07-01"], ["a"], [1.0], assets="vti")
        df = vault.list_transactions()
        assert df["account_name"].tolist() == ["checking"]
        assert df["asset"].tolist() == ["VTI"]

    def test_unregistered_account_raises(self, vault):
        with pytest.raises(ValueError, match="savings"):
            vault.add_transactions("savings", ["2026-07-01"], ["a"], [1.0])

    def test_unregistered_asset_raises(self, vault):
        with pytest.raises(ValueError, match="DOGE"):
            vault.add_transactions("checking", ["2026-07-01"], ["a"], [1.0], assets="DOGE")

    def test_mismatched_lengths_raise(self, vault):
        with pytest.raises(ValueError, match="length"):
            vault.add_transactions("checking", ["2026-07-01"], ["a", "b"], [1.0])

    def test_reload_is_idempotent(self, vault):
        args = (
            "checking",
            ["2026-07-01", "2026-07-01", "2026-07-01"],
            ["coffee", "coffee", "groceries"],
            [-5.0, -5.0, -40.0],
        )
        vault.add_transactions(*args)
        vault.add_transactions(*args)  # rebuilding the world never double-counts

        assert len(vault.list_transactions()) == 3

    def test_identical_rows_in_one_call_get_suffixes(self, vault):
        vault.add_transactions(
            "checking",
            ["2026-07-01"] * 3,
            ["coffee"] * 3,
            [-5.0] * 3,
        )
        assert vault.list_transactions()["description"].tolist() == [
            "coffee",
            "coffee (2)",
            "coffee (3)",
        ]

    def test_empty_vault_lists_no_transactions(self, vault):
        df = vault.list_transactions()
        assert len(df) == 0
        assert "transaction_id" in df.columns


def _yahoo_history(rows):
    """Build a frame shaped like yfinance.Ticker.history() output: tz-aware
    daily index, split-adjusted Close, split ratio on split days else 0."""
    idx = pd.to_datetime([r[0] for r in rows]).tz_localize("America/New_York")
    return pd.DataFrame(
        {"Close": [r[1] for r in rows], "Stock Splits": [r[2] for r in rows]},
        index=idx,
    )


class TestPrices:
    @pytest.fixture
    def vault(self, filepath):
        with Vault.create(filepath) as v:
            v.add_account("brokerage", account_group_name="investments")
            v.add_asset("NVDA")
            v.add_transactions("brokerage", ["2024-06-03"], ["buy NVDA"], [2.0], assets="NVDA")
            yield v

    @pytest.fixture
    def yahoo(self, monkeypatch):
        """Replace yfinance.Ticker with a fake serving frames[symbol] and
        recording (symbol, start) for every fetch."""
        import types

        fake = types.SimpleNamespace(frames={}, calls=[])

        class FakeTicker:
            def __init__(self, symbol):
                self.symbol = symbol

            def history(self, *, start=None, **kwargs):
                fake.calls.append((self.symbol, start))
                return fake.frames[self.symbol]

        monkeypatch.setattr("yfinance.Ticker", FakeTicker)
        return fake

    def _stored_prices(self, vault):
        return {
            date: price
            for date, price in vault._conn.execute(
                "SELECT price_date, price FROM prices ORDER BY price_date"
            )
        }

    def test_populate_unadjusts_splits(self, vault, yahoo):
        # NVDA's 10-for-1 split of 2024-06-10: Yahoo serves the 06-07 close in
        # post-split dollars; the vault must store it as it traded that day.
        yahoo.frames["NVDA"] = _yahoo_history(
            [
                ("2024-06-07", 120.89, 0.0),
                ("2024-06-10", 121.79, 10.0),
                ("2024-06-11", 120.91, 0.0),
            ]
        )
        vault.populate_yfinance_prices("NVDA")

        stored = self._stored_prices(vault)
        assert stored["2024-06-07"] == pytest.approx(1208.9)
        assert stored["2024-06-10"] == pytest.approx(121.79)  # split day: already post-split
        assert stored["2024-06-11"] == pytest.approx(120.91)

    def test_populate_applies_reverse_splits(self, vault, yahoo):
        # 1-for-10 reverse split (ratio 0.1): Yahoo shows pre-split closes
        # multiplied by 10; the as-traded price is a tenth of that.
        yahoo.frames["NVDA"] = _yahoo_history(
            [
                ("2024-01-05", 500.0, 0.0),
                ("2024-01-08", 505.0, 0.1),
                ("2024-01-09", 510.0, 0.0),
            ]
        )
        vault.populate_yfinance_prices("NVDA")

        stored = self._stored_prices(vault)
        assert stored["2024-01-05"] == pytest.approx(50.0)
        assert stored["2024-01-08"] == pytest.approx(505.0)

    def test_populate_fetches_each_asset_from_its_own_first_transaction(self, vault, yahoo):
        vault.add_asset("VTI")
        vault.add_transactions("brokerage", ["2024-01-15"], ["buy VTI"], [5.0], assets="VTI")
        yahoo.frames["NVDA"] = _yahoo_history([("2024-06-07", 120.89, 0.0)])
        yahoo.frames["VTI"] = _yahoo_history([("2024-01-16", 240.0, 0.0)])

        vault.populate_yfinance_prices(["NVDA", "VTI"])

        assert yahoo.calls == [("NVDA", "2024-06-03"), ("VTI", "2024-01-15")]

    def test_populate_is_idempotent_upsert(self, vault, yahoo):
        yahoo.frames["NVDA"] = _yahoo_history([("2024-06-07", 120.89, 0.0)])
        vault.populate_yfinance_prices("NVDA")
        yahoo.frames["NVDA"] = _yahoo_history([("2024-06-07", 121.00, 0.0)])
        vault.populate_yfinance_prices("NVDA")

        assert self._stored_prices(vault) == {"2024-06-07": pytest.approx(121.00)}

    def test_populate_drops_nan_closes(self, vault, yahoo):
        yahoo.frames["NVDA"] = _yahoo_history(
            [("2024-06-06", float("nan"), 0.0), ("2024-06-07", 120.89, 0.0)]
        )
        vault.populate_yfinance_prices("NVDA")

        assert list(self._stored_prices(vault)) == ["2024-06-07"]

    def test_populate_unregistered_asset_raises(self, vault, yahoo):
        with pytest.raises(ValueError, match="DOGE"):
            vault.populate_yfinance_prices("DOGE")

    def test_populate_without_transactions_raises(self, vault, yahoo):
        vault.add_asset("VTI")
        with pytest.raises(ValueError, match="VTI"):
            vault.populate_yfinance_prices("VTI")

    def test_populate_empty_yahoo_response_raises(self, vault, yahoo):
        yahoo.frames["NVDA"] = pd.DataFrame()
        with pytest.raises(ValueError, match="NVDA"):
            vault.populate_yfinance_prices("NVDA")

    # ---- get_asset_prices -------------------------------------------------

    def _seed(self, vault, rows):
        with vault._conn:
            vault._conn.executemany(
                "INSERT INTO prices (asset_id, price_date, price) VALUES (?, ?, ?)",
                rows,
            )

    def test_get_prices_grid(self, vault):
        self._seed(vault, [(2, "2024-06-06", 120.0), (2, "2024-06-07", 121.0)])

        out = vault.get_asset_prices(["2024-06-06", "2024-06-07"], ["USD", "NVDA"])
        assert list(out.columns) == ["USD", "NVDA"]
        assert out.index.tolist() == pd.to_datetime(["2024-06-06", "2024-06-07"]).tolist()
        assert out["USD"].tolist() == [1.0, 1.0]
        assert out["NVDA"].tolist() == [120.0, 121.0]

    def test_stale_fill_uses_stored_prices_not_requested_dates(self, vault):
        self._seed(vault, [(2, "2024-06-07", 121.0)])

        # 2024-06-09 is a Sunday and the only date asked for; the fill must
        # come from the vault's Friday quote, not from another requested date.
        out = vault.get_asset_prices(["2024-06-09"], "NVDA")
        assert out["NVDA"].tolist() == [121.0]

    def test_no_stale_fill_gives_nan(self, vault):
        self._seed(vault, [(2, "2024-06-07", 121.0)])

        out = vault.get_asset_prices(["2024-06-09"], "NVDA", fill_missing_with_stale=False)
        assert out["NVDA"].isna().tolist() == [True]

    def test_dates_before_first_price_are_nan(self, vault):
        self._seed(vault, [(2, "2024-06-07", 121.0)])

        out = vault.get_asset_prices(["2024-06-01"], ["USD", "NVDA"])
        assert out["NVDA"].isna().tolist() == [True]
        assert out["USD"].tolist() == [1.0]  # base currency is always 1

    def test_columns_keep_requested_names_and_order(self, vault):
        self._seed(vault, [(2, "2024-06-07", 121.0)])

        out = vault.get_asset_prices(["2024-06-07"], ["nvda", "USD"])
        assert list(out.columns) == ["nvda", "USD"]
        assert out["nvda"].tolist() == [121.0]

    def test_get_prices_unregistered_asset_raises(self, vault):
        with pytest.raises(ValueError, match="DOGE"):
            vault.get_asset_prices(["2024-06-07"], "DOGE")


class TestEncrypted:
    def test_roundtrip_with_prompts(self, filepath, monkeypatch):
        _typed(monkeypatch, "hunter2", "hunter2")  # create: enter + confirm
        v = Vault.create(filepath, encrypted=True)
        v.add_account("checking")
        v.close()

        forget_password()
        _typed(monkeypatch, "hunter2")  # open: single entry, no confirmation
        with Vault.open(filepath) as v:
            assert v.list_accounts()["account_name"].tolist() == ["checking"]

    def test_remembered_password_skips_prompts(self, filepath, monkeypatch):
        _typed(monkeypatch, "hunter2", "hunter2")
        ask_password()

        _no_prompt(monkeypatch)
        Vault.create(filepath, encrypted=True).close()
        with Vault.open(filepath) as v:
            assert len(v.list_accounts()) == 0

    def test_mismatched_confirmation(self, monkeypatch):
        _typed(monkeypatch, "hunter2", "hunter3")
        with pytest.raises(ValueError):
            ask_password()

    def test_empty_password(self, monkeypatch):
        _typed(monkeypatch, "", "")
        with pytest.raises(ValueError):
            ask_password()

    def test_wrong_password_is_forgotten(self, filepath, monkeypatch):
        _typed(monkeypatch, "hunter2", "hunter2")
        Vault.create(filepath, encrypted=True).close()
        forget_password()

        _typed(monkeypatch, "wrong")
        with pytest.raises(ValueError):
            Vault.open(filepath)

        # The wrong password was not kept, so the next open prompts again.
        _typed(monkeypatch, "hunter2")
        Vault.open(filepath).close()

    def test_expired_password_reprompts(self, filepath, monkeypatch):
        _typed(monkeypatch, "hunter2", "hunter2")
        Vault.create(filepath, encrypted=True).close()

        _password._cache["expires_at"] = time.monotonic() - 1
        _typed(monkeypatch, "hunter2")
        Vault.open(filepath).close()

    def test_plain_vault_never_prompts(self, filepath, monkeypatch):
        Vault.create(filepath).close()
        _no_prompt(monkeypatch)
        Vault.open(filepath).close()

    def test_encrypted_file_not_plain_sqlite(self, filepath, monkeypatch):
        _typed(monkeypatch, "hunter2", "hunter2")
        Vault.create(filepath, encrypted=True).close()
        assert not filepath.read_bytes().startswith(b"SQLite format 3")

    def test_password_with_quotes(self, filepath, monkeypatch):
        pw = "it's a 'quoted' pass"
        _typed(monkeypatch, pw, pw)
        Vault.create(filepath, encrypted=True).close()

        forget_password()
        _typed(monkeypatch, pw)
        with Vault.open(filepath) as v:
            assert len(v.list_accounts()) == 0
