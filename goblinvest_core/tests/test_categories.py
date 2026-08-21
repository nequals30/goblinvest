import sqlite3

import pandas as pd
import pytest

from goblinvest_core import Vault, _password, encrypt_file, forget_password

UNCLASSIFIED = "unclassified"

# The vocabulary these tests hand out; categories must be defined before use.
CATEGORIES = [
    "checks",
    "fraud",
    "fun",
    "games",
    "gifts",
    "groceries",
    "income",
    "investing",
    "misc",
    "movies",
    "rent",
    "streaming",
]


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
    """Make any password prompt a test failure."""

    def boom(prompt=""):
        raise AssertionError("asked for a password when it should not have")

    monkeypatch.setattr(_password, "getpass", boom)


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


def _categories_file(adjustments_dir):
    return adjustments_dir / "categories.csv"


@pytest.fixture
def vault(tmp_path):
    """A vault with its adjustments folder, and the test vocabulary defined."""
    v = Vault.create(tmp_path / "Vault.db", adjustments_dir=tmp_path / "adjustments")
    v.add_category(CATEGORIES)
    v.add_account("checking", account_group_name="cash")
    v.add_account("credit-card", account_group_name="credit")
    v.add_asset("VTI")
    v.add_transactions(
        "checking",
        ["2026-01-05", "2026-01-05", "2026-02-01", "2026-02-14", "2026-03-14"],
        ["Netflix", "Netflix", "ACME Payroll", "CHECK # 1145", "CHECK # 1146"],
        [-15.99, -15.99, 3000.00, -500.00, -200.00],
    )
    v.add_transactions(
        "credit-card", ["2026-01-20", "2026-01-21"], ["Steamboat Grill", "Steam"], [-45.0, -10.0]
    )
    v.add_transactions("checking", ["2026-01-30"], ["buy VTI"], [3.2], assets="VTI")
    yield v
    v.close()


@pytest.fixture
def adjustments(vault):
    return vault.adjustments_dir


def _categories_of(v):
    df = v.list_transactions()
    return dict(zip(df["description"], df["category"]))


def _rules_file(adjustments_dir):
    return adjustments_dir / "category_rules.csv"


def _exceptions_file(adjustments_dir):
    return adjustments_dir / "category_exceptions.csv"


def _write_rules(adjustments_dir, *rows):
    _rules_file(adjustments_dir).write_text(
        "pattern,category\n" + "".join(f"{p},{c}\n" for p, c in rows)
    )


def _write_exceptions(adjustments_dir, *rows):
    _exceptions_file(adjustments_dir).write_text(
        "account,date,description,amount,asset,category\n" + "".join(f"{r}\n" for r in rows)
    )


