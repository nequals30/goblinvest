import datetime
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


def _answers(monkeypatch, *replies):
    """Make the delete confirmation 'type' these replies, in order. Returns the
    list the questions land in, so a test can check what was asked."""
    it = iter(replies)
    asked = []

    def prompt(question=""):
        asked.append(question)
        return next(it)

    monkeypatch.setattr("builtins.input", prompt)
    return asked


def _no_confirmation(monkeypatch):
    """Make any delete confirmation a test failure."""

    def boom(question=""):
        raise AssertionError(f"asked for confirmation when it should not have: {question}")

    monkeypatch.setattr("builtins.input", boom)


def test_create_builds_julia_compatible_schema(filepath):
    v = Vault.create(filepath)
    v.close()

    # Read the file back with plain stdlib sqlite3, as the Julia package would.
    conn = sqlite3.connect(filepath)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"transactions", "accounts", "assets", "prices"} <= tables
    assert conn.execute("SELECT asset_id, asset_name FROM assets").fetchall() == [(1, "USD")]
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


class TestDeleting:
    @pytest.fixture
    def vault(self, filepath):
        with Vault.create(filepath) as v:
            v.add_account("checking", account_group_name="cash")
            v.add_account("brokerage", account_group_name="investments")
            v.add_asset("VTI")
            v.add_transactions(
                "checking", ["2026-07-01", "2026-07-02"], ["rent", "coffee"], [-1200.0, -4.5]
            )
            v.add_transactions(
                "brokerage",
                ["2026-07-02", "2026-07-02"],
                ["buy VTI", "buy VTI"],
                [-1000.0, 3.2],
                assets=["USD", "VTI"],
            )
            with v._conn:
                v._conn.executemany(
                    "INSERT INTO prices (asset_id, price_date, price) VALUES (2, ?, ?);",
                    [("2026-07-02", 312.5), ("2026-07-03", 314.0)],
                )
            yield v

    def _descriptions(self, vault):
        return vault.list_transactions()["description"].tolist()

    def test_delete_account_takes_its_transactions_with_it(self, vault, monkeypatch):
        _answers(monkeypatch, "y")
        vault.delete_account("checking")

        assert vault.list_accounts()["account_name"].tolist() == ["brokerage"]
        assert self._descriptions(vault) == ["buy VTI", "buy VTI"]

    def test_delete_account_question_names_what_goes(self, vault, monkeypatch):
        asked = _answers(monkeypatch, "y")
        vault.delete_account("checking")

        assert 'account "checking"' in asked[0]
        assert "2 transactions" in asked[0]

    def test_declining_deletes_nothing(self, vault, monkeypatch):
        _answers(monkeypatch, "n")
        vault.delete_account("checking")

        assert vault.list_accounts()["account_name"].tolist() == ["checking", "brokerage"]
        assert len(vault.list_transactions()) == 4

    @pytest.mark.parametrize("reply", ["", "no", "yes please", "Ynot", " ", "q"])
    def test_only_a_plain_yes_deletes(self, vault, monkeypatch, reply):
        _answers(monkeypatch, reply)
        vault.delete_account("checking")

        assert len(vault.list_accounts()) == 2

    @pytest.mark.parametrize("reply", ["y", "Y", "yes", " YES "])
    def test_the_ways_of_saying_yes(self, vault, monkeypatch, reply):
        _answers(monkeypatch, reply)
        vault.delete_account("checking")

        assert len(vault.list_accounts()) == 1

    def test_confirm_false_never_asks(self, vault, monkeypatch):
        _no_confirmation(monkeypatch)
        vault.delete_account("checking", confirm=False)

        assert vault.list_accounts()["account_name"].tolist() == ["brokerage"]

    def test_confirm_is_keyword_only(self, vault):
        with pytest.raises(TypeError):
            vault.delete_account("checking", False)

    def test_account_names_match_case_insensitively(self, vault, monkeypatch):
        _no_confirmation(monkeypatch)
        vault.delete_account("CHECKING", confirm=False)

        assert vault.list_accounts()["account_name"].tolist() == ["brokerage"]

    def test_unregistered_account_raises(self, vault, monkeypatch):
        _no_confirmation(monkeypatch)
        with pytest.raises(ValueError, match="not registered"):
            vault.delete_account("savings", confirm=False)

    def test_delete_asset_takes_transactions_and_prices(self, vault, monkeypatch):
        _answers(monkeypatch, "y")
        vault.delete_asset("VTI")

        assert vault.list_assets()["asset_name"].tolist() == ["USD"]
        # the dollar leg of the brokerage purchase is untouched
        assert self._descriptions(vault) == ["rent", "buy VTI", "coffee"]
        assert vault._conn.execute("SELECT COUNT(*) FROM prices;").fetchone()[0] == 0

    def test_delete_asset_question_names_what_goes(self, vault, monkeypatch):
        asked = _answers(monkeypatch, "y")
        vault.delete_asset("VTI")

        assert 'asset "VTI"' in asked[0]
        assert "1 transaction," in asked[0]
        assert "2 stored prices" in asked[0]

    def test_declining_an_asset_deletes_nothing(self, vault, monkeypatch):
        _answers(monkeypatch, "n")
        vault.delete_asset("VTI")

        assert vault.list_assets()["asset_name"].tolist() == ["USD", "VTI"]
        assert len(vault.list_transactions()) == 4
        assert vault._conn.execute("SELECT COUNT(*) FROM prices;").fetchone()[0] == 2

    def test_base_currency_cannot_be_deleted(self, vault, monkeypatch):
        _no_confirmation(monkeypatch)
        with pytest.raises(ValueError, match="base currency"):
            vault.delete_asset("USD", confirm=False)

        assert len(vault.list_transactions()) == 4

    def test_unregistered_asset_raises(self, vault, monkeypatch):
        _no_confirmation(monkeypatch)
        with pytest.raises(ValueError, match="not registered"):
            vault.delete_asset("NVDA", confirm=False)

    def test_deleting_is_survived_by_a_reload(self, vault, monkeypatch):
        """The vault is disposable: loading the statements again brings it back."""
        _no_confirmation(monkeypatch)
        vault.delete_account("checking", confirm=False)

        vault.add_account("checking", account_group_name="cash")
        vault.add_transactions(
            "checking", ["2026-07-01", "2026-07-02"], ["rent", "coffee"], [-1200.0, -4.5]
        )
        assert len(vault.list_transactions()) == 4


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
            "category",
            "ownership_share",
            "account_group_name",
        ]
        assert df["category"].tolist() == ["unclassified"] * 3
        # sorted by date, then account_name
        assert (
            df["date"].tolist()
            == pd.to_datetime(["2026-07-01", "2026-07-01", "2026-07-03"]).tolist()
        )
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


