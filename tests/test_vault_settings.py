"""Viewing, adding and deleting accounts, assets and categories, against a real
goblinvest_core vault."""

import re

import pytest

from goblinvest import vaults

ACCOUNTS = "/settings/vault/accounts"
ASSETS = "/settings/vault/assets"
CATEGORIES = "/settings/vault/categories"


@pytest.fixture
def stocked(signed_up):
    """User 1's vault with two accounts, an asset, a category, and transactions
    that a delete can take with it."""
    with vaults.open_vault(1) as v:
        v.add_account("checking", account_group_name="cash")
        v.add_account("joint-checking", ownership_share=0.5, account_group_name="cash")
        v.add_asset("VFIAX")
        v.add_category("groceries")
        v.add_transactions(
            "checking",
            ["2026-05-02", "2026-05-20"],
            ["Whole Foods", "Rent"],
            [-82.50, -2000.00],
        )
        v.add_transactions("checking", ["2026-05-17"], ["Buy VFIAX"], [1.5], assets=["VFIAX"])
        v.set_category_rule("Whole Foods", "groceries")
    return signed_up


def body_rows(html):
    return re.findall(r"<tr>(.*?)</tr>", html.split("<tbody>")[1].split("</tbody>")[0], re.DOTALL)


def names(vault, kind):
    frame = getattr(vault, f"list_{kind}")()
    return list(frame[frame.columns[1]])


# --- viewing -----------------------------------------------------------------


def test_settings_menu_links_to_all_three(signed_up):
    html = signed_up.get("/settings").text
    for url in (ACCOUNTS, ASSETS, CATEGORIES):
        assert f'href="{url}"' in html


