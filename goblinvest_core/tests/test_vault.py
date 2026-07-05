import sqlite3
import time

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
    with Vault.create(filepath, default_asset="EUR") as v:
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