class TestListTransactionsDateRange:
    @pytest.fixture
    def vault(self, filepath):
        with Vault.create(filepath) as v:
            v.add_account("checking")
            v.add_transactions(
                "checking",
                ["2026-07-01", "2026-07-15", "2026-07-31", "2026-08-01"],
                ["a", "b", "c", "d"],
                [1.0, 2.0, 3.0, 4.0],
            )
            yield v

    def test_start_date_only(self, vault):
        df = vault.list_transactions(start_date="2026-07-15")
        assert df["description"].tolist() == ["b", "c", "d"]

    def test_end_date_only(self, vault):
        df = vault.list_transactions(end_date="2026-07-15")
        assert df["description"].tolist() == ["a", "b"]

    def test_both_bounds_are_inclusive(self, vault):
        df = vault.list_transactions(start_date="2026-07-01", end_date="2026-07-31")
        assert df["description"].tolist() == ["a", "b", "c"]

    def test_date_objects_work_too(self, vault):
        df = vault.list_transactions(
            start_date=datetime.date(2026, 7, 15),
            end_date=datetime.date(2026, 7, 31),
        )
        assert df["description"].tolist() == ["b", "c"]

    def test_no_bounds_returns_everything(self, vault):
        assert len(vault.list_transactions()) == 4

    def test_range_matching_nothing_returns_empty_frame(self, vault):
        df = vault.list_transactions(start_date="2027-01-01", end_date="2027-12-31")
        assert len(df) == 0
        assert list(df.columns) == list(vault.list_transactions().columns)

    def test_backwards_range_returns_empty_frame(self, vault):
        df = vault.list_transactions(start_date="2026-07-31", end_date="2026-07-01")
        assert len(df) == 0

    def test_range_is_keyword_only(self, vault):
        with pytest.raises(TypeError):
            vault.list_transactions("2026-07-01")

    def test_other_columns_survive_filtering(self, vault):
        df = vault.list_transactions(start_date="2026-08-01")
        assert df["date"].tolist() == [pd.Timestamp("2026-08-01")]
        assert df["asset"].tolist() == ["USD"]
        assert df["category"].tolist() == ["unclassified"]


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


