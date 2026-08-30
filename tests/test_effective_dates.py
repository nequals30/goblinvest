"""Moving a transaction's date from the Month View table.

A charge that posts in one month and belongs in another counts in the one it
belongs to. Core records the decision; this layer is the date cell, the editor
it opens, and the tint that marks a row that has been moved.
"""

import re

import pytest

from goblinvest import vaults

MAY = "/month?y=2026&mo=5"
APRIL = "/month?y=2026&mo=4"


@pytest.fixture
def ledger(signed_up):
    """One May statement: a charge that really belongs to April, and two others."""
    with vaults.open_vault(1) as v:
        v.add_account("checking", account_group_name="cash")
        v.add_account("brokerage", account_group_name="investments")
        v.add_asset("VFIAX")
        v.add_transactions(
            "checking",
            ["2026-05-02", "2026-05-20"],
            ["Whole Foods", "Rent"],
            [-82.50, -2000.00],
        )
        v.add_transactions("brokerage", ["2026-05-17"], ["Buy VFIAX"], [1.5], assets=["VFIAX"])
    return signed_up


def row(html, description):
    """The <tr> of one transaction."""
    found = [r for r in re.findall(r"<tr>(.*?)</tr>", html, re.DOTALL) if f">{description}<" in r]
    assert found, f"no row for {description}"
    return found[0]


def date_cell(html, description):
    """The date <td> of one row."""
    return row(html, description).split('<td class="c-date')[1].split("</td>")[0]


def transaction(description):
    with vaults.open_vault(1) as v:
        frame = v.list_transactions()
    return frame[frame["description"] == description].iloc[0]


def dates():
    """Every transaction's effective date, keyed by description."""
    with vaults.open_vault(1) as v:
        frame = v.list_transactions()
    return {d: t.date().isoformat() for d, t in zip(frame["description"], frame["date"])}


def post(client, description, effective, *, scope="set", follow_redirects=True, **overrides):
    """Submit one row's date form the way the browser would."""
    tx = transaction(description)
    data = {
        "y": 2026,
        "mo": 5,
        "scope": scope,
        "effective": effective,
        "tid": int(tx["transaction_id"]),
        "account": tx["account_name"],
        # The statement's own date, which is what the form renders.
        "date": tx["original_date"].date().isoformat(),
        "description": tx["description"],
        "amount": float(tx["amount"]),
        "asset": tx["asset"],
    }
    return client.post(
        "/month/set-date", data={**data, **overrides}, follow_redirects=follow_redirects
    )


# --- what the page offers ----------------------------------------------------


def test_every_date_is_a_link_to_its_own_editor(ledger):
    html = ledger.get(MAY).text
    tid = int(transaction("Rent")["transaction_id"])
    assert f"redate={tid}" in date_cell(html, "Rent")
    # No pencil, no glyph, no extra column: the date itself is the control.
    assert "✎" not in date_cell(html, "Rent")
    assert html.count('class="redate"') == 3


def test_no_editor_is_open_until_a_date_is_clicked(ledger):
    html = ledger.get(MAY).text
    assert "/month/set-date" not in html


def test_the_link_opens_the_editor_for_that_row_only(ledger):
    tid = int(transaction("Rent")["transaction_id"])
    html = ledger.get(f"{MAY}&redate={tid}").text

    editing = date_cell(html, "Rent")
    assert 'type="date" name="effective" value="2026-05-20"' in editing
    assert 'name="scope" value="set"' in editing
    assert 'name="date" value="2026-05-20"' in editing  # the statement's date
    assert "reset" not in editing  # nothing to reset: it hasn't been moved

    assert "/month/set-date" not in date_cell(html, "Whole Foods")


def test_the_editor_can_be_left_without_changing_anything(ledger):
    tid = int(transaction("Rent")["transaction_id"])
    html = ledger.get(f"{MAY}&redate={tid}").text
    assert 'class="cancel"' in date_cell(html, "Rent")
    assert dates()["Rent"] == "2026-05-20"