class TestAdjustmentsFolder:
    def test_create_starts_the_folder_and_its_files(self, adjustments):
        assert adjustments.is_dir()
        assert _rules_file(adjustments).read_text() == "pattern,category\n"
        assert (
            _exceptions_file(adjustments).read_text()
            == "account,date,description,amount,asset,category\n"
        )

    def test_default_location_is_beside_the_vault(self, tmp_path):
        with Vault.create(tmp_path / "MyVault.db") as v:
            assert v.adjustments_dir == tmp_path / "MyVault_adjustments"
            assert _rules_file(v.adjustments_dir).is_file()

    def test_open_remembers_the_folder(self, tmp_path):
        elsewhere = tmp_path / "statements"
        elsewhere.mkdir()
        with Vault.create(tmp_path / "V.db", adjustments_dir=elsewhere / "adj") as v:
            v.add_category("groceries")
        with Vault.open(tmp_path / "V.db") as v:
            assert v.adjustments_dir == elsewhere / "adj"
            v.set_category_rule("anything", "groceries")  # no folder named anywhere

    def test_open_can_repoint_a_moved_folder_for_good(self, tmp_path, vault):
        moved = tmp_path / "moved"
        vault.adjustments_dir.rename(moved)
        vault.close()
        with Vault.open(tmp_path / "Vault.db", adjustments_dir=moved) as v:
            assert v.adjustments_dir == moved
        with Vault.open(tmp_path / "Vault.db") as v:  # and it stuck
            assert v.adjustments_dir == moved

    def test_the_pair_can_be_moved_together(self, tmp_path):
        home = tmp_path / "finance"
        home.mkdir()
        with Vault.create(home / "V.db", adjustments_dir=home / "adj") as v:
            v.add_category("groceries")
        home.rename(tmp_path / "finance-elsewhere")
        with Vault.open(tmp_path / "finance-elsewhere" / "V.db") as v:
            assert v.adjustments_dir == tmp_path / "finance-elsewhere" / "adj"
            assert "groceries" in _categories_file(v.adjustments_dir).read_text()

    def test_missing_folder_says_so(self, tmp_path, vault):
        import shutil

        shutil.rmtree(vault.adjustments_dir)
        with pytest.raises(FileNotFoundError, match="adjustments folder is missing"):
            vault.apply_categories()

    def test_vault_that_predates_adjustments_says_so(self, tmp_path):
        with Vault.create(tmp_path / "V.db") as v:
            v._conn.execute("DROP TABLE settings;")
        with Vault.open(tmp_path / "V.db") as v:
            with pytest.raises(FileNotFoundError, match="does not know where"):
                v.apply_categories()
            # ...and pointing it at a folder is the fix
        with Vault.open(tmp_path / "V.db", adjustments_dir=tmp_path / "adj") as v:
            assert v.adjustments_dir == tmp_path / "adj"

    def test_a_deleted_file_comes_back_on_use(self, vault, adjustments):
        _exceptions_file(adjustments).unlink()
        vault.apply_categories()
        assert (
            _exceptions_file(adjustments).read_text()
            == "account,date,description,amount,asset,category\n"
        )

    def test_rebuilding_the_vault_keeps_the_adjustments(self, tmp_path, vault, adjustments):
        vault.set_category_rule("Netflix", "streaming")
        before = _rules_file(adjustments).read_text()
        vault.close()
        with Vault.create(tmp_path / "Vault.db", overwrite=True, adjustments_dir=adjustments) as v:
            assert _rules_file(adjustments).read_text() == before
            assert "streaming" in _categories_file(v.adjustments_dir).read_text()

    def test_missing_parent_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            Vault.create(tmp_path / "V.db", adjustments_dir=tmp_path / "typo" / "adj")

    def test_existing_file_in_the_way_raises(self, tmp_path):
        occupied = tmp_path / "adj"
        occupied.write_text("not a folder\n")
        with pytest.raises(NotADirectoryError):
            Vault.create(tmp_path / "V.db", adjustments_dir=occupied)


class TestEncryptedFolder:
    def test_encrypted_vault_encrypts_its_adjustments(self, tmp_path, monkeypatch):
        _typed(monkeypatch, "pw", "pw")
        with Vault.create(tmp_path / "V.db", encrypted=True) as v:
            for f in (_rules_file(v.adjustments_dir), _categories_file(v.adjustments_dir)):
                assert f.read_bytes().startswith(b"GVENC")
            assert b"pattern" not in _rules_file(v.adjustments_dir).read_bytes()

    def test_plain_vault_leaves_them_plain(self, tmp_path, monkeypatch):
        _no_prompt(monkeypatch)
        with Vault.create(tmp_path / "V.db") as v:
            assert not _rules_file(v.adjustments_dir).read_bytes().startswith(b"GVENC")

    def test_a_new_file_matches_the_folder(self, tmp_path, monkeypatch):
        # A file added later (a new kind of adjustment) is encrypted because
        # the folder already is, without asking again.
        _typed(monkeypatch, "pw", "pw")
        with Vault.create(tmp_path / "V.db", encrypted=True) as v:
            _exceptions_file(v.adjustments_dir).unlink()
            v.apply_categories()
            assert _exceptions_file(v.adjustments_dir).read_bytes().startswith(b"GVENC")

    def test_mistyped_new_password_creates_nothing(self, tmp_path, monkeypatch):
        _typed(monkeypatch, "pw", "typo")
        with pytest.raises(ValueError, match="do not match"):
            Vault.create(tmp_path / "V.db", encrypted=True)
        assert not (tmp_path / "V_adjustments").exists()