class TestAnalytics:
    @pytest.fixture
    def vault(self, filepath):
        with Vault.create(filepath) as v:
            v.add_account("checking", account_group_name="cash")
            v.add_account("brokerage", account_group_name="investments")
            v.add_asset("NVDA")
            v.add_transactions(
                ["checking", "brokerage", "brokerage"],
                ["2024-06-03", "2024-06-05", "2024-06-05"],
                ["paycheck", "buy NVDA", "buy NVDA"],
                [1000.0, -240.0, 2.0],
                assets=["USD", "USD", "NVDA"],
            )
            with v._conn:  # NVDA is asset 2
                v._conn.executemany(
                    "INSERT INTO prices (asset_id, price_date, price) VALUES (?, ?, ?)",
                    [(2, "2024-06-05", 120.0), (2, "2024-06-07", 125.0)],
                )
            yield v

    # ---- accumulate_mv ----------------------------------------------------

    def test_ungrouped_daily_market_values(self, vault):
        out = vault.accumulate_mv()

        assert set(out.columns) == {"checking::USD", "brokerage::USD", "brokerage::NVDA"}
        assert out.index[0] == pd.Timestamp("2024-06-03")
        assert out.index[-1] == pd.Timestamp.today().normalize()  # runs to today

        assert out.loc["2024-06-03", "checking::USD"] == 1000.0
        assert out.loc["2024-06-04", "checking::USD"] == 1000.0  # carried between transactions
        assert out.loc["2024-06-04", "brokerage::NVDA"] == 0.0  # nothing held yet
        assert out.loc["2024-06-05", "brokerage::USD"] == -240.0
        assert out.loc["2024-06-05", "brokerage::NVDA"] == 240.0  # 2 shares x 120
        assert out.loc["2024-06-07", "brokerage::NVDA"] == 250.0  # 2 shares x 125
        assert out.loc["2024-06-08", "brokerage::NVDA"] == 250.0  # stale price on Saturday
        assert out.iloc[-1]["brokerage::NVDA"] == 250.0  # last known price through today

    def test_group_by_account_name(self, vault):
        out = vault.accumulate_mv(group_by="account_name")
        assert set(out.columns) == {"checking", "brokerage"}
        assert out.loc["2024-06-05", "brokerage"] == 0.0  # -240 cash + 240 shares
        assert out.loc["2024-06-07", "brokerage"] == 10.0

    def test_group_by_asset(self, vault):
        out = vault.accumulate_mv(group_by="asset")
        assert set(out.columns) == {"USD", "NVDA"}
        assert out.loc["2024-06-05", "USD"] == 760.0
        assert out.loc["2024-06-05", "NVDA"] == 240.0

    def test_group_by_account_group_name(self, vault):
        out = vault.accumulate_mv(group_by="account_group_name")
        assert set(out.columns) == {"cash", "investments"}
        assert out.loc["2024-06-07", "cash"] == 1000.0
        assert out.loc["2024-06-07", "investments"] == 10.0

    def test_invalid_group_by_raises(self, vault):
        with pytest.raises(ValueError, match="group_by"):
            vault.accumulate_mv(group_by="asset_class")

    def test_ownership_share_weights_values(self, vault):
        vault.add_account("joint", ownership_share=0.5, account_group_name="cash")
        vault.add_transactions("joint", ["2024-06-03"], ["deposit"], [1000.0])

        out = vault.accumulate_mv()
        assert out.loc["2024-06-03", "joint::USD"] == 500.0

    def test_closed_position_is_exactly_zero(self, vault):
        # 0.3 - 0.1 - 0.2 leaves float dust of ~1e-17; the vault must report 0.
        vault.add_transactions(
            "checking",
            ["2024-06-10"] * 3,
            ["a", "b", "c"],
            [0.3, -0.1, -0.2],
        )
        out = vault.accumulate_mv()
        assert out.loc["2024-06-11", "checking::USD"] == 1000.0

    def test_held_but_never_priced_asset_is_nan(self, vault):
        vault.add_asset("BTC")
        vault.add_transactions("brokerage", ["2024-06-05"], ["buy BTC"], [1.0], assets="BTC")

        out = vault.accumulate_mv()
        assert pd.isna(out.loc["2024-06-05", "brokerage::BTC"])
        assert out.loc["2024-06-04", "brokerage::BTC"] == 0.0  # not held yet: 0, not NaN

    def test_accumulate_mv_empty_vault(self, filepath):
        with Vault.create(filepath) as v:
            out = v.accumulate_mv()
            assert len(out) == 0

    # ---- summarize_accounts -----------------------------------------------

    def test_summary_rows_and_columns(self, vault):
        out = vault.summarize_accounts()

        assert list(out.columns) == [
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
        # sorted by account then asset
        assert out["account_name"].tolist() == ["brokerage", "brokerage", "checking"]
        assert out["asset"].tolist() == ["NVDA", "USD", "USD"]
        assert out["units"].tolist() == [2.0, -240.0, 1000.0]
        assert out["price"].tolist() == [125.0, 1.0, 1.0]  # NVDA at its latest stored price
        assert out["market_value"].tolist() == [250.0, -240.0, 1000.0]
        assert (
            out["last_transaction"].tolist()
            == pd.to_datetime(["2024-06-05", "2024-06-05", "2024-06-03"]).tolist()
        )

    def test_summary_price_date_shows_staleness(self, vault):
        out = vault.summarize_accounts().set_index("asset")
        assert out.loc["NVDA", "price_date"] == pd.Timestamp("2024-06-07")
        assert pd.isna(out.loc[["USD"], "price_date"]).all()  # base currency has no quote date

    def test_summary_drops_dust_positions(self, vault):
        vault.add_account("dusty")
        vault.add_transactions("dusty", ["2024-06-10"], ["leftover"], [0.005])

        assert "dusty" not in vault.summarize_accounts()["account_name"].tolist()

    def test_summary_empty_vault(self, filepath):
        with Vault.create(filepath) as v:
            out = v.summarize_accounts()
            assert len(out) == 0
            assert "market_value" in out.columns

    def test_summary_matches_last_row_of_accumulate_mv(self, vault):
        vault.add_account("joint", ownership_share=0.5, account_group_name="cash")
        vault.add_transactions("joint", ["2024-06-03"], ["deposit"], [1000.0])

        latest_mv = vault.accumulate_mv().iloc[-1]
        for row in vault.summarize_accounts().itertuples():
            assert latest_mv[f"{row.account_name}::{row.asset}"] == pytest.approx(row.market_value)


class TestSummarizeVault:
    @pytest.fixture
    def vault(self, tmp_path):
        with Vault.create(tmp_path / "Vault.db", adjustments_dir=tmp_path / "adjustments") as v:
            v.add_category(["groceries", "income"])
            v.add_account("checking", account_group_name="cash")
            v.add_account("brokerage", account_group_name="investments")
            v.add_asset("NVDA")
            v.add_transactions(
                ["checking", "checking", "brokerage", "brokerage"],
                ["2024-06-03", "2024-06-04", "2024-06-05", "2024-06-05"],
                ["paycheck", "WHOLEFDS #123", "buy NVDA", "buy NVDA"],
                [1000.0, -40.0, -240.0, 2.0],
                assets=["USD", "USD", "USD", "NVDA"],
            )
            v.apply_categories()
            yield v

    def test_keys_and_their_order(self, vault):
        assert list(vault.summarize_vault()) == [
            "n_transactions",
            "n_accounts",
            "n_assets",
            "n_categories",
            "n_uncategorized",
            "first_transaction",
            "last_transaction",
        ]

    def test_counts(self, vault):
        summary = vault.summarize_vault()
        assert summary["n_transactions"] == 4
        assert summary["n_accounts"] == 2
        assert summary["n_assets"] == 2  # USD and NVDA
        assert summary["n_categories"] == 2  # defined, none assigned to anything yet
        assert summary["n_uncategorized"] == 4

    def test_counts_are_plain_ints(self, vault):
        # numpy integers would not survive json.dumps.
        counts = [v for k, v in vault.summarize_vault().items() if k.startswith("n_")]
        assert all(type(count) is int for count in counts)

    def test_category_count_matches_list_categories(self, vault):
        assert vault.summarize_vault()["n_categories"] == len(vault.list_categories())

    def test_date_span(self, vault):
        summary = vault.summarize_vault()
        assert summary["first_transaction"] == pd.Timestamp("2024-06-03")
        assert summary["last_transaction"] == pd.Timestamp("2024-06-05")

    def test_accounts_without_transactions_still_count(self, vault):
        vault.add_account("savings")
        assert vault.summarize_vault()["n_accounts"] == 3

    def test_categorizing_shrinks_the_uncategorized_count(self, vault):
        vault.set_category_rule("WHOLEFDS #123", "groceries")

        summary = vault.summarize_vault()
        assert summary["n_uncategorized"] == 3
        assert summary["n_categories"] == 2  # unchanged: it counts definitions, not use

    def test_empty_vault(self, filepath):
        with Vault.create(filepath) as v:
            summary = v.summarize_vault()
            assert summary["n_transactions"] == 0
            assert summary["n_accounts"] == 0
            assert summary["n_assets"] == 1  # the base currency is there from the start
            assert summary["n_categories"] == 0
            assert summary["n_uncategorized"] == 0
            assert summary["first_transaction"] is pd.NaT
            assert summary["last_transaction"] is pd.NaT


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
