import pandas as pd
import pytest

from goblinvest_core import Vault, _password, encrypt_file, forget_password


@pytest.fixture(autouse=True)
def forget_between_tests():
    forget_password()
    yield
    forget_password()


def _typed(monkeypatch, *entries):
    """Make the hidden password prompt 'type' these entries, in order."""
    it = iter(entries)
    monkeypatch.setattr(_password, "getpass", lambda prompt="": next(it))


@pytest.fixture
def vault(tmp_path):
    """A vault whose ledger straddles a month boundary, which is what an
    effective date is usually there to fix."""
    v = Vault.create(tmp_path / "Vault.db", adjustments_dir=tmp_path / "adjustments")
    v.add_account("checking", account_group_name="cash")
    v.add_account("credit-card", account_group_name="credit")
    v.add_asset("VTI")
    v.add_transactions(
        "credit-card",
        ["2026-02-03", "2026-02-04", "2026-02-10"],
        ["AMAZON.COM", "AMAZON MKTPL", "GAS STATION"],
        [-42.10, -17.99, -30.00],
    )
    v.add_transactions("checking", ["2026-01-31"], ["ACME Payroll"], [3000.00])
    v.add_transactions("checking", ["2026-02-02"], ["buy VTI"], [3.2], assets="VTI")
    yield v
    v.close()


@pytest.fixture
def adjustments(vault):
    return vault.adjustments_dir


def _file(adjustments_dir):
    return adjustments_dir / "effective_dates.csv"


def _write_file(adjustments_dir, *rows):
    _file(adjustments_dir).write_text(
        "account,date,description,amount,asset,effective_date\n" + "".join(f"{r}\n" for r in rows)
    )


def _dates_of(v):
    """description -> (date it counts on, date the statement gave it)."""
    df = v.list_transactions()
    return {
        row.description: (row.date.strftime("%Y-%m-%d"), row.original_date.strftime("%Y-%m-%d"))
        for row in df.itertuples()
    }


def _seed_prices(v, *rows):
    with v._conn:
        v._conn.executemany(
            "INSERT INTO prices (asset_id, price_date, price) VALUES (?, ?, ?);", rows
        )


class TestTheFile:
    def test_create_starts_it_with_the_others(self, adjustments):
        assert (
            _file(adjustments).read_text()
            == "account,date,description,amount,asset,effective_date\n"
        )

    def test_a_folder_from_before_gets_it_on_next_use(self, vault, adjustments):
        _file(adjustments).unlink()
        vault.apply_effective_dates()
        assert _file(adjustments).is_file()

    def test_it_joins_an_encrypted_folder_encrypted(self, tmp_path, monkeypatch):
        _typed(monkeypatch, "hunter2", "hunter2")
        with Vault.create(
            tmp_path / "V.db", adjustments_dir=tmp_path / "adj", encrypted=False
        ) as v:
            for name in ("categories.csv", "category_rules.csv", "category_exceptions.csv"):
                encrypt_file(v.adjustments_dir / name)
            _file(v.adjustments_dir).unlink()
            v.apply_effective_dates()
            assert _file(v.adjustments_dir).read_bytes().startswith(b"GVENC1\n")


class TestListTransactions:
    def test_both_dates_are_the_same_until_one_is_moved(self, vault):
        for date, original in _dates_of(vault).values():
            assert date == original

    def test_setting_one_moves_the_date_and_keeps_the_original(self, vault):
        vault.set_effective_date("credit-card", "2026-02-03", "AMAZON.COM", -42.10, "2026-01-28")
        assert _dates_of(vault)["AMAZON.COM"] == ("2026-01-28", "2026-02-03")

    def test_the_moved_date_is_what_it_sorts_by(self, vault):
        vault.set_effective_date("credit-card", "2026-02-10", "GAS STATION", -30.00, "2026-01-02")
        assert vault.list_transactions()["description"].tolist()[0] == "GAS STATION"

    def test_a_date_range_asks_where_it_counts_now(self, vault):
        vault.set_effective_date("credit-card", "2026-02-03", "AMAZON.COM", -42.10, "2026-01-28")
        january = vault.list_transactions(start_date="2026-01-01", end_date="2026-01-31")
        assert "AMAZON.COM" in january["description"].tolist()
        february = vault.list_transactions(start_date="2026-02-01", end_date="2026-02-28")
        assert "AMAZON.COM" not in february["description"].tolist()

    def test_it_is_priced_on_the_date_it_moved_to(self, vault):
        _seed_prices(vault, (2, "2026-01-15", 100.0), (2, "2026-02-02", 250.0))
        assert vault.list_transactions().set_index("description").loc["buy VTI", "price"] == 250.0
        vault.set_effective_date(
            "checking", "2026-02-02", "buy VTI", 3.2, "2026-01-15", assets="VTI"
        )
        moved = vault.list_transactions().set_index("description").loc["buy VTI"]
        assert moved["price"] == 100.0
        assert moved["market_value"] == pytest.approx(320.0)


