"""The Month View page, against a real goblinvest_core vault."""

import re
from concurrent.futures import ThreadPoolExecutor

import pytest

from goblinvest import storage, vaults


@pytest.fixture
def with_transactions(signed_up):
    """Put a few real transactions in user 1's vault, spanning three months."""
    with vaults.open_vault(1) as v:
        v.add_account("checking", account_group_name="cash")
        v.add_account("brokerage", account_group_name="investments")
        v.add_asset("VFIAX")
        v.add_transactions(
            "checking",
            ["2026-03-10", "2026-05-02", "2026-05-20"],
            ["March Payroll", "Whole Foods", "Rent"],
            [3000.00, -82.50, -2000.00],
        )
        v.add_transactions("brokerage", ["2026-05-17"], ["Buy VFIAX"], [1.5], assets=["VFIAX"])
    return signed_up


def body_rows(html):
    return re.findall(r"<tr>(.*?)</tr>", html.split("<tbody>")[1].split("</tbody>")[0], re.DOTALL)


def select_options(html, select_id):
    """The <option> values of one <select>, and which is selected."""
    block = html.split(f'id="{select_id}"', 1)[1].split("</select>", 1)[0]
    values = re.findall(r'<option value="([^"]+)"', block)
    chosen = re.search(r'<option value="([^"]+)" selected', block)
    return values, (chosen.group(1) if chosen else None)


def shown_month(html):
    """The (month, year) the two dropdowns are currently showing."""
    return select_options(html, "mo")[1], select_options(html, "y")[1]


def test_signup_creates_a_vault_and_adjustments_folder(signed_up):
    assert storage.vault_path(1).is_file()
    assert storage.adjustments_dir(1).is_dir()


def test_defaults_to_the_newest_month(with_transactions):
    html = with_transactions.get("/month").text
    assert shown_month(html) == ("5", "2026")
    assert len(body_rows(html)) == 3  # the two May checking rows plus the buy


def test_month_dropdown_lists_all_twelve_months(with_transactions):
    values, _ = select_options(with_transactions.get("/month").text, "mo")
    assert values == [str(n) for n in range(1, 13)]
    assert "January" in with_transactions.get("/month").text


def test_year_dropdown_lists_only_years_with_data_newest_first(with_transactions):
    values, _ = select_options(with_transactions.get("/month").text, "y")
    assert values == ["2026"]


def test_selecting_a_month_filters_the_rows(with_transactions):
    html = with_transactions.get("/month?y=2026&mo=3").text
    rows = body_rows(html)
    assert len(rows) == 1
    assert "March Payroll" in rows[0]
    assert "Whole Foods" not in html


def test_month_inside_the_range_with_no_transactions_renders_empty(with_transactions):
    html = with_transactions.get("/month?y=2026&mo=4").text
    assert body_rows(html) == []
    assert "No transactions in April 2026." in html
    assert shown_month(html) == ("4", "2026")  # stays put, doesn't jump away


@pytest.mark.parametrize(
    "query,expected",
    [
        ("", ("5", "2026")),  # nothing asked for -> newest
        ("y=2026&mo=12", ("5", "2026")),  # past the end -> clamped to newest
        ("y=2026&mo=1", ("3", "2026")),  # before the start -> clamped to oldest
        ("y=1999&mo=6", ("3", "2026")),  # a year with no data at all
        ("mo=4", ("5", "2026")),  # half a pair is not a choice
        ("y=2026&mo=99", ("5", "2026")),  # nonsense month number
    ],
)
def test_out_of_range_selections_clamp_to_a_month_that_has_data(with_transactions, query, expected):
    r = with_transactions.get(f"/month?{query}")
    assert r.status_code == 200
    assert shown_month(r.text) == expected


def test_junk_query_values_do_not_500(with_transactions):
    assert with_transactions.get("/month?y=hello&mo=world").status_code == 422


