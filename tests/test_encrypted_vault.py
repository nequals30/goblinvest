"""Encrypted vaults end to end: signup, locking, unlocking, and doing real work.

The point of the whole feature is that nothing under `data/users/<id>/` is
readable without the password, so several of these assert on the bytes on disk
rather than on a page.
"""

import re

import pytest

from goblinvest import keyring, storage, vaults

PASSWORD = "hoardgold"


@pytest.fixture
def encrypted(client, cheap_kdf):
    """A signed-in user whose vault and adjustments files are encrypted."""
    response = client.post(
        "/signup", data={"username": "goblin", "password": PASSWORD, "encrypt": "1"}
    )
    assert response.status_code == 200 and response.url.path == "/"
    return client


def head(path, n=16):
    with path.open("rb") as f:
        return f.read(n)


def adjustments_files():
    return sorted(storage.adjustments_dir(1).glob("*.csv"))


# --- what signup writes -------------------------------------------------------


def test_the_vault_file_is_not_a_readable_sqlite_database(encrypted):
    assert head(storage.vault_path(1)) != b"SQLite format 3\x00"
    assert vaults.is_encrypted(1)


def test_every_adjustments_file_is_armored(encrypted):
    files = adjustments_files()
    assert files, "core should have created the adjustments CSVs"
    assert all(head(path, 6) == b"GVENC1" for path in files)


def test_the_password_is_nowhere_on_disk(encrypted):
    """The only copy is in this process's memory."""
    for path in [storage.vault_path(1), *adjustments_files()]:
        assert PASSWORD.encode() not in path.read_bytes()


def test_opting_out_leaves_everything_in_the_clear(client):
    client.post("/signup", data={"username": "goblin", "password": PASSWORD})
    assert head(storage.vault_path(1)) == b"SQLite format 3\x00"
    assert not vaults.needs_password(1)
    assert client.get("/month").status_code == 200


# --- locking and unlocking ----------------------------------------------------


def test_a_fresh_signup_is_already_unlocked(encrypted):
    assert keyring.is_unlocked(1)
    assert encrypted.get("/month").status_code == 200