class TestSetEffectiveDate:
    def test_it_writes_one_row_naming_the_original_date(self, vault, adjustments):
        vault.set_effective_date("credit-card", "2026-02-03", "AMAZON.COM", -42.10, "2026-01-28")
        rows = pd.read_csv(_file(adjustments))
        assert len(rows) == 1
        assert rows.iloc[0].to_dict() == {
            "account": "credit-card",
            "date": "2026-02-03",
            "description": "AMAZON.COM",
            "amount": -42.10,
            "asset": "USD",
            "effective_date": "2026-01-28",
        }

    def test_one_date_applies_to_a_batch(self, vault):
        vault.set_effective_date(
            "credit-card",
            ["2026-02-03", "2026-02-04"],
            ["AMAZON.COM", "AMAZON MKTPL"],
            [-42.10, -17.99],
            "2026-01-28",
        )
        dates = {d for d, _ in _dates_of(vault).values()}
        assert dates == {"2026-01-28", "2026-01-31", "2026-02-02", "2026-02-10"}

    def test_setting_it_again_replaces_the_row(self, vault, adjustments):
        vault.set_effective_date("credit-card", "2026-02-03", "AMAZON.COM", -42.10, "2026-01-28")
        vault.set_effective_date("credit-card", "2026-02-03", "AMAZON.COM", -42.10, "2026-01-20")
        rows = pd.read_csv(_file(adjustments))
        assert rows["effective_date"].tolist() == ["2026-01-20"]
        assert _dates_of(vault)["AMAZON.COM"] == ("2026-01-20", "2026-02-03")

    def test_none_puts_it_back_and_drops_the_row(self, vault, adjustments):
        vault.set_effective_date("credit-card", "2026-02-03", "AMAZON.COM", -42.10, "2026-01-28")
        vault.set_effective_date("credit-card", "2026-02-03", "AMAZON.COM", -42.10, None)
        assert pd.read_csv(_file(adjustments)).empty
        assert _dates_of(vault)["AMAZON.COM"] == ("2026-02-03", "2026-02-03")

    def test_one_call_can_move_some_and_restore_others(self, vault, adjustments):
        vault.set_effective_date(
            "credit-card",
            ["2026-02-03", "2026-02-04"],
            ["AMAZON.COM", "AMAZON MKTPL"],
            [-42.10, -17.99],
            "2026-01-28",
        )
        vault.set_effective_date(
            "credit-card",
            ["2026-02-03", "2026-02-04"],
            ["AMAZON.COM", "AMAZON MKTPL"],
            [-42.10, -17.99],
            [None, "2026-01-20"],
        )
        rows = pd.read_csv(_file(adjustments))
        assert rows["date"].tolist() == ["2026-02-04"]
        assert rows["effective_date"].tolist() == ["2026-01-20"]

    def test_the_last_decision_in_one_call_wins(self, vault, adjustments):
        vault.set_effective_date(
            "credit-card",
            "2026-02-03",
            "AMAZON.COM",
            -42.10,
            ["2026-01-28", "2026-01-20"],
        )
        assert pd.read_csv(_file(adjustments))["effective_date"].tolist() == ["2026-01-20"]

    def test_a_dated_object_works_as_well_as_a_string(self, vault):
        import datetime

        vault.set_effective_date(
            "credit-card",
            datetime.date(2026, 2, 3),
            "AMAZON.COM",
            -42.10,
            datetime.date(2026, 1, 28),
        )
        assert _dates_of(vault)["AMAZON.COM"][0] == "2026-01-28"

    def test_the_asset_column_pins_a_non_base_row(self, vault, adjustments):
        vault.set_effective_date(
            "checking", "2026-02-02", "buy VTI", 3.2, "2026-01-15", assets="VTI"
        )
        assert pd.read_csv(_file(adjustments))["asset"].tolist() == ["VTI"]
        assert _dates_of(vault)["buy VTI"] == ("2026-01-15", "2026-02-02")

    def test_names_ignore_capitalization(self, vault):
        vault.set_effective_date("CREDIT-CARD", "2026-02-03", "AMAZON.COM", -42.10, "2026-01-28")
        assert _dates_of(vault)["AMAZON.COM"][0] == "2026-01-28"

    def test_a_row_matching_nothing_raises_and_writes_nothing(self, vault, adjustments):
        with pytest.raises(ValueError, match="No transaction matches"):
            vault.set_effective_date("credit-card", "2026-02-03", "AMAZON.CO", -42.10, "2026-01-28")
        assert pd.read_csv(_file(adjustments)).empty

    def test_every_unmatched_row_is_named(self, vault):
        with pytest.raises(ValueError) as excinfo:
            vault.set_effective_date(
                "credit-card",
                ["2026-02-03", "2026-02-04"],
                ["NOPE", "ALSO NOPE"],
                [-1.0, -2.0],
                "2026-01-28",
            )
        assert "NOPE" in str(excinfo.value)
        assert "ALSO NOPE" in str(excinfo.value)

    def test_a_date_that_is_not_a_date_raises_and_writes_nothing(self, vault, adjustments):
        with pytest.raises(ValueError, match="not dates"):
            vault.set_effective_date("credit-card", "2026-02-03", "AMAZON.COM", -42.10, "someday")
        assert pd.read_csv(_file(adjustments)).empty

    def test_mismatched_lengths_raise(self, vault):
        with pytest.raises(ValueError, match="mismatched lengths"):
            vault.set_effective_date(
                "credit-card",
                ["2026-02-03", "2026-02-04"],
                ["AMAZON.COM"],
                [-42.10, -17.99],
                "2026-01-28",
            )