def test_step_buttons_move_one_month(with_transactions):
    html = with_transactions.get("/month?y=2026&mo=4&step=-1").text
    assert shown_month(html) == ("3", "2026")

    html = with_transactions.get("/month?y=2026&mo=4&step=1").text
    assert shown_month(html) == ("5", "2026")


def picker(html):
    """Just the month picker — the table has disabled controls of its own."""
    return html.split('<form class="picker"', 1)[1].split("</form>", 1)[0]


def test_step_buttons_are_disabled_at_the_ends(with_transactions):
    newest = picker(with_transactions.get("/month?y=2026&mo=5").text)
    assert newest.count("disabled") == 1  # only "next" is off
    assert re.search(r'value="1" class="step"[^>]*disabled', newest)

    oldest = picker(with_transactions.get("/month?y=2026&mo=3").text)
    assert re.search(r'value="-1" class="step"[^>]*disabled', oldest)

    middle = picker(with_transactions.get("/month?y=2026&mo=4").text)
    assert "disabled" not in middle


def test_stepping_past_the_end_clamps(with_transactions):
    html = with_transactions.get("/month?y=2026&mo=5&step=1").text
    assert shown_month(html) == ("5", "2026")


def test_the_picker_is_a_get_form_so_it_works_without_javascript(with_transactions):
    html = with_transactions.get("/month").text
    assert '<form class="picker" method="get" action="/month">' in html
    assert "data-nojs" in html  # the fallback submit button JS hides


def test_rows_carry_machine_readable_sort_values(with_transactions):
    html = with_transactions.get("/month?y=2026&mo=5").text
    assert 'data-sort="2026-05-20"' in html  # ISO date, not the displayed "5/20"
    assert ">5/20<" in html  # short and numeric; the column is one cell wide
    assert 'data-sort="-2000.0"' in html  # raw number, not "-2,000.00"
    assert "-2,000.00" in html  # displayed value is still formatted
    assert 'class="num mv neg"' in html  # negatives marked for styling


def test_table_is_marked_sortable_with_typed_columns(with_transactions):
    html = with_transactions.get("/month?y=2026&mo=5").text
    assert 'class="grid sortable dense"' in html
    for column in ('data-type="date"', 'data-type="number"', 'data-type="text"'):
        assert column in html


def test_non_usd_asset_shows_its_own_asset_column(with_transactions):
    assert "VFIAX" in with_transactions.get("/month?y=2026&mo=5").text


def test_the_base_currency_is_left_out_of_the_asset_column(with_transactions):
    """Every row would say USD, so only the ones that don't are worth reading."""
    html = with_transactions.get("/month?y=2026&mo=5").text
    assert ">USD<" not in html  # never as a cell's text...
    assert 'value="USD"' in html  # ...but still the natural key in the form


def test_market_value_is_the_amount_in_the_base_currency(with_transactions):
    """Core prices each row at its own date; a base-currency row is 1:1."""
    html = with_transactions.get("/month?y=2026&mo=5").text
    assert "market value" in html
    rent = next(row for row in body_rows(html) if "Rent" in row)
    assert rent.count("-2,000.00") == 2  # amount and market value, side by side
    assert 'class="num mv neg"' in rent and rent.count('data-sort="-2000.0"') == 2


def test_an_unpriced_asset_has_no_market_value(with_transactions):
    """VFIAX has no stored price, so core hands back NaN — which is a dash
    here, never the string "nan"."""
    rows = body_rows(with_transactions.get("/month?y=2026&mo=5").text)
    row = next(r for r in rows if "VFIAX" in r)
    assert "unpriced" in row and "—" in row
    assert "nan" not in row.lower()


def test_the_month_table_is_not_paginated(with_transactions):
    """A month is ~30 rows. The pager stays in app.js for tables that need it."""
    assert "data-page-size" not in with_transactions.get("/month").text


def test_the_scope_buttons_have_no_paragraph_explaining_them(with_transactions):
    assert "categorizes every transaction" not in with_transactions.get("/month").text


# --- filtering ---------------------------------------------------------------