class TestAddCategory:
    def test_defines_one_and_many(self, tmp_path):
        with Vault.create(tmp_path / "V.db") as v:
            v.add_category("groceries")
            v.add_category(["rent", "travel"])
            assert (
                _categories_file(v.adjustments_dir).read_text()
                == "category\ngroceries\nrent\ntravel\n"
            )

    def test_is_idempotent(self, tmp_path):
        with Vault.create(tmp_path / "V.db") as v:
            v.add_category(["rent", "travel"])
            before = _categories_file(v.adjustments_dir).read_text()
            v.add_category(["rent", "travel"])
            v.add_category("rent")
            assert _categories_file(v.adjustments_dir).read_text() == before

    def test_capitalization_does_not_make_a_new_category(self, tmp_path):
        with Vault.create(tmp_path / "V.db") as v:
            v.add_category("groceries")
            v.add_category(["GROCERIES", "Groceries"])
            assert _categories_file(v.adjustments_dir).read_text() == "category\ngroceries\n"

    def test_duplicates_within_one_call_collapse(self, tmp_path):
        with Vault.create(tmp_path / "V.db") as v:
            v.add_category(["rent", "rent", "Rent"])
            assert _categories_file(v.adjustments_dir).read_text() == "category\nrent\n"

    def test_rejects_reserved_and_empty(self, vault):
        with pytest.raises(ValueError, match="reserved"):
            vault.add_category("Unclassified")
        with pytest.raises(ValueError, match="empty"):
            vault.add_category(["fine", "  "])

    def test_stays_encrypted(self, tmp_path, monkeypatch):
        _typed(monkeypatch, "pw", "pw")
        with Vault.create(tmp_path / "V.db", encrypted=True) as v:
            v.add_category("groceries")
            assert _categories_file(v.adjustments_dir).read_bytes().startswith(b"GVENC")
            assert b"groceries" not in _categories_file(v.adjustments_dir).read_bytes()


class TestDeleteCategory:
    @pytest.fixture
    def categorized(self, vault, adjustments, monkeypatch):
        """The test vault with a few decisions already made."""
        _no_confirmation(monkeypatch)
        vault.set_category_rule(["Netflix", "Steam"], "streaming")
        vault.set_category_rule("ACME Payroll", "income")
        vault.set_category_exception("checking", "2026-02-14", "CHECK # 1145", -500.00, "gifts")
        return vault

    def test_undefines_the_category(self, categorized, adjustments):
        categorized.delete_category("streaming", confirm=False)

        assert "streaming" not in _categories_file(adjustments).read_text()
        assert "streaming" not in categorized.list_categories()["category_name"].tolist()

    def test_takes_its_rules_and_unclassifies_their_transactions(self, categorized, adjustments):
        categorized.delete_category("streaming", confirm=False)

        assert _rules_file(adjustments).read_text() == "pattern,category\nACME Payroll,income\n"
        categories = _categories_of(categorized)
        assert categories["Netflix"] == UNCLASSIFIED
        assert categories["Steam"] == UNCLASSIFIED
        assert categories["ACME Payroll"] == "income"  # untouched

    def test_takes_its_exceptions_too(self, categorized, adjustments):
        categorized.delete_category("gifts", confirm=False)

        assert _exceptions_file(adjustments).read_text() == (
            "account,date,description,amount,asset,category\n"
        )
        assert _categories_of(categorized)["CHECK # 1145"] == UNCLASSIFIED

    def test_no_transaction_is_deleted(self, categorized):
        before = len(categorized.list_transactions())
        categorized.delete_category(["streaming", "gifts"], confirm=False)

        assert len(categorized.list_transactions()) == before

    def test_a_rebuild_does_not_bring_it_back(self, categorized):
        categorized.delete_category("streaming", confirm=False)
        categorized.apply_categories()

        assert _categories_of(categorized)["Netflix"] == UNCLASSIFIED

    def test_many_at_once(self, categorized, adjustments):
        categorized.delete_category(["streaming", "income"], confirm=False)

        assert _rules_file(adjustments).read_text() == "pattern,category\n"
        assert _categories_of(categorized)["ACME Payroll"] == UNCLASSIFIED

    def test_question_names_what_goes(self, categorized, monkeypatch):
        asked = _answers(monkeypatch, "y")
        categorized.delete_category("streaming")

        assert 'category "streaming"' in asked[0]
        assert "2 rules" in asked[0]
        assert "0 exceptions" in asked[0]
        assert "3 transactions" in asked[0]  # Netflix, Netflix (2), Steam

    def test_question_uses_the_spelling_you_defined(self, categorized, monkeypatch):
        asked = _answers(monkeypatch, "n")
        categorized.delete_category("STREAMING")

        assert 'category "streaming"' in asked[0]

    def test_declining_changes_nothing(self, categorized, adjustments, monkeypatch):
        before = _rules_file(adjustments).read_text()
        categories_before = _categories_file(adjustments).read_text()

        _answers(monkeypatch, "n")
        categorized.delete_category("streaming")

        assert _rules_file(adjustments).read_text() == before
        assert _categories_file(adjustments).read_text() == categories_before
        assert _categories_of(categorized)["Netflix"] == "streaming"

    def test_names_match_case_insensitively(self, categorized, adjustments):
        categorized.delete_category("Streaming", confirm=False)

        assert "streaming" not in _categories_file(adjustments).read_text()

    def test_undefined_category_raises_and_writes_nothing(self, categorized, adjustments):
        before = _categories_file(adjustments).read_text()
        with pytest.raises(ValueError, match="not defined.*streamign"):
            categorized.delete_category(["streaming", "streamign"], confirm=False)

        assert _categories_file(adjustments).read_text() == before
        assert _categories_of(categorized)["Netflix"] == "streaming"

    def test_a_category_nothing_uses_just_goes(self, vault, adjustments, monkeypatch):
        _no_confirmation(monkeypatch)
        vault.delete_category("movies", confirm=False)

        assert "movies" not in _categories_file(adjustments).read_text()

    def test_it_can_be_defined_again_afterwards(self, categorized, adjustments):
        categorized.delete_category("streaming", confirm=False)
        categorized.add_category("streaming")

        assert "streaming" in _categories_file(adjustments).read_text()
        # the rule went with it, so the transactions stay unclassified
        assert _categories_of(categorized)["Netflix"] == UNCLASSIFIED

    def test_stays_encrypted(self, tmp_path, monkeypatch):
        _typed(monkeypatch, "pw", "pw")
        with Vault.create(tmp_path / "V.db", encrypted=True) as v:
            v.add_category(["groceries", "rent"])
            v.delete_category("groceries", confirm=False)

            raw = _categories_file(v.adjustments_dir).read_bytes()
            assert raw.startswith(b"GVENC")
            assert b"rent" not in raw
            assert v.list_categories()["category_name"].tolist() == ["rent"]


