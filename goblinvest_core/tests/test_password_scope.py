import threading

import pandas as pd
import pytest

from goblinvest_core import (
    Vault,
    encrypt_file,
    forget_password,
    password_scope,
    read_encrypted_file,
)
from goblinvest_core import _password


@pytest.fixture
def filepath(tmp_path):
    return tmp_path / "vault.db"


@pytest.fixture(autouse=True)
def forget_between_tests():
    forget_password()
    yield
    forget_password()


@pytest.fixture(autouse=True)
def no_prompt(monkeypatch):
    """Nothing in a password scope may ever reach the terminal prompt."""

    def fail(prompt=""):
        raise AssertionError("password prompt should not have appeared")

    monkeypatch.setattr(_password, "getpass", fail)


def _make(filepath, password):
    with password_scope(password):
        v = Vault.create(filepath, encrypted=True)
        v.add_account("checking")
        v.add_transactions("checking", ["2026-01-05"], ["Coffee"], [-4.5])
        v.close()


def test_scope_creates_and_opens_an_encrypted_vault(filepath):
    _make(filepath, "hunter2")
    assert filepath.read_bytes()[:16] != b"SQLite format 3\x00"
    with password_scope("hunter2"):
        with Vault.open(filepath) as v:
            assert v.list_transactions()["description"].tolist() == ["Coffee"]


def test_scope_does_not_leak_into_the_terminal_cache(filepath):
    _make(filepath, "hunter2")
    assert _password._cache == {}
    with pytest.raises(AssertionError, match="prompt"):
        Vault.open(filepath)


def test_scope_handles_the_encrypted_adjustments_files(filepath):
    _make(filepath, "hunter2")
    scope = password_scope("hunter2")
    with scope:
        with Vault.open(filepath) as v:
            v.add_category("treats")
            v.set_category_rule("Coffee", "treats")
            assert v.list_transactions()["category"].tolist() == ["treats"]
    rules = filepath.parent / "vault_adjustments" / "category_rules.csv"
    assert rules.read_bytes().startswith(b"GVENC")


def test_scope_reads_encrypted_files(tmp_path):
    csv = tmp_path / "s.csv"
    csv.write_text("a,b\n1,2\n")
    with password_scope("pw"):
        encrypt_file(csv)
        assert pd.read_csv(read_encrypted_file(csv))["b"].tolist() == [2]


def test_wrong_password_raises_and_empties_the_scope(filepath):
    _make(filepath, "hunter2")
    scope = password_scope("wrong")
    with scope:
        with pytest.raises(ValueError, match="wrong password"):
            Vault.open(filepath)
    with scope:
        with pytest.raises(ValueError, match="forgotten"):
            Vault.open(filepath)


def test_empty_password_is_refused():
    with pytest.raises(ValueError, match="empty"):
        password_scope("")


def test_scope_is_reusable_and_keeps_the_vault_key(filepath):
    _make(filepath, "hunter2")
    scope = password_scope("hunter2")
    for _ in range(3):
        with scope:
            Vault.open(filepath).close()
    assert len(scope._room["vault_keys"]) == 1


def test_repeat_open_skips_key_derivation(filepath, monkeypatch):
    _make(filepath, "hunter2")
    scope = password_scope("hunter2")
    with scope:
        Vault.open(filepath).close()

    monkeypatch.setattr("hashlib.pbkdf2_hmac", lambda *a, **k: pytest.fail("derived again"))
    with scope:
        with Vault.open(filepath) as v:
            assert len(v.list_transactions()) == 1


def test_raw_key_matches_sqlcipher_passphrase_derivation(filepath):
    """If SQLCipher's defaults ever drift from ours, the raw key would stop
    working and every open would quietly pay for the fallback."""
    _make(filepath, "hunter2")
    with password_scope("hunter2"):
        key = _password._get_vault_key(filepath.read_bytes()[:16])
    from goblinvest_core.vault import _connect

    _connect(filepath, None, raw_key=key).close()


def test_stale_raw_key_falls_back_to_the_passphrase(filepath):
    _make(filepath, "hunter2")
    scope = password_scope("hunter2")
    salt = filepath.read_bytes()[:16]
    scope._room["vault_keys"] = {salt: b"\x00" * 32}
    with scope:
        Vault.open(filepath).close()
    assert scope._room["password"] == "hunter2"


def test_scopes_nest_and_restore(tmp_path):
    a, b = tmp_path / "a.db", tmp_path / "b.db"
    _make(a, "alpha")
    _make(b, "bravo")
    with password_scope("alpha"):
        with password_scope("bravo"):
            Vault.open(b).close()
        Vault.open(a).close()


def test_threads_use_their_own_scopes(tmp_path):
    paths = {pw: tmp_path / f"{pw}.db" for pw in ("alpha", "bravo", "charlie")}
    for pw, path in paths.items():
        _make(path, pw)
    scopes = {pw: password_scope(pw) for pw in paths}
    errors = []
    barrier = threading.Barrier(len(paths))

    def work(pw):
        try:
            with scopes[pw]:
                barrier.wait()
                for _ in range(3):
                    with Vault.open(paths[pw]) as v:
                        assert len(v.list_transactions()) == 1
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=work, args=(pw,)) for pw in paths]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []


def test_forget_password_inside_a_scope_forgets_the_scope(filepath):
    _make(filepath, "hunter2")
    scope = password_scope("hunter2")
    with scope:
        forget_password()
        with pytest.raises(ValueError, match="forgotten"):
            Vault.open(filepath)


def test_terminal_cache_still_works_outside_scopes(filepath, monkeypatch):
    _make(filepath, "hunter2")
    entries = iter(["hunter2"])
    monkeypatch.setattr(_password, "getpass", lambda prompt="": next(entries))
    Vault.open(filepath).close()
    Vault.open(filepath).close()  # remembered: no second prompt
    assert _password._cache["password"] == "hunter2"