class TestApplyEffectiveDates:
    def test_a_hand_written_file_is_applied(self, vault, adjustments):
        _write_file(adjustments, "credit-card,2026-02-03,AMAZON.COM,-42.10,,2026-01-28")
        assert vault.apply_effective_dates().empty
        assert _dates_of(vault)["AMAZON.COM"] == ("2026-01-28", "2026-02-03")

    def test_a_blank_asset_means_the_base_currency(self, vault, adjustments):
        _write_file(adjustments, "credit-card,2026-02-03,AMAZON.COM,-42.10,,2026-01-28")
        vault.apply_effective_dates()
        assert _dates_of(vault)["AMAZON.COM"][0] == "2026-01-28"

    def test_removing_a_row_puts_the_transaction_back(self, vault, adjustments):
        _write_file(adjustments, "credit-card,2026-02-03,AMAZON.COM,-42.10,,2026-01-28")
        vault.apply_effective_dates()
        _write_file(adjustments)
        vault.apply_effective_dates()
        assert _dates_of(vault)["AMAZON.COM"] == ("2026-02-03", "2026-02-03")

    def test_the_last_row_for_a_transaction_wins(self, vault, adjustments):
        _write_file(
            adjustments,
            "credit-card,2026-02-03,AMAZON.COM,-42.10,,2026-01-28",
            "credit-card,2026-02-03,AMAZON.COM,-42.10,,2026-01-20",
        )
        vault.apply_effective_dates()
        assert _dates_of(vault)["AMAZON.COM"][0] == "2026-01-20"

    def test_a_row_matching_nothing_comes_back_as_an_orphan(self, vault, adjustments):
        _write_file(
            adjustments,
            "credit-card,2026-02-03,AMAZON.COM,-42.10,,2026-01-28",
            "credit-card,2026-02-03,GONE,-42.10,,2026-01-28",
        )
        orphans = vault.apply_effective_dates()
        assert orphans["description"].tolist() == ["GONE"]
        assert _dates_of(vault)["AMAZON.COM"][0] == "2026-01-28"

    def test_an_empty_effective_date_raises(self, vault, adjustments):
        _write_file(adjustments, "credit-card,2026-02-03,AMAZON.COM,-42.10,,")
        with pytest.raises(ValueError, match="no effective date"):
            vault.apply_effective_dates()

    def test_an_unreadable_effective_date_raises(self, vault, adjustments):
        _write_file(adjustments, "credit-card,2026-02-03,AMAZON.COM,-42.10,,someday")
        with pytest.raises(ValueError, match="not dates"):
            vault.apply_effective_dates()

    def test_a_non_numeric_amount_raises(self, vault, adjustments):
        _write_file(adjustments, "credit-card,2026-02-03,AMAZON.COM,lots,,2026-01-28")
        with pytest.raises(ValueError, match="non-numeric amount"):
            vault.apply_effective_dates()

    def test_a_missing_column_raises(self, vault, adjustments):
        _file(adjustments).write_text("account,date,description,amount,asset\n")
        with pytest.raises(ValueError, match="missing the column"):
            vault.apply_effective_dates()

    def test_a_missing_folder_raises(self, vault, adjustments, tmp_path):
        for f in adjustments.iterdir():
            f.unlink()
        adjustments.rmdir()
        with pytest.raises(FileNotFoundError, match="adjustments folder is missing"):
            vault.apply_effective_dates()