class TestUndefinedCategories:
    def test_apply_rejects_undefined_rule_category(self, vault, adjustments):
        _write_rules(adjustments, ("Netflix", "streamign"))
        with pytest.raises(ValueError, match="not defined.*streamign"):
            vault.apply_categories()

    def test_apply_names_the_file_and_every_offender(self, vault, adjustments):
        _write_rules(adjustments, ("Netflix", "streamign"), ("Steam", "gamez"))
        with pytest.raises(ValueError, match="category_rules.csv") as excinfo:
            vault.apply_categories()
        assert "gamez" in str(excinfo.value) and "streamign" in str(excinfo.value)

    def test_apply_rejects_undefined_exception_category(self, vault, adjustments):
        _write_exceptions(adjustments, "checking,2026-02-14,CHECK # 1145,-500.0,,giftz")
        with pytest.raises(ValueError, match="not defined.*giftz"):
            vault.apply_categories()

    def test_rule_with_undefined_category_is_caught_even_if_it_matches_nothing(
        self, vault, adjustments
    ):
        _write_rules(adjustments, ("NOTHING LIKE THIS EXISTS", "typo"))
        with pytest.raises(ValueError, match="not defined"):
            vault.apply_categories()

    def test_setter_refuses_undefined_and_writes_nothing(self, vault, adjustments):
        before = _rules_file(adjustments).read_text()
        with pytest.raises(ValueError, match="not defined.*streamign"):
            vault.set_category_rule("Netflix", "streamign")
        assert _rules_file(adjustments).read_text() == before

    def test_exception_setter_refuses_undefined_and_writes_nothing(self, vault, adjustments):
        before = _exceptions_file(adjustments).read_text()
        with pytest.raises(ValueError, match="not defined.*giftz"):
            vault.set_category_exception(
                "checking",
                "2026-02-14",
                "CHECK # 1145",
                -500.0,
                "giftz",
            )
        assert _exceptions_file(adjustments).read_text() == before

    def test_defined_spelling_wins_over_the_files(self, vault, adjustments):
        _write_rules(adjustments, ("Netflix", "STREAMING"))
        vault.apply_categories()
        assert _categories_of(vault)["Netflix"] == "streaming"

    def test_setter_records_the_defined_spelling(self, vault, adjustments):
        vault.set_category_rule("Netflix", "Streaming")
        assert "Netflix,streaming" in _rules_file(adjustments).read_text()
        assert _categories_of(vault)["Netflix"] == "streaming"

    def test_defined_but_unused_categories_are_listed(self, vault, adjustments):
        _write_rules(adjustments, ("Netflix", "streaming"))
        vault.apply_categories()
        counts = vault.list_categories().set_index("category_name")["n_transactions"]
        assert set(counts.index) == set(CATEGORIES)  # every defined category
        assert counts["streaming"] == 2
        assert counts["rent"] == 0


