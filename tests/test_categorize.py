"""Categorizing from the Month View table, against a real goblinvest_core vault.

Every control here is a plain form post — the assertions about markup are there
to keep it that way.
"""

import re

import pytest

from goblinvest import vaults

MONTH = "/month?y=2026&mo=5"


@pytest.fixture
def ledger(signed_up):
    """A month with two look-alike transactions and one of another kind."""
    with vaults.open_vault(1) as v:
        v.add_account("checking", account_group_name="cash")
        v.add_account("brokerage", account_group_name="investments")
        v.add_asset("VFIAX")
        v.add_category("groceries")
        v.add_category("rent")
        v.add_transactions(
            "checking",
            ["2026-05-02", "2026-05-19", "2026-05-20"],
            ["Whole Foods", "Whole Foods", "Rent"],
            [-82.50, -31.00, -2000.00],
        )
        v.add_transactions("brokerage", ["2026-05-17"], ["Buy VFIAX"], [1.5], assets=["VFIAX"])
        v.apply_categories()  # defined categories reach the vault table on apply
    return signed_up


def categories(client=None):
    """Every transaction's category, keyed by description."""
    with vaults.open_vault(1) as v:
        frame = v.list_transactions()
    return dict(zip(frame["description"], frame["category"]))


def amounts_to_categories():
    with vaults.open_vault(1) as v:
        frame = v.list_transactions()
    return dict(zip(frame["amount"], frame["category"]))


def cell(html, description):
    """The <td> of the category column for one row."""
    row = [r for r in re.findall(r"<tr>(.*?)</tr>", html, re.DOTALL) if f">{description}<" in r]
    assert row, f"no row for {description}"
    return row[0].split('<td class="cat"')[1]


def post(client, description, category, scope, *, follow_redirects=True, **overrides):
    """Submit one row's category form the way the browser would."""
    with vaults.open_vault(1) as v:
        frame = v.list_transactions()
    row = frame[frame["description"] == description].iloc[0]
    data = {
        "y": 2026,
        "mo": 5,
        "scope": scope,
        "category": category,
        "account": row["account_name"],
        "date": row["date"].date().isoformat(),
        "description": row["description"],
        "amount": float(row["amount"]),
        "asset": row["asset"],
    }
    return client.post(
        "/month/categorize", data={**data, **overrides}, follow_redirects=follow_redirects
    )


# --- what the page offers ----------------------------------------------------


def transaction_id(description):
    with vaults.open_vault(1) as v:
        frame = v.list_transactions()
    return int(frame.loc[frame["description"] == description, "transaction_id"].iloc[0])


def test_every_unclassified_row_has_a_form_with_both_scopes(ledger):
    html = ledger.get(MONTH).text
    assert html.count('action="/month/categorize"') == 4  # all four start unclassified
    assert 'name="scope" value="all"' in html
    assert 'name="scope" value="one"' in html
    assert "<script" not in html.split("<tbody>")[1].split("</tbody>")[0]  # no JS in the cell


def test_the_dropdown_offers_every_defined_category(ledger):
    options = cell(ledger.get(MONTH).text, "Rent")
    assert '<option value="groceries"' in options
    assert '<option value="rent"' in options


def test_an_uncategorized_row_starts_unset(ledger):
    options = cell(ledger.get(MONTH).text, "Rent")
    assert '<option value="unclassified" selected>unclassified</option>' in options
    assert 'class="unset"' in options


def test_a_classified_row_is_just_its_category_and_a_pencil(ledger):
    post(ledger, "Rent", "rent", "one")
    settled = cell(ledger.get(MONTH).text, "Rent")
    assert '<span class="cat-name">rent</span>' in settled
    assert "<select" not in settled  # no dropdown until it's asked for
    assert f"edit={transaction_id('Rent')}" in settled

    # the rows still to do keep theirs
    assert "<select" in cell(ledger.get(MONTH).text, "Whole Foods")


def test_the_pencil_opens_the_editor_for_that_row_only(ledger):
    post(ledger, "Rent", "rent", "one")
    post(ledger, "Buy VFIAX", "groceries", "one")
    tid = transaction_id("Rent")

    html = ledger.get(f"{MONTH}&edit={tid}").text
    editing = cell(html, "Rent")
    assert "<select" in editing
    assert '<option value="rent" selected>' in editing  # its category, preselected
    # "unclassified" is offered but not chosen: it's how the category comes off.
    assert '<option value="unclassified">' in editing
    assert f'name="tid" value="{tid}"' in editing
    assert "<select" not in cell(html, "Buy VFIAX")  # the other settled row is untouched