def test_the_table_names_itself_so_a_sort_can_be_carried_across_one_load(with_transactions):
    """app.js keeps the sort only over a submit from inside the table, and the
    error re-render happens at /month/categorize — the key is what ties them."""
    assert 'data-sort-key="month"' in with_transactions.get("/month").text


def test_the_account_filter_comes_before_the_category_one(with_transactions):
    html = with_transactions.get("/month?y=2026&mo=5").text
    assert html.index('id="f-acct"') < html.index('id="f-cat"')


def test_filters_offer_the_values_present_in_the_month(with_transactions):
    html = with_transactions.get("/month?y=2026&mo=5").text
    accounts, _ = select_options(html, "f-acct")
    assert accounts == ["brokerage", "checking"]
    categories, _ = select_options(html, "f-cat")
    assert categories == ["unclassified"]  # nothing categorized yet


def test_filtering_by_account_narrows_the_rows(with_transactions):
    html = with_transactions.get("/month?y=2026&mo=5&acct=brokerage").text
    rows = body_rows(html)
    assert len(rows) == 1 and "VFIAX" in rows[0]
    assert "1 of 3 transactions" in html
    assert select_options(html, "f-acct")[1] == "brokerage"  # the pick sticks


def test_several_picks_are_a_union(with_transactions):
    with vaults.open_vault(1) as v:
        v.add_account("savings", account_group_name="cash")
        v.add_transactions("savings", ["2026-05-04"], ["Interest"], [1.25])
    two = with_transactions.get("/month?y=2026&mo=5&acct=savings&acct=brokerage").text
    assert len(body_rows(two)) == 2


def test_an_empty_pick_means_every_row(with_transactions):
    assert len(body_rows(with_transactions.get("/month?y=2026&mo=5").text)) == 3


def test_a_filter_matching_nothing_says_so_and_keeps_the_control(with_transactions):
    html = with_transactions.get("/month?y=2026&mo=5&acct=hovercraft").text
    assert body_rows(html) == []
    assert "No transactions match." in html
    # The value isn't in this month, but the control applying it must still show it.
    assert select_options(html, "f-acct") == (["brokerage", "checking", "hovercraft"], "hovercraft")


def test_the_filter_survives_stepping_to_another_month(with_transactions):
    html = with_transactions.get("/month?y=2026&mo=5&acct=checking").text
    assert '<input type="hidden" name="acct" value="checking" />' in picker(html)


def test_the_filter_is_a_get_form_that_works_without_javascript(with_transactions):
    html = with_transactions.get("/month?y=2026&mo=5").text
    assert '<form class="filters" method="get" action="/month">' in html
    assert 'name="acct" multiple' in html  # a plain multi-select underneath
    assert "data-combo" in html  # which app.js upgrades


def test_the_clear_link_appears_only_while_filtering(with_transactions):
    assert 'class="clear"' not in with_transactions.get("/month?y=2026&mo=5").text
    assert 'class="clear"' in with_transactions.get("/month?y=2026&mo=5&acct=checking").text


def test_empty_vault_says_so(signed_up):
    html = signed_up.get("/month").text
    assert "No transactions yet" in html
    assert "<tbody>" not in html


def test_missing_vault_is_handled(signed_up):
    storage.vault_path(1).unlink()
    r = signed_up.get("/month")
    assert r.status_code == 200 and "No vault yet" in r.text


def test_month_view_requires_login(client):
    r = client.get("/month", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_javascript_is_served_locally(signed_up):
    html = signed_up.get("/month").text
    assert '<script src="/static/app.js" defer></script>' in html
    assert "http://" not in html and "https://" not in html  # no CDN
    assert signed_up.get("/static/app.js").status_code == 200


def test_concurrent_requests_do_not_trip_sqlite_thread_affinity(with_transactions):
    """FastAPI runs the connection dependency and the endpoint on different
    threadpool threads once requests overlap; `check_same_thread=False` in
    db.connect is what keeps that working."""
    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(pool.map(lambda _: with_transactions.get("/month").status_code, range(40)))
    assert codes == [200] * 40