class TestApplyCategories:
    def test_exact_match_ignores_case(self, vault, adjustments):
        _write_rules(adjustments, ("netflix", "streaming"))
        vault.apply_categories()
        cats = _categories_of(vault)
        assert cats["Netflix"] == "streaming"
        assert cats["ACME Payroll"] == UNCLASSIFIED

    def test_no_substring_matching(self, vault, adjustments):
        _write_rules(adjustments, ("Steam", "games"), ("CHECK #", "checks"))
        vault.apply_categories()
        cats = _categories_of(vault)
        assert cats["Steam"] == "games"
        assert cats["Steamboat Grill"] == UNCLASSIFIED  # not a letter-for-letter match
        assert cats["CHECK # 1145"] == UNCLASSIFIED

    def test_duplicate_suffix_matches_base_rule(self, vault, adjustments):
        _write_rules(adjustments, ("Netflix", "streaming"))
        vault.apply_categories()
        assert _categories_of(vault)["Netflix (2)"] == "streaming"

    def test_suffix_stripped_from_patterns_too(self, vault, adjustments):
        _write_rules(adjustments, ("Netflix (2)", "streaming"))
        vault.apply_categories()
        cats = _categories_of(vault)
        assert cats["Netflix"] == "streaming"
        assert cats["Netflix (2)"] == "streaming"

    def test_last_rule_wins(self, vault, adjustments):
        _write_rules(adjustments, ("Netflix", "movies"), ("Netflix", "streaming"))
        vault.apply_categories()
        assert _categories_of(vault)["Netflix"] == "streaming"

    def test_exception_beats_rule(self, vault, adjustments):
        _write_rules(adjustments, ("CHECK # 1145", "checks"))
        _write_exceptions(adjustments, "checking,2026-02-14,CHECK # 1145,-500.0,,gifts")
        orphans = vault.apply_categories()
        assert orphans.empty
        assert _categories_of(vault)["CHECK # 1145"] == "gifts"

    def test_exception_is_exact_not_normalized(self, vault, adjustments):
        # An exception pins one transaction: the " (2)" twin is untouched.
        _write_exceptions(adjustments, "checking,2026-01-05,Netflix (2),-15.99,,fraud")
        vault.apply_categories()
        cats = _categories_of(vault)
        assert cats["Netflix (2)"] == "fraud"
        assert cats["Netflix"] == UNCLASSIFIED

    def test_exception_account_asset_case_insensitive_blank_asset(self, vault, adjustments):
        _write_exceptions(adjustments, "CHECKING,2026-01-30,buy VTI,3.2,vti,investing")
        orphans = vault.apply_categories()
        assert orphans.empty
        assert _categories_of(vault)["buy VTI"] == "investing"

    def test_orphaned_exceptions_are_returned_not_raised(self, vault, adjustments):
        _write_exceptions(
            adjustments,
            "checking,2026-02-14,CHECK # 1145,-500.0,,gifts",
            "checking,2026-02-14,REWORDED BY BANK,-500.0,,gifts",
        )
        orphans = vault.apply_categories()
        assert orphans["description"].tolist() == ["REWORDED BY BANK"]

    def test_duplicate_exception_keeps_last(self, vault, adjustments):
        _write_exceptions(
            adjustments,
            "checking,2026-02-14,CHECK # 1145,-500.0,,gifts",
            "checking,2026-02-14,CHECK # 1145,-500.0,,rent",
        )
        orphans = vault.apply_categories()
        assert orphans.empty
        assert _categories_of(vault)["CHECK # 1145"] == "rent"

    def test_reapply_is_idempotent_with_stable_ids(self, vault, adjustments):
        _write_rules(adjustments, ("Netflix", "streaming"), ("Steam", "games"))
        vault.apply_categories()
        first = vault.list_categories()
        vault.apply_categories()
        pd.testing.assert_frame_equal(vault.list_categories(), first)

    def test_reserved_and_empty_categories_rejected(self, vault, adjustments):
        _write_rules(adjustments, ("Netflix", "Unclassified"))
        with pytest.raises(ValueError, match="reserved"):
            vault.apply_categories()
        _write_rules(adjustments, ("Netflix", ""))
        with pytest.raises(ValueError, match="empty category"):
            vault.apply_categories()
        _write_rules(adjustments, ("", "streaming"))
        with pytest.raises(ValueError, match="empty pattern"):
            vault.apply_categories()

    def test_reloading_statements_preserves_categories(self, vault, adjustments):
        _write_rules(adjustments, ("Netflix", "streaming"))
        vault.apply_categories()
        vault.add_transactions(
            "checking", ["2026-01-05", "2026-01-05"], ["Netflix", "Netflix"], [-15.99, -15.99]
        )
        assert _categories_of(vault)["Netflix"] == "streaming"