def test_the_editor_stays_open_when_the_answer_is_an_error(ledger):
    post(ledger, "Rent", "rent", "one")
    tid = transaction_id("Rent")
    r = post(ledger, "Rent", "hovercraft", "all", tid=tid)
    assert r.status_code == 400
    assert "<select" in cell(r.text, "Rent")


def test_the_filter_rides_on_the_form_action_and_comes_back(ledger):
    """Filter, categorize, and you're still looking at the same filtered list —
    the picks are on the action URL rather than in a hidden field per row."""
    html = ledger.get(f"{MONTH}&acct=checking").text
    assert 'action="/month/categorize?acct=checking"' in html

    tid = transaction_id("Rent")
    r = ledger.post(
        "/month/categorize?acct=checking",
        data={
            "y": 2026,
            "mo": 5,
            "tid": tid,
            "scope": "one",
            "category": "rent",
            "account": "checking",
            "date": "2026-05-20",
            "description": "Rent",
            "amount": -2000.0,
            "asset": "USD",
        },
        follow_redirects=False,
    )
    assert r.headers["location"] == f"/month?y=2026&mo=5&acct=checking#t{tid}"
    assert categories()["Rent"] == "rent"


def test_an_error_while_filtering_keeps_the_filter(ledger):
    r = ledger.post(
        "/month/categorize?acct=checking",
        data={
            "y": 2026,
            "mo": 5,
            "scope": "all",
            "category": "",
            "account": "checking",
            "date": "2026-05-20",
            "description": "Rent",
            "amount": -2000.0,
            "asset": "USD",
        },
    )
    assert r.status_code == 400 and "Pick a category first." in r.text
    assert "Buy VFIAX" not in r.text  # still the filtered list


def test_submitting_lands_back_on_the_row(ledger):
    tid = transaction_id("Rent")
    r = post(ledger, "Rent", "rent", "one", tid=tid, follow_redirects=False)
    assert r.headers["location"] == f"/month?y=2026&mo=5#t{tid}"


def test_the_category_column_stays_sortable(ledger):
    """The <select> would otherwise sort as the text of all its options."""
    html = ledger.get(MONTH).text
    assert 'data-sort="unclassified"' in cell(html, "Rent")
    post(ledger, "Rent", "rent", "all")
    assert 'data-sort="rent"' in cell(ledger.get(MONTH).text, "Rent")


def test_the_buttons_are_off_when_nothing_is_defined_to_pick(signed_up):
    with vaults.open_vault(1) as v:
        v.add_account("checking")
        v.add_transactions("checking", ["2026-05-02"], ["Whole Foods"], [-82.50])
    html = signed_up.get(MONTH).text
    assert 'value="all" disabled' in html.replace("\n", " ").replace("  ", " ")


# --- (A) every transaction with this description -----------------------------


def test_all_categorizes_every_look_alike(ledger):
    r = post(ledger, "Whole Foods", "groceries", "all")
    assert r.status_code == 200 and r.url.path == "/month"
    assert categories()["Whole Foods"] == "groceries"
    # Both rows with that description, not just the one clicked.
    assert set(amounts_to_categories().values()) == {"groceries", "unclassified"}
    assert amounts_to_categories()[-31.00] == "groceries"


def test_a_rule_survives_a_reload_of_the_statements(ledger):
    """It's written to the adjustments file, so re-applying keeps it."""
    post(ledger, "Whole Foods", "groceries", "all")
    with vaults.open_vault(1) as v:
        v.add_transactions("checking", ["2026-05-28"], ["Whole Foods"], [-12.00])
        v.apply_categories()
    assert amounts_to_categories()[-12.00] == "groceries"


# --- (B) just this transaction -----------------------------------------------


def test_this_categorizes_only_the_one_row(ledger):
    post(ledger, "Whole Foods", "groceries", "one")
    by_amount = amounts_to_categories()
    assert by_amount[-82.50] == "groceries"
    assert by_amount[-31.00] == "unclassified"  # its look-alike is untouched


def test_an_exception_beats_a_later_rule(ledger):
    post(ledger, "Whole Foods", "rent", "one")  # pin the -82.50 row
    post(ledger, "Whole Foods", "groceries", "all")
    by_amount = amounts_to_categories()
    assert by_amount[-82.50] == "rent"
    assert by_amount[-31.00] == "groceries"