@pytest.mark.parametrize("url", [ACCOUNTS, ASSETS, CATEGORIES])
def test_pages_require_login(client, url):
    r = client.get(url, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_accounts_page_lists_name_group_and_share(stocked):
    rows = body_rows(stocked.get(ACCOUNTS).text)
    assert len(rows) == 2
    assert "checking" in rows[0] and "cash" in rows[0]
    assert "joint-checking" in rows[1] and "0.5" in rows[1]


def test_assets_page_lists_the_base_currency_and_the_rest(stocked):
    html = stocked.get(ASSETS).text
    assert "USD" in html and "base currency" in html
    assert "VFIAX" in html


def test_categories_page_shows_transaction_counts(stocked):
    rows = body_rows(stocked.get(CATEGORIES).text)
    assert len(rows) == 1
    assert "groceries" in rows[0]
    assert ">1</td>" in rows[0].replace(" ", "")  # the one rule matched one row


def test_an_unknown_kind_is_not_a_page(signed_up):
    assert signed_up.get("/settings/vault/pancakes").status_code == 422


def test_missing_vault_is_handled(signed_up):
    from goblinvest import storage

    storage.vault_path(1).unlink()
    r = signed_up.get(ACCOUNTS)
    assert r.status_code == 200 and "No vault yet" in r.text


# --- adding ------------------------------------------------------------------


def test_add_account(stocked):
    r = stocked.post(f"{ACCOUNTS}/add", data={"name": "brokerage", "group": "investments"})
    assert r.status_code == 200 and r.url.path == ACCOUNTS
    with vaults.open_vault(1) as v:
        assert "brokerage" in names(v, "accounts")
    assert "investments" in stocked.get(ACCOUNTS).text


def test_add_account_keeps_the_ownership_share(stocked):
    stocked.post(f"{ACCOUNTS}/add", data={"name": "ours", "group": "cash", "share": "0.25"})
    with vaults.open_vault(1) as v:
        accounts = v.list_accounts()
        share = accounts.loc[accounts["account_name"] == "ours", "ownership_share"].iloc[0]
    assert float(share) == 0.25


def test_add_account_with_a_nonsense_share_is_rejected(stocked):
    r = stocked.post(f"{ACCOUNTS}/add", data={"name": "ours", "share": "half"})
    assert r.status_code == 400 and "must be a number" in r.text
    with vaults.open_vault(1) as v:
        assert "ours" not in names(v, "accounts")


def test_add_asset(stocked):
    stocked.post(f"{ASSETS}/add", data={"name": "VTI"})
    with vaults.open_vault(1) as v:
        assert "VTI" in names(v, "assets")


def test_add_category(stocked):
    r = stocked.post(f"{CATEGORIES}/add", data={"name": "rent"})
    with vaults.open_vault(1) as v:
        assert "rent" in names(v, "categories")
    # It's defined in the adjustments file; only apply_categories puts it in the
    # vault table the page reads, so this is what pins that call.
    assert "rent" in r.text


def test_a_blank_name_is_rejected(stocked):
    r = stocked.post(f"{ASSETS}/add", data={"name": "   "})
    assert r.status_code == 400 and "Give the asset a name." in r.text


def test_core_complaints_are_shown_not_raised(stocked):
    """ "unclassified" is reserved — core's message reaches the page."""
    r = stocked.post(f"{CATEGORIES}/add", data={"name": "unclassified"})
    assert r.status_code == 400
    assert "reserved" in r.text


# --- deleting ----------------------------------------------------------------


def test_the_list_links_to_a_confirmation_page_not_a_delete(stocked):
    html = stocked.get(ACCOUNTS).text
    assert f'href="{ACCOUNTS}/delete?name=checking"' in html
    assert "<form" not in html.split("<tbody>")[1].split("</tbody>")[0]


def test_confirmation_page_warns_before_anything_happens(stocked):
    r = stocked.get(f"{ACCOUNTS}/delete?name=checking")
    assert r.status_code == 200
    assert "cannot be undone" in r.text
    assert "Every transaction in this account is deleted with it." in r.text
    assert f'<form method="post" action="{ACCOUNTS}/delete"' in r.text
    with vaults.open_vault(1) as v:  # still there — a GET changes nothing
        assert "checking" in names(v, "accounts")


def test_the_category_warning_counts_what_it_affects(stocked):
    html = stocked.get(f"{CATEGORIES}/delete?name=groceries").text
    assert "1 transaction in it right now." in html
    assert "become" in html and "unclassified" in html


def test_deleting_an_account_takes_its_transactions(stocked):
    r = stocked.post(f"{ACCOUNTS}/delete", data={"name": "checking"})
    assert r.status_code == 200 and r.url.path == ACCOUNTS
    assert "Deleted account" in r.text
    with vaults.open_vault(1) as v:
        assert "checking" not in names(v, "accounts")
        assert v.list_transactions().empty


def test_deleting_an_asset_takes_its_transactions(stocked):
    stocked.post(f"{ASSETS}/delete", data={"name": "VFIAX"})
    with vaults.open_vault(1) as v:
        assert "VFIAX" not in names(v, "assets")
        assert "Buy VFIAX" not in list(v.list_transactions()["description"])


def test_deleting_a_category_keeps_the_transactions(stocked):
    stocked.post(f"{CATEGORIES}/delete", data={"name": "groceries"})
    with vaults.open_vault(1) as v:
        assert "groceries" not in names(v, "categories")
        transactions = v.list_transactions()
        assert len(transactions) == 3
        assert set(transactions["category"]) == {"unclassified"}


def test_the_base_currency_cannot_be_deleted(stocked):
    html = stocked.get(ASSETS).text
    assert f"{ASSETS}/delete?name=USD" not in html  # no link offered

    r = stocked.get(f"{ASSETS}/delete?name=USD", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == ASSETS  # no warning page either

    r = stocked.post(f"{ASSETS}/delete", data={"name": "USD"})  # and not by hand
    assert r.status_code == 400
    with vaults.open_vault(1) as v:
        assert "USD" in names(v, "assets")


def test_confirming_an_unknown_name_goes_back_to_the_list(stocked):
    r = stocked.get(f"{ACCOUNTS}/delete?name=nope", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == ACCOUNTS


def test_deleting_an_unknown_name_says_so(stocked):
    r = stocked.post(f"{ACCOUNTS}/delete", data={"name": "nope"})
    assert r.status_code == 400 and "not registered" in r.text


def test_names_match_the_way_core_matches_them(stocked):
    assert stocked.get(f"{ACCOUNTS}/delete?name=CHECKING").status_code == 200
    stocked.post(f"{ACCOUNTS}/delete", data={"name": "CHECKING"})
    with vaults.open_vault(1) as v:
        assert "checking" not in names(v, "accounts")


def test_delete_answers_303_so_a_refresh_does_not_repost(stocked):
    r = stocked.post(f"{ACCOUNTS}/delete", data={"name": "checking"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith(ACCOUNTS)