class TestSetCategoryRule:
    def test_appends_rule_and_syncs_vault(self, vault, adjustments):
        vault.set_category_rule("ACME Payroll", "income")
        assert _rules_file(adjustments).read_text().endswith("ACME Payroll,income\n")
        assert _categories_of(vault)["ACME Payroll"] == "income"

    def test_skips_append_when_rules_already_answer(self, vault, adjustments):
        _write_rules(adjustments, ("acme payroll", "income"))
        before = _rules_file(adjustments).read_text()
        vault.set_category_rule("ACME Payroll", "income")
        assert _rules_file(adjustments).read_text() == before

    def test_reclassify_appends_and_last_wins(self, vault, adjustments):
        vault.set_category_rule("Netflix", "movies")
        vault.set_category_rule("Netflix", "streaming")
        assert _rules_file(adjustments).read_text().count("Netflix") == 2
        assert _categories_of(vault)["Netflix"] == "streaming"

    def test_suffixed_description_writes_base_pattern(self, vault, adjustments):
        vault.set_category_rule("Netflix (2)", "streaming")
        assert "Netflix,streaming" in _rules_file(adjustments).read_text()
        assert _categories_of(vault)["Netflix"] == "streaming"

    def test_rejects_reserved_and_empty(self, vault, adjustments):
        with pytest.raises(ValueError, match="reserved"):
            vault.set_category_rule("Netflix", "UNCLASSIFIED")
        with pytest.raises(ValueError, match="empty"):
            vault.set_category_rule("Netflix", "  ")

    def test_parallel_lists(self, vault, adjustments):
        vault.set_category_rule(["Netflix", "ACME Payroll"], ["streaming", "income"])
        cats = _categories_of(vault)
        assert cats["Netflix"] == "streaming"
        assert cats["ACME Payroll"] == "income"

    def test_one_category_broadcasts_across_descriptions(self, vault, adjustments):
        vault.set_category_rule(["Steam", "Steamboat Grill"], "fun")
        cats = _categories_of(vault)
        assert cats["Steam"] == cats["Steamboat Grill"] == "fun"

    def test_accepts_a_series(self, vault, adjustments):
        # list_uncategorized()["description"] straight back in is the to-do-list workflow.
        todo = vault.list_uncategorized()["description"]
        vault.set_category_rule(todo, "misc")
        assert vault.list_uncategorized().empty

    def test_mismatched_lengths_raise(self, vault, adjustments):
        with pytest.raises(ValueError, match="mismatched lengths"):
            vault.set_category_rule(["Netflix", "Steam"], ["streaming"])

    def test_repeated_description_in_one_call_keeps_last(self, vault, adjustments):
        vault.set_category_rule(["Netflix", "Netflix"], ["movies", "streaming"])
        assert _rules_file(adjustments).read_text().count("Netflix") == 1
        assert _categories_of(vault)["Netflix"] == "streaming"

    def test_batch_appends_only_what_is_new(self, vault, adjustments):
        _write_rules(adjustments, ("Netflix", "streaming"))
        vault.set_category_rule(["Netflix", "Steam"], ["streaming", "games"])
        assert _rules_file(adjustments).read_text() == (
            "pattern,category\nNetflix,streaming\nSteam,games\n"
        )

    def test_one_bad_row_writes_nothing(self, vault, adjustments):
        before = _rules_file(adjustments).read_text()
        with pytest.raises(ValueError, match="reserved"):
            vault.set_category_rule(["Netflix", "Steam"], ["streaming", "unclassified"])
        assert _rules_file(adjustments).read_text() == before