def test_a_non_base_currency_row_carries_its_asset(ledger):
    """`assets=None` would mean the base currency to core, and this row is VFIAX."""
    r = post(ledger, "Buy VFIAX", "groceries", "one")
    assert r.status_code == 200
    assert categories()["Buy VFIAX"] == "groceries"


# --- changing an existing category -------------------------------------------


def test_a_category_can_be_changed(ledger):
    post(ledger, "Rent", "groceries", "all")
    assert categories()["Rent"] == "groceries"
    post(ledger, "Rent", "rent", "all")
    assert categories()["Rent"] == "rent"


# --- taking a category back off ----------------------------------------------
#
# Same control, same two buttons: "unclassified" is the option that isn't a
# category, and the route turns it into the None core reads as "remove the rule"
# or "remove the exception".


def test_the_dropdown_always_offers_unclassified(ledger):
    post(ledger, "Rent", "rent", "one")
    tid = transaction_id("Rent")
    assert '<option value="unclassified">' in cell(ledger.get(f"{MONTH}&edit={tid}").text, "Rent")


def test_unclassifying_all_removes_the_rule(ledger):
    post(ledger, "Whole Foods", "groceries", "all")
    assert categories()["Whole Foods"] == "groceries"

    r = post(ledger, "Whole Foods", "unclassified", "all")
    assert r.status_code == 200
    # Both look-alikes, the way the rule reached both of them.
    assert set(amounts_to_categories().values()) == {"unclassified"}


def test_unclassifying_this_removes_only_that_rows_exception(ledger):
    post(ledger, "Whole Foods", "groceries", "all")  # the rule covers both
    post(ledger, "Whole Foods", "rent", "one")  # and this one is pinned
    assert amounts_to_categories()[-82.50] == "rent"

    post(ledger, "Whole Foods", "unclassified", "one")
    by_amount = amounts_to_categories()
    assert by_amount[-82.50] == "groceries"  # back to following the rule
    assert by_amount[-31.00] == "groceries"  # its look-alike never moved


def test_unclassifying_a_row_that_is_pinned_by_an_exception_only(ledger):
    post(ledger, "Buy VFIAX", "groceries", "one")
    post(ledger, "Buy VFIAX", "unclassified", "one")
    assert categories()["Buy VFIAX"] == "unclassified"


def test_the_reserved_name_never_reaches_core_as_a_category(ledger):
    """Core refuses "unclassified" as a category name — the route has to have
    turned it into None before it gets there, not passed it along."""
    r = post(ledger, "Rent", "UNCLASSIFIED", "all")
    assert r.status_code == 200 and "reserved" not in r.text
    assert categories()["Rent"] == "unclassified"


# --- the round trip ----------------------------------------------------------


def test_it_comes_back_to_the_month_it_came_from(ledger):
    r = post(ledger, "Rent", "rent", "all", y=2026, mo=5)
    assert r.status_code == 200
    assert "May" in r.text and "2026" in r.text

    r = ledger.post(
        "/month/categorize",
        data={
            "y": 2026,
            "mo": 5,
            "scope": "all",
            "category": "rent",
            "account": "checking",
            "date": "2026-05-20",
            "description": "Rent",
            "amount": -2000.0,
            "asset": "USD",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303 and r.headers["location"] == "/month?y=2026&mo=5"


def test_submitting_without_picking_says_so(ledger):
    r = post(ledger, "Rent", "", "all")
    assert r.status_code == 400 and "Pick a category first." in r.text
    assert categories()["Rent"] == "unclassified"


def test_an_undefined_category_is_refused_by_core_and_shown(ledger):
    r = post(ledger, "Rent", "hovercraft", "all")
    assert r.status_code == 400 and "not defined" in r.text
    assert categories()["Rent"] == "unclassified"


def test_a_transaction_that_does_not_match_is_refused(ledger):
    r = post(ledger, "Rent", "rent", "one", amount=-9999.0)
    assert r.status_code == 400 and "match" in r.text


def test_categorizing_requires_login(client):
    r = client.post("/month/categorize", data={}, follow_redirects=False)
    assert r.status_code in (303, 422)


def test_a_category_defined_in_settings_reaches_the_month_dropdown(ledger):
    """Defining one is settings' job; the month page only spends them."""
    ledger.post("/settings/vault/categories/add", data={"name": "travel"})
    assert '<option value="travel"' in ledger.get(MONTH).text
    post(ledger, "Rent", "travel", "all")
    assert categories()["Rent"] == "travel"