def test_the_filter_rides_on_the_form_action_and_the_link(ledger):
    tid = int(transaction("Rent")["transaction_id"])
    html = ledger.get(f"{MAY}&acct=checking&redate={tid}").text
    assert 'action="/month/set-date?acct=checking"' in html
    assert "acct=checking&amp;redate=" in html  # and the other rows' links keep it


def test_moving_while_filtering_comes_back_to_the_filtered_list(ledger):
    tid = int(transaction("Rent")["transaction_id"])
    r = post(ledger, "Rent", "2026-05-01", follow_redirects=False)
    assert r.headers["location"] == f"/month?y=2026&mo=5#d{tid}"

    tx = transaction("Whole Foods")
    r = ledger.post(
        "/month/set-date?acct=checking",
        data={
            "y": 2026,
            "mo": 5,
            "scope": "set",
            "effective": "2026-05-03",
            "tid": int(tx["transaction_id"]),
            "account": tx["account_name"],
            "date": tx["original_date"].date().isoformat(),
            "description": tx["description"],
            "amount": float(tx["amount"]),
            "asset": tx["asset"],
        },
        follow_redirects=False,
    )
    assert r.headers["location"] == (
        f"/month?y=2026&mo=5&acct=checking#d{int(tx['transaction_id'])}"
    )


# --- moving one -------------------------------------------------------------


def test_a_transaction_can_be_moved_to_another_date(ledger):
    r = post(ledger, "Rent", "2026-05-01")
    assert r.status_code == 200
    assert dates()["Rent"] == "2026-05-01"
    assert transaction("Rent")["original_date"].date().isoformat() == "2026-05-20"


def test_a_moved_row_is_tinted_and_says_where_it_came_from(ledger):
    post(ledger, "Rent", "2026-05-01")
    cell = date_cell(ledger.get(MAY).text, "Rent")
    assert "moved" in cell.split(">")[0]  # the class, on the <td>
    assert 'title="original date: May 20, 2026"' in cell  # the one thing the tint can't say
    assert ">5/1<" in cell  # what it counts on, displayed
    # The rows that weren't moved are not tinted.
    assert "moved" not in date_cell(ledger.get(MAY).text, "Whole Foods").split(">")[0]


def test_moving_a_row_out_of_the_month_says_where_it_went(ledger):
    r = post(ledger, "Rent", "2026-04-28", follow_redirects=False)
    assert r.headers["location"] == "/month?y=2026&mo=5&moved=2026-04"

    html = ledger.get("/month?y=2026&mo=5&moved=2026-04").text
    assert "Moved to" in html and "April 2026" in html
    assert "Rent" not in html.split("<tbody>")[1]  # gone from May
    assert "Rent" in ledger.get(APRIL).text  # and in April


def test_moving_a_row_within_the_month_lands_back_on_it(ledger):
    tid = int(transaction("Rent")["transaction_id"])
    r = post(ledger, "Rent", "2026-05-01", follow_redirects=False)
    assert r.headers["location"] == f"/month?y=2026&mo=5#d{tid}"


def test_a_moved_transaction_can_be_moved_again(ledger):
    """The second edit still names the row by the statement's date, so it
    matches — sending the effective one would match nothing."""
    post(ledger, "Rent", "2026-05-01")
    post(ledger, "Rent", "2026-05-09")
    assert dates()["Rent"] == "2026-05-09"


def test_reset_puts_it_back_on_the_statement_s_date(ledger):
    post(ledger, "Rent", "2026-04-28")
    assert dates()["Rent"] == "2026-04-28"

    tid = int(transaction("Rent")["transaction_id"])
    editing = date_cell(ledger.get(f"{APRIL}&redate={tid}").text, "Rent")
    assert 'name="scope" value="clear"' in editing  # the reset button is there now

    post(ledger, "Rent", "", scope="clear", mo=4)
    assert dates()["Rent"] == "2026-05-20"
    assert "moved" not in date_cell(ledger.get(MAY).text, "Rent").split(">")[0]


