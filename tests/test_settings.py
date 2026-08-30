"""The settings menu and account page."""

import pytest

from goblinvest import storage, vaults

ACCOUNT_URL = "/settings/account"
PASSWORD_URL = "/settings/account/password"
APPLY_URL = "/settings/adjustments/apply"


def change(client, current="hoardgold", new="newhoard1", confirm=None):
    return client.post(
        PASSWORD_URL,
        data={
            "current_password": current,
            "new_password": new,
            "confirm_password": new if confirm is None else confirm,
        },
    )


def test_settings_menu_links_to_both_sections(signed_up):
    html = signed_up.get("/settings").text
    assert 'href="/settings/account"' in html
    assert 'href="/settings/nav"' in html


def test_settings_is_active_in_the_pane(signed_up):
    assert 'is-active"\n             href="/settings"' in signed_up.get(ACCOUNT_URL).text


def test_password_change_takes_effect(signed_up):
    r = change(signed_up)
    assert r.status_code == 200 and r.url.path == ACCOUNT_URL
    assert "Password changed" in r.text

    signed_up.post("/logout")
    assert (
        signed_up.post("/login", data={"username": "goblin", "password": "hoardgold"}).status_code
        == 401
    )
    r = signed_up.post("/login", data={"username": "goblin", "password": "newhoard1"})
    assert r.status_code == 200 and r.url.path == "/"


def test_wrong_current_password_is_rejected(signed_up):
    r = change(signed_up, current="wrongone")
    assert r.status_code == 400 and "Current password is incorrect." in r.text
    assert signed_up.get("/").status_code == 200  # still signed in


def test_mismatched_confirmation_is_rejected(signed_up):
    r = change(signed_up, new="newhoard1", confirm="newhoard2")
    assert r.status_code == 400 and "don&#39;t match" in r.text


def test_short_password_is_rejected(signed_up):
    r = change(signed_up, new="short")
    assert r.status_code == 400 and "at least 8 characters" in r.text


def test_reused_password_is_rejected(signed_up):
    r = change(signed_up, new="hoardgold")
    assert r.status_code == 400 and "must differ" in r.text


def test_password_change_keeps_this_session_and_drops_others(client):
    client.post("/signup", data={"username": "goblin", "password": "hoardgold"})
    stale = client.cookies.get("gv_session")

    client.post("/logout")
    client.post("/login", data={"username": "goblin", "password": "hoardgold"})
    change(client)

    assert client.get("/").status_code == 200  # the session that changed it survives

    client.cookies.set("gv_session", stale)
    assert client.get("/", follow_redirects=False).status_code == 303


def test_account_page_requires_login(client):
    r = client.post(PASSWORD_URL, data={}, follow_redirects=False)
    assert r.status_code in (303, 422)


# --- apply all adjustments ----------------------------------------------------
#
# One button for every adjustments file: it used to be "apply categories" on the
# categories page, and moved here when effective dates joined them (user,
# 2026-08-30).


@pytest.fixture
def ledger(signed_up):
    """A vault whose adjustments files are worth re-applying."""
    with vaults.open_vault(1) as v:
        v.add_account("checking", account_group_name="cash")
        v.add_category("groceries")
        v.add_transactions(
            "checking",
            ["2026-05-02", "2026-05-20"],
            ["Whole Foods", "Rent"],
            [-82.50, -2000.00],
        )
    return signed_up


def categories_of():
    with vaults.open_vault(1) as v:
        frame = v.list_transactions()
    return dict(zip(frame["description"], frame["category"]))


def test_the_settings_menu_offers_it(ledger):
    assert f'action="{APPLY_URL}"' in ledger.get("/settings").text
    assert "apply all adjustments" in ledger.get("/settings").text


def test_apply_picks_up_a_hand_edited_rules_file(ledger):
    """The adjustments files are the user's own and get edited outside this
    app; the button is what makes the vault match them again."""
    rules = storage.adjustments_dir(1) / "category_rules.csv"
    rules.write_text(rules.read_text() + "Rent,groceries\n")
    assert categories_of()["Rent"] == "unclassified"

    r = ledger.post(APPLY_URL)
    assert r.status_code == 200 and r.url.path == "/settings"
    assert "Adjustments applied." in r.text
    assert categories_of()["Rent"] == "groceries"


def test_apply_runs_the_effective_dates_too(ledger):
    """The reason it isn't on the categories page any more."""
    moves = storage.adjustments_dir(1) / "effective_dates.csv"
    moves.write_text(
        "account,date,description,amount,asset,effective_date\n"
        "checking,2026-05-02,Whole Foods,-82.50,,2026-04-28\n"
    )
    r = ledger.post(APPLY_URL)
    assert r.status_code == 200
    with vaults.open_vault(1) as v:
        frame = v.list_transactions()
    moved = frame[frame["description"] == "Whole Foods"].iloc[0]
    assert moved["date"].date().isoformat() == "2026-04-28"


def test_apply_answers_303_so_a_refresh_does_not_repost(ledger):
    r = ledger.post(APPLY_URL, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/settings?applied=1&cats=0&dates=0"


def test_apply_reports_file_rows_that_matched_nothing(ledger):
    """A file row outlives the transaction it names — core hands those back
    rather than failing silently, so the page says how many, per file."""
    with vaults.open_vault(1) as v:
        v.set_category_exception("checking", "2026-05-20", "Rent", -2000.00, "groceries")
        v.set_effective_date("checking", "2026-05-02", "Whole Foods", -82.50, "2026-04-28")
        v.delete_account("checking", confirm=False)

    r = ledger.post(APPLY_URL)
    assert "1 exception row and 1 date row matched no transaction." in r.text


def test_apply_shows_core_complaints_rather_than_raising(ledger):
    rules = storage.adjustments_dir(1) / "category_rules.csv"
    rules.write_text(rules.read_text() + "Rent,not-a-category\n")
    r = ledger.post(APPLY_URL)
    assert r.status_code == 400 and "not-a-category" in r.text


def test_apply_with_no_vault_goes_back_to_the_menu(signed_up):
    storage.vault_path(1).unlink()
    r = signed_up.post(APPLY_URL, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/settings"