def test_locking_sends_the_next_page_to_unlock(encrypted):
    encrypted.post("/lock")
    assert not keyring.is_unlocked(1)

    response = encrypted.get("/month?y=2026&mo=5", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/unlock?next=/month?y=2026&mo=5"


def test_the_unlock_page_asks_for_a_password(encrypted):
    encrypted.post("/lock")
    html = encrypted.get("/unlock").text
    assert 'name="password"' in html
    assert 'type="password"' in html


def test_unlocking_returns_to_where_you_were(encrypted):
    encrypted.post("/lock")
    response = encrypted.post(
        "/unlock", data={"password": PASSWORD, "next": "/month"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/month"
    assert keyring.is_unlocked(1)


def test_the_wrong_password_is_refused_and_not_kept(encrypted):
    encrypted.post("/lock")
    response = encrypted.post("/unlock", data={"password": "not it", "next": "/month"})
    assert response.status_code == 400
    assert "doesn&#39;t open this vault" in response.text
    assert not keyring.is_unlocked(1)


def test_unlocking_an_unlocked_vault_just_goes_there(encrypted):
    response = encrypted.get("/unlock?next=/month", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/month"


def test_next_cannot_leave_the_site(encrypted):
    encrypted.post("/lock")
    response = encrypted.post(
        "/unlock",
        data={"password": PASSWORD, "next": "//evil.example/steal"},
        follow_redirects=False,
    )
    assert response.headers["location"] == "/"


def test_an_idle_vault_relocks(encrypted, monkeypatch):
    from goblinvest.config import settings

    monkeypatch.setenv("GV_VAULT_TTL_MINUTES", "0")
    settings.cache_clear()
    keyring.remember(1, PASSWORD)  # remembered with a zero-length lifetime

    assert not keyring.is_unlocked(1)
    assert encrypted.get("/month", follow_redirects=False).status_code == 303


def test_a_locked_vault_never_reaches_for_a_terminal(encrypted):
    """The backstop: if some path got to core without a password in place, it
    must raise rather than block on a getpass that has no terminal to read."""
    keyring.forget_all()
    with pytest.raises(keyring.VaultLocked), vaults.open_vault(1):
        pass

    from goblinvest_core import _password

    with pytest.raises(keyring.VaultLocked):
        _password.getpass("Enter password: ")


# --- the account password vs. the vault password ------------------------------


def test_logging_in_again_unlocks_the_vault(encrypted):
    encrypted.post("/logout")
    assert not keyring.is_unlocked(1)

    encrypted.post("/login", data={"username": "goblin", "password": PASSWORD})
    assert keyring.is_unlocked(1)
    assert encrypted.get("/month").status_code == 200


def test_logging_out_locks_the_vault(encrypted):
    encrypted.post("/logout")
    assert not keyring.is_unlocked(1)


def test_a_changed_account_password_leaves_the_vault_on_the_old_one(encrypted):
    """Core has no call that re-keys a vault (deferred, see CLAUDE.md), so the
    two drift apart and the vault keeps the password it was made with."""
    response = encrypted.post(
        "/settings/account/password",
        data={
            "current_password": PASSWORD,
            "new_password": "newhoard1",
            "confirm_password": "newhoard1",
        },
    )
    assert response.status_code == 200
    encrypted.post("/logout")

    encrypted.post("/login", data={"username": "goblin", "password": "newhoard1"})
    assert not keyring.is_unlocked(1)  # the new password doesn't open it

    assert (
        encrypted.post(
            "/unlock", data={"password": PASSWORD, "next": "/"}, follow_redirects=False
        ).headers["location"]
        == "/"
    )
    assert keyring.is_unlocked(1)


def test_the_account_page_says_the_vault_keeps_its_password(encrypted):
    assert "this doesn't change it" in encrypted.get("/settings/account").text


# --- more than one user -------------------------------------------------------


def test_each_user_gets_their_own_password(client, cheap_kdf):
    client.post("/signup", data={"username": "one", "password": "password-one", "encrypt": "1"})
    client.post("/logout")
    client.post("/signup", data={"username": "two", "password": "password-two", "encrypt": "1"})

    assert keyring.is_unlocked(2) and not keyring.is_unlocked(1)
    assert client.get("/month").status_code == 200

    # User one's password must not open user two's vault, and vice versa.
    assert not vaults.unlock(1, "password-two")
    assert vaults.unlock(1, "password-one")
    assert client.get("/month").status_code == 200  # two is still fine


# --- an encrypted adjustments folder on a plain vault -------------------------


def test_armored_adjustments_alone_still_need_the_password(client, cheap_kdf):
    """The two encryptions are separate questions. A plain vault opens without
    a password, but reading a category rule out of an armored file doesn't."""
    client.post("/signup", data={"username": "goblin", "password": PASSWORD})
    assert not vaults.needs_password(1)

    from goblinvest_core import encrypt_file

    keyring.remember(1, "files-only")
    with keyring.borrow(1):
        for path in adjustments_files():
            encrypt_file(path)
    keyring.forget_all()

    assert not vaults.is_encrypted(1)
    assert vaults.needs_password(1)
    assert client.get("/settings", follow_redirects=False).status_code == 200
    assert client.post("/settings/adjustments/apply", follow_redirects=False).status_code == 303

    assert not vaults.unlock(1, "wrong")
    assert vaults.unlock(1, "files-only")
    response = client.post("/settings/adjustments/apply", follow_redirects=True)
    assert "Adjustments applied." in response.text


# --- real work through the encryption -----------------------------------------


def test_a_whole_months_work_survives_encryption(encrypted):
    """Everything the app does to a vault, done to an encrypted one: the ledger
    lives in SQLCipher and every decision about it in an armored CSV."""
    with vaults.open_vault(1) as v:
        v.add_account("checking", account_group_name="cash")
        v.add_transactions(
            "checking",
            ["2026-05-02", "2026-05-19", "2026-05-20"],
            ["Whole Foods", "Whole Foods", "Rent"],
            [-82.50, -31.00, -2000.00],
        )

    # define a category (writes categories.csv, armored)
    assert (
        encrypted.post("/settings/vault/categories/add", data={"name": "groceries"}).status_code
        == 200
    )

    month = encrypted.get("/month?y=2026&mo=5")
    assert month.status_code == 200
    assert "Whole Foods" in month.text

    with vaults.open_vault(1) as v:
        row = v.list_transactions()
        row = row[row["description"] == "Rent"].iloc[0]
        key = {
            "account": row["account_name"],
            "date": row["original_date"].date().isoformat(),
            "description": row["description"],
            "amount": float(row["amount"]),
            "asset": row["asset"],
        }

    # a rule (category_rules.csv) and an effective date (effective_dates.csv)
    encrypted.post(
        "/month/categorize",
        data={**key, "y": 2026, "mo": 5, "scope": "all", "category": "groceries"},
    )
    encrypted.post(
        "/month/set-date",
        data={**key, "y": 2026, "mo": 5, "scope": "set", "effective": "2026-05-25"},
    )

    applied = encrypted.post("/settings/adjustments/apply", follow_redirects=True)
    assert "Adjustments applied." in applied.text

    with vaults.open_vault(1) as v:
        frame = v.list_transactions()
    by_description = dict(zip(frame["description"], frame["category"]))
    assert by_description["Rent"] == "groceries"
    moved = frame[frame["description"] == "Rent"].iloc[0]
    assert moved["date"].date().isoformat() == "2026-05-25"

    # and none of it is readable on disk
    for path in adjustments_files():
        assert head(path, 6) == b"GVENC1"
    assert b"Whole Foods" not in storage.vault_path(1).read_bytes()


def test_the_settings_page_reports_the_lock_state(encrypted):
    html = encrypted.get("/settings").text
    assert "are encrypted" in html and "unlocked" in html
    assert 'action="/lock"' in html

    encrypted.post("/lock")
    html = encrypted.get("/settings").text
    assert "locked" in html
    assert 'href="/unlock?next=/settings"' in html


def test_concurrent_requests_against_one_encrypted_vault(encrypted):
    """Core's password scopes are per-thread, and nothing here serializes
    vault work any more, so overlapping requests must each find the password
    in force on their own thread."""
    from concurrent.futures import ThreadPoolExecutor

    with vaults.open_vault(1) as v:
        v.add_account("checking")
        v.add_transactions("checking", ["2026-05-02"], ["Whole Foods"], [-82.50])

    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(lambda _: encrypted.get("/month?y=2026&mo=5"), range(16)))

    assert [r.status_code for r in responses] == [200] * 16
    assert all(len(re.findall(r"Whole Foods", r.text)) >= 1 for r in responses)


def test_two_users_vaults_open_at_the_same_time(client, cheap_kdf):
    """Two different passwords in force on two threads at once — what the old
    single global cache couldn't do without a lock."""
    import threading

    for uid in (1, 2):
        storage.provision_user_storage(uid)
        vaults.create_vault(uid, password=f"password-{uid}")

    both_inside = threading.Barrier(2, timeout=10)
    seen = {}

    def work(uid):
        with vaults.open_vault(uid) as v:
            both_inside.wait()  # neither leaves until the other is in too
            seen[uid] = v.list_accounts()

    threads = [threading.Thread(target=work, args=(uid,)) for uid in (1, 2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert set(seen) == {1, 2}


# --- core errors vs. a password that stopped working ---------------------------


def test_a_core_complaint_does_not_lock_the_vault(encrypted):
    """Core says "that name is reserved" with a ValueError, the same type it
    uses for a wrong password. Only the second should drop the password."""
    response = encrypted.post("/settings/vault/categories/add", data={"name": "unclassified"})
    assert response.status_code == 400
    assert keyring.is_unlocked(1)


def test_a_password_core_has_forgotten_is_dropped(encrypted):
    """If core decides the password in force is wrong it forgets it, and every
    later use raises. The next page must ask again rather than fail forever."""
    from goblinvest_core import forget_password

    with pytest.raises(ValueError), vaults.open_vault(1) as v:
        forget_password()  # what core does to a scope on a failed decrypt
        v.apply_categories()  # reads the armored adjustments: needs the password
    assert not keyring.is_unlocked(1)
    assert encrypted.get("/month", follow_redirects=False).status_code == 303