class TestItSurvives:
    def test_a_statement_reload_keeps_the_moved_date(self, vault):
        vault.set_effective_date("credit-card", "2026-02-03", "AMAZON.COM", -42.10, "2026-01-28")
        vault.add_transactions("credit-card", ["2026-02-03"], ["AMAZON.COM"], [-42.10])
        assert _dates_of(vault)["AMAZON.COM"] == ("2026-01-28", "2026-02-03")

    def test_a_rebuild_from_the_files_restores_it(self, vault, tmp_path):
        vault.set_effective_date("credit-card", "2026-02-03", "AMAZON.COM", -42.10, "2026-01-28")
        vault.close()
        with Vault.create(
            tmp_path / "Vault.db", overwrite=True, adjustments_dir=tmp_path / "adjustments"
        ) as rebuilt:
            rebuilt.add_account("credit-card")
            rebuilt.add_transactions("credit-card", ["2026-02-03"], ["AMAZON.COM"], [-42.10])
            assert rebuilt.apply_effective_dates().empty
            assert _dates_of(rebuilt)["AMAZON.COM"] == ("2026-01-28", "2026-02-03")

    def test_a_vault_from_before_the_column_gets_it_on_open(self, vault, tmp_path):
        with vault._conn:
            vault._conn.execute("ALTER TABLE transactions DROP COLUMN effective_date;")
        vault.close()
        with Vault.open(tmp_path / "Vault.db") as reopened:
            columns = reopened._conn.execute("PRAGMA table_info(transactions);").fetchall()
            assert any(row[1] == "effective_date" for row in columns)
            reopened.set_effective_date(
                "credit-card", "2026-02-03", "AMAZON.COM", -42.10, "2026-01-28"
            )
            assert _dates_of(reopened)["AMAZON.COM"][0] == "2026-01-28"


class TestTheRestOfTheVault:
    def test_a_category_exception_still_names_the_original_date(self, vault):
        """The decisive one: moving a transaction must not orphan the other
        decisions made about it, which is why every file keys on the date the
        statement gave it."""
        vault.add_category("shopping")
        vault.set_category_exception("credit-card", "2026-02-03", "AMAZON.COM", -42.10, "shopping")
        vault.set_effective_date("credit-card", "2026-02-03", "AMAZON.COM", -42.10, "2026-01-28")
        assert vault.apply_categories().empty
        categories = dict(
            zip(vault.list_transactions()["description"], vault.list_transactions()["category"])
        )
        assert categories["AMAZON.COM"] == "shopping"

    def test_the_vault_summary_spans_the_dates_things_count_on(self, vault):
        vault.set_effective_date("credit-card", "2026-02-10", "GAS STATION", -30.00, "2026-03-15")
        summary = vault.summarize_vault()
        assert summary["last_transaction"] == pd.Timestamp("2026-03-15")

    def test_net_worth_history_moves_with_it(self, vault):
        history = vault.accumulate_mv(group_by="account_name")
        assert history.loc[pd.Timestamp("2026-02-03"), "credit-card"] == pytest.approx(-42.10)
        vault.set_effective_date("credit-card", "2026-02-03", "AMAZON.COM", -42.10, "2026-01-28")
        history = vault.accumulate_mv(group_by="account_name")
        assert history.loc[pd.Timestamp("2026-01-28"), "credit-card"] == pytest.approx(-42.10)

    def test_the_account_summary_reports_the_date_it_counts_on(self, vault):
        vault.set_effective_date("credit-card", "2026-02-10", "GAS STATION", -30.00, "2026-03-15")
        summary = vault.summarize_accounts().set_index("account_name")
        assert summary.loc["credit-card", "last_transaction"] == pd.Timestamp("2026-03-15")