class TestSetCategoryException:
    def test_sets_one_transaction_only(self, vault, adjustments):
        _write_rules(adjustments, ("Netflix", "streaming"))
        vault.apply_categories()
        vault.set_category_exception(
            "checking",
            "2026-01-05",
            "Netflix (2)",
            -15.99,
            "fraud",
        )
        cats = _categories_of(vault)
        assert cats["Netflix (2)"] == "fraud"
        assert cats["Netflix"] == "streaming"

    def test_replaces_earlier_exception_for_same_transaction(self, vault, adjustments):
        for category in ("gifts", "rent"):
            vault.set_category_exception(
                "checking",
                "2026-02-14",
                "CHECK # 1145",
                -500.0,
                category,
            )
        file_df = pd.read_csv(_exceptions_file(adjustments))
        assert len(file_df) == 1
        assert _categories_of(vault)["CHECK # 1145"] == "rent"

    def test_none_removes_exception_and_falls_back_to_rules(self, vault, adjustments):
        _write_rules(adjustments, ("CHECK # 1145", "checks"))
        vault.set_category_exception(
            "checking",
            "2026-02-14",
            "CHECK # 1145",
            -500.0,
            "gifts",
        )
        vault.set_category_exception(
            "checking",
            "2026-02-14",
            "CHECK # 1145",
            -500.0,
            None,
        )
        assert len(pd.read_csv(_exceptions_file(adjustments))) == 0
        assert _categories_of(vault)["CHECK # 1145"] == "checks"

    def test_nonexistent_transaction_raises(self, vault, adjustments):
        with pytest.raises(ValueError, match="No transaction matches"):
            vault.set_category_exception(
                "checking",
                "2026-02-14",
                "CHECK # 9999",
                -500.0,
                "gifts",
            )

    def test_asset_defaults_to_base_currency(self, vault, adjustments):
        vault.set_category_exception(
            "checking",
            "2026-01-30",
            "buy VTI",
            3.2,
            "investing",
            assets="VTI",
        )
        with pytest.raises(ValueError, match="No transaction matches"):
            vault.set_category_exception(
                "checking",
                "2026-01-30",
                "buy VTI",
                3.2,
                "investing",  # asset omitted -> USD, and no USD row matches
            )

    def test_parallel_lists_with_broadcast(self, vault, adjustments):
        vault.set_category_exception(
            "checking",
            ["2026-02-14", "2026-03-14"],
            ["CHECK # 1145", "CHECK # 1146"],
            [-500.0, -200.0],
            "gifts",
        )
        cats = _categories_of(vault)
        assert cats["CHECK # 1145"] == cats["CHECK # 1146"] == "gifts"
        assert len(pd.read_csv(_exceptions_file(adjustments))) == 2

    def test_mixed_categories_set_some_and_clear_others(self, vault, adjustments):
        _write_rules(adjustments, ("CHECK # 1145", "checks"))
        vault.set_category_exception(
            "checking",
            ["2026-02-14", "2026-03-14"],
            ["CHECK # 1145", "CHECK # 1146"],
            [-500.0, -200.0],
            "gifts",
        )
        vault.set_category_exception(
            "checking",
            ["2026-02-14", "2026-03-14"],
            ["CHECK # 1145", "CHECK # 1146"],
            [-500.0, -200.0],
            [None, "rent"],
        )
        file_df = pd.read_csv(_exceptions_file(adjustments))
        assert file_df["description"].tolist() == ["CHECK # 1146"]
        cats = _categories_of(vault)
        assert cats["CHECK # 1145"] == "checks"  # exception removed, rule takes over
        assert cats["CHECK # 1146"] == "rent"

    def test_every_unmatched_row_is_named_and_nothing_written(self, vault, adjustments):
        before = _exceptions_file(adjustments).read_text()
        with pytest.raises(ValueError, match="(?s)CHECK # 9998.*CHECK # 9999") as excinfo:
            vault.set_category_exception(
                "checking",
                ["2026-02-14", "2026-02-14", "2026-02-14"],
                ["CHECK # 1145", "CHECK # 9998", "CHECK # 9999"],
                [-500.0, -1.0, -2.0],
                "gifts",
            )
        assert "CHECK # 1145" not in str(excinfo.value)  # the good row isn't blamed
        assert _exceptions_file(adjustments).read_text() == before

    def test_mismatched_lengths_raise(self, vault, adjustments):
        with pytest.raises(ValueError, match="mismatched lengths"):
            vault.set_category_exception(
                "checking",
                ["2026-02-14", "2026-03-14"],
                ["CHECK # 1145"],
                [-500.0, -200.0],
                "gifts",
            )

    def test_repeated_transaction_in_one_call_keeps_last(self, vault, adjustments):
        vault.set_category_exception(
            "checking",
            "2026-02-14",
            "CHECK # 1145",
            -500.0,
            ["gifts", "rent"],
        )
        assert len(pd.read_csv(_exceptions_file(adjustments))) == 1
        assert _categories_of(vault)["CHECK # 1145"] == "rent"