def test_a_non_base_currency_row_carries_its_asset(ledger):
    """`assets=None` means the base currency to core, and this row is VFIAX."""
    r = post(ledger, "Buy VFIAX", "2026-05-11")
    assert r.status_code == 200
    assert dates()["Buy VFIAX"] == "2026-05-11"


def test_a_move_survives_a_reload_of_the_statements(ledger):
    """It's written to the adjustments file, so re-applying keeps it."""
    post(ledger, "Rent", "2026-05-01")
    with vaults.open_vault(1) as v:
        v.apply_effective_dates()
    assert dates()["Rent"] == "2026-05-01"


# --- when it can't be done ---------------------------------------------------


def test_submitting_without_a_date_says_so(ledger):
    r = post(ledger, "Rent", "")
    assert r.status_code == 400 and "Pick a date first." in r.text
    assert dates()["Rent"] == "2026-05-20"


def test_the_editor_stays_open_when_the_answer_is_an_error(ledger):
    tid = int(transaction("Rent")["transaction_id"])
    r = post(ledger, "Rent", "", tid=tid)
    assert r.status_code == 400
    assert "/month/set-date" in date_cell(r.text, "Rent")


def test_a_transaction_that_does_not_match_is_refused(ledger):
    r = post(ledger, "Rent", "2026-05-01", amount=-9999.0)
    assert r.status_code == 400 and "match" in r.text
    assert dates()["Rent"] == "2026-05-20"


def test_a_date_that_is_not_a_date_is_refused_by_core_and_shown(ledger):
    r = post(ledger, "Rent", "hovercraft")
    assert r.status_code == 400 and "not dates" in r.text
    assert dates()["Rent"] == "2026-05-20"


def test_an_error_while_filtering_keeps_the_filter(ledger):
    r = ledger.post(
        "/month/set-date?acct=checking",
        data={
            "y": 2026,
            "mo": 5,
            "scope": "set",
            "effective": "",
            "account": "checking",
            "date": "2026-05-20",
            "description": "Rent",
            "amount": -2000.0,
            "asset": "USD",
        },
    )
    assert r.status_code == 400 and "Pick a date first." in r.text
    assert "Buy VFIAX" not in r.text  # still the filtered list


def test_moving_requires_login(client):
    r = client.post("/month/set-date", data={}, follow_redirects=False)
    assert r.status_code in (303, 422)


# --- what a move does to the rest of the page --------------------------------


def test_categorizing_a_moved_row_still_matches_it(ledger):
    """Core names a transaction by its original date whatever it's been moved
    to, so the categorize form has to send that one too."""
    with vaults.open_vault(1) as v:
        v.add_category("rent")
        v.apply_categories()
    post(ledger, "Rent", "2026-04-28")

    tx = transaction("Rent")
    r = ledger.post(
        "/month/categorize",
        data={
            "y": 2026,
            "mo": 4,
            "scope": "one",
            "category": "rent",
            "account": tx["account_name"],
            "date": tx["original_date"].date().isoformat(),
            "description": tx["description"],
            "amount": float(tx["amount"]),
            "asset": tx["asset"],
        },
    )
    assert r.status_code == 200
    with vaults.open_vault(1) as v:
        frame = v.list_transactions()
    assert dict(zip(frame["description"], frame["category"]))["Rent"] == "rent"


def test_the_page_sends_the_original_date_in_both_forms(ledger):
    """The rendered markup, not just the route: a moved row's forms have to
    carry 2026-05-20 even though the cell reads 4/28."""
    post(ledger, "Rent", "2026-04-28")
    tid = int(transaction("Rent")["transaction_id"])
    marked = row(ledger.get(f"{APRIL}&redate={tid}").text, "Rent")
    assert marked.count('name="date" value="2026-05-20"') == 2
    assert 'name="date" value="2026-04-28"' not in marked


def test_the_date_column_sorts_by_what_it_counts_on(ledger):
    post(ledger, "Rent", "2026-05-01")
    assert 'data-sort="2026-05-01"' in date_cell(ledger.get(MAY).text, "Rent")