class TestListViews:
    def test_transactions_have_category_column_after_asset(self, vault):
        columns = list(vault.list_transactions().columns)
        assert columns.index("category") == columns.index("asset") + 1
        assert set(vault.list_transactions()["category"]) == {UNCLASSIFIED}

    def test_list_categories_counts(self, vault, adjustments):
        _write_rules(adjustments, ("Netflix", "streaming"), ("Steam", "games"))
        vault.apply_categories()
        df = vault.list_categories().set_index("category_name")
        assert df.loc["streaming", "n_transactions"] == 2  # Netflix + Netflix (2)
        assert df.loc["games", "n_transactions"] == 1

    def test_list_uncategorized_groups_and_sorts(self, vault, adjustments):
        _write_rules(adjustments, ("Steam", "games"))
        vault.apply_categories()
        df = vault.list_uncategorized()
        assert list(df.columns) == ["account_name", "description", "n_transactions", "total_amount"]
        top = df.iloc[0]
        assert (top["description"], top["n_transactions"], top["total_amount"]) == (
            "Netflix",
            2,
            -31.98,
        )
        assert "Steam" not in df["description"].tolist()

    def test_list_uncategorized_empty_when_all_categorized(self, vault, adjustments):
        for description in vault.list_uncategorized()["description"]:
            vault.set_category_rule(description, "misc")
        assert vault.list_uncategorized().empty


class TestEncryptedFiles:
    def test_documented_encrypted_workflow(self, tmp_path, monkeypatch):
        # The worked example from the docs: one encrypted=True, then the
        # ordinary calls with no password and no folder named anywhere.
        _typed(monkeypatch, "pw", "pw")
        vault = Vault.create(tmp_path / "Vault.db", encrypted=True)
        vault.add_account("checking")
        vault.add_transactions(
            "checking",
            ["2026-01-05", "2026-02-14"],
            ["Netflix", "CHECK # 1145"],
            [-15.99, -500.00],
        )
        adjustments = vault.adjustments_dir
        vault.add_category(["streaming", "gifts"])

        vault.apply_categories()
        vault.set_category_rule("Netflix", "streaming")
        vault.set_category_exception(
            "checking",
            "2026-02-14",
            "CHECK # 1145",
            -500.00,
            "gifts",
        )
        cats = _categories_of(vault)
        assert cats["Netflix"] == "streaming"
        assert cats["CHECK # 1145"] == "gifts"
        # still encrypted, and the plaintext never landed on disk
        for f in (_rules_file(adjustments), _exceptions_file(adjustments)):
            assert f.read_bytes().startswith(b"GVENC")
        assert b"Netflix" not in _rules_file(adjustments).read_bytes()
        assert b"gifts" not in _exceptions_file(adjustments).read_bytes()
        vault.close()

    def test_apply_reads_armored_files(self, vault, adjustments, monkeypatch):
        _write_rules(adjustments, ("Netflix", "streaming"))
        _write_exceptions(adjustments, "checking,2026-02-14,CHECK # 1145,-500.0,,gifts")
        _typed(monkeypatch, "pw", "pw")
        encrypt_file(_rules_file(adjustments))
        encrypt_file(_exceptions_file(adjustments))
        orphans = vault.apply_categories()
        assert orphans.empty
        cats = _categories_of(vault)
        assert cats["Netflix"] == "streaming"
        assert cats["CHECK # 1145"] == "gifts"

    def test_setters_keep_files_armored(self, vault, adjustments, monkeypatch):
        _typed(monkeypatch, "pw", "pw")
        encrypt_file(_rules_file(adjustments))
        encrypt_file(_exceptions_file(adjustments))
        vault.set_category_rule("Netflix", "streaming")
        vault.set_category_exception(
            "checking",
            "2026-02-14",
            "CHECK # 1145",
            -500.0,
            "gifts",
        )
        assert _rules_file(adjustments).read_bytes().startswith(b"GVENC")
        assert _exceptions_file(adjustments).read_bytes().startswith(b"GVENC")
        # plaintext never on disk
        assert b"Netflix" not in _rules_file(adjustments).read_bytes()
        cats = _categories_of(vault)
        assert cats["Netflix"] == "streaming"
        assert cats["CHECK # 1145"] == "gifts"


class TestSchema:
    def test_new_tables_and_column_exist(self, tmp_path):
        with Vault.create(tmp_path / "V.db"):
            pass
        conn = sqlite3.connect(tmp_path / "V.db")
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "categories" in tables
        columns = [r[1] for r in conn.execute("PRAGMA table_info(transactions)")]
        assert "category_id" in columns
        conn.close()

    def test_named_column_writers_still_work(self, vault, tmp_path):
        # The goblinvest web UI inserts with named columns and no category_id;
        # that must keep working against the new schema.
        db_path = next(tmp_path.glob("*.db"))
        vault.close()
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            INSERT INTO transactions (account_id, trans_date, trans_desc, amount, asset_id)
            VALUES (1, '2026-04-01', 'legacy writer', -1.0, 1)
            ;"""
        )
        conn.commit()
        conn.close()
