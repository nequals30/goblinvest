"""Find Transactions: a search over the whole ledger, paged and sorted on the
server, against a real goblinvest_core vault."""

import re
from urllib.parse import parse_qs, urlsplit

import pytest

from goblinvest import vaults
from goblinvest.routes.pages import FIND_PAGE_SIZE


@pytest.fixture
def ledger(signed_up):
    """Two accounts, a second asset, a joint account, categories set and not."""
    with vaults.open_vault(1) as v:
        v.add_account("checking", account_group_name="cash")
        v.add_account("joint visa", account_group_name="cards", ownership_share=0.5)
        v.add_asset("VFIAX")
        v.add_category("groceries")
        v.add_category("travel")
        v.add_transactions(
            "checking",
            ["2025-12-30", "2026-03-10", "2026-05-02"],
            ["Shell Gas #12", "March Payroll", "Whole Foods"],
            [-40.00, 3000.00, -82.50],
        )
        v.add_transactions("joint visa", ["2026-04-11"], ["Las Vegas Hotel"], [-600.00])
        v.add_transactions("checking", ["2026-05-17"], ["Buy VFIAX"], [1.5], assets=["VFIAX"])
        v.apply_categories()
        v.set_category_rule("Whole Foods", "groceries")
        v.set_category_rule("Las Vegas Hotel", "travel")
    return signed_up


def body_rows(html):
    body = html.split("<tbody>")[1].split("</tbody>")[0]
    return re.findall(r"<tr>(.*?)</tr>", body, re.DOTALL)


def descriptions(html):
    return [re.search(r'<td class="desc">(.*?)</td>', row).group(1) for row in body_rows(html)]


def search(client, **params):
    pairs = [("search", "1")]
    for name, value in params.items():
        for v in value if isinstance(value, list) else [value]:
            pairs.append((name, v))
    return client.get("/find", params=pairs)


def test_opens_on_the_form_alone(ledger):
    html = ledger.get("/find").text
    assert "<h1>Find Transactions</h1>" in html
    assert ">Search</button>" in html
    assert "<tbody>" not in html


def test_searching_for_nothing_lists_the_whole_ledger(ledger):
    html = search(ledger).text
    assert len(body_rows(html)) == 5
    assert "5 transactions" in html


def test_the_form_waits_for_its_button(ledger):
    """No control applies itself: the combos are in a data-manual form, the
    share box has no data-autosubmit, and the button is not a no-JS fallback."""
    html = ledger.get("/find").text
    form = html.split('<form class="filters find"')[1].split("</form>")[0]
    assert "data-manual" in form
    assert "data-autosubmit" not in form
    assert "data-nojs" not in form


def test_filter_options_are_everything_the_vault_has(ledger):
    html = ledger.get("/find").text
    for select_id, expected in [
        ("f-acct", ["checking", "joint visa"]),
        ("f-cat", ["groceries", "travel", "unclassified"]),
        ("f-asset", ["USD", "VFIAX"]),
    ]:
        block = html.split(f'id="{select_id}"')[1].split("</select>")[0]
        assert re.findall(r'<option value="([^"]+)"', block) == expected


def test_account_filter(ledger):
    html = search(ledger, acct="joint visa").text
    assert descriptions(html) == ["Las Vegas Hotel"]


def test_category_filter_takes_unclassified(ledger):
    html = search(ledger, cat=["unclassified"]).text
    assert sorted(descriptions(html)) == ["Buy VFIAX", "March Payroll", "Shell Gas #12"]


def test_asset_filter(ledger):
    assert descriptions(search(ledger, asset="VFIAX").text) == ["Buy VFIAX"]


def test_description_pattern_is_a_wildcard(ledger):
    html = search(ledger, desc="*gas*").text
    assert descriptions(html) == ["Shell Gas #12", "Las Vegas Hotel"]


def test_date_range_either_end_alone(ledger):
    assert descriptions(search(ledger, start="2026-05-01").text) == ["Whole Foods", "Buy VFIAX"]
    assert descriptions(search(ledger, end="2025-12-31").text) == ["Shell Gas #12"]


def test_filters_combine(ledger):
    html = search(ledger, acct="checking", desc="*a*", start="2026-01-01").text
    assert descriptions(html) == ["March Payroll", "Buy VFIAX"]


def test_picks_survive_into_the_form(ledger):
    html = search(ledger, acct="checking", desc="*gas*", start="2025-01-01", share="1").text
    assert '<option value="checking" selected>' in html
    assert 'name="desc" value="*gas*"' in html
    assert 'name="start" value="2025-01-01"' in html
    assert re.search(r'name="share" value="1"\s+checked', html)


def test_ownership_share(ledger):
    whole = search(ledger, acct="joint visa").text
    half = search(ledger, acct="joint visa", share="1").text
    assert "-600.00" in whole.split("<tfoot>")[1]
    assert "-300.00" in half.split("<tfoot>")[1]
    assert "(&times; 0.5)" in half


def test_a_name_the_vault_lacks_is_core_s_error(ledger):
    r = search(ledger, acct="chekcing")
    assert r.status_code == 400
    assert "not in the vault: chekcing" in r.text
    # The stale pick stays in the control so it can be unticked.
    assert '<option value="chekcing" selected>' in r.text


def test_no_match(ledger):
    html = search(ledger, desc="nothing like this").text
    assert "No transactions match." in html
    assert "<tfoot>" not in html


def test_one_page_has_a_total(ledger):
    html = search(ledger, cat="groceries").text
    assert "-82.50" in html.split("<tfoot>")[1]


# --- paging and sorting -------------------------------------------------------


@pytest.fixture
def big(signed_up):
    """More than two pages of transactions, one per day."""
    n = FIND_PAGE_SIZE * 2 + 50
    with vaults.open_vault(1) as v:
        v.add_account("checking")
        v.add_transactions(
            "checking",
            [f"2025-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(n)],
            [f"row {i:03d}" for i in range(n)],
            [float(-(i + 1)) for i in range(n)],
        )
    return signed_up, n


def test_paged_on_the_server(big):
    client, n = big
    first = search(client).text
    assert len(body_rows(first)) == FIND_PAGE_SIZE
    assert descriptions(first)[0] == "row 000"
    assert f"1–100 of {n}" in first
    last = search(client, page="3").text
    assert len(body_rows(last)) == n - 2 * FIND_PAGE_SIZE
    assert descriptions(last)[-1] == f"row {n - 1:03d}"


def test_with_more_than_one_page_the_total_is_of_every_page(big):
    """Amounts are -1 .. -n, so the whole ledger sums to -n(n+1)/2 — on every
    page, labelled as such, not just the hundred rows on screen."""
    client, n = big
    whole = f"{-n * (n + 1) / 2:,.2f}"
    for page in ("1", "3"):
        foot = search(client, page=page).text.split("<tfoot>")[1]
        assert "<td>Total, all pages</td>" in foot
        assert whole in foot


def test_one_page_total_is_just_total(ledger):
    assert "<td>Total</td>" in search(ledger).text.split("<tfoot>")[1]


def test_a_page_past_the_end_is_the_last_one(big):
    client, n = big
    html = search(client, page="99").text
    assert descriptions(html)[-1] == f"row {n - 1:03d}"


def test_pager_links_keep_the_search(big):
    client, _ = big
    html = search(client, desc="row*", sort="amount").text
    link = re.search(r'<a href="([^"]+)">next ›</a>', html).group(1).replace("&amp;", "&")
    qs = parse_qs(urlsplit(link).query)
    assert qs["desc"] == ["row*"] and qs["sort"] == ["amount"] and qs["page"] == ["2"]


def test_sort_sees_every_row_not_just_the_page(big):
    """The smallest amount is on the last page in date order; sorted ascending
    by amount it is the first row of the first page."""
    client, n = big
    html = search(client, sort="amount").text
    assert descriptions(html)[0] == f"row {n - 1:03d}"
    html = search(client, sort="amount", dir="desc").text
    assert descriptions(html)[0] == "row 000"


def test_header_links_flip_the_current_column(ledger):
    html = search(ledger, sort="mv").text
    mv = re.search(
        r'<th class="is-sortable num" aria-sort="ascending"><a class="sort-btn" '
        r'href="([^"]+)">market value</a>',
        html,
    )
    assert mv and "dir=desc" in mv.group(1)
    # Arriving unsorted, the rows are in date order, and the date says so.
    assert (
        'aria-sort="ascending"><a class="sort-btn" href="/find?search=1&amp;sort=date'
        '&amp;dir=desc">date' in search(ledger).text
    )


def test_unpriced_rows_sort_last_both_ways(ledger):
    # VFIAX has no stored price, so its market value is a dash.
    for direction in ("", "desc"):
        html = search(ledger, sort="mv", dir=direction).text
        assert descriptions(html)[-1] == "Buy VFIAX"


# --- editing from the results -------------------------------------------------


def _form_data(html, description, form_class):
    row = next(r for r in body_rows(html) if f">{description}<" in r)
    form = (
        row.split(f'class="{form_class}"')[0].rsplit("<form", 1)[1]
        + row.split(f'class="{form_class}"')[1]
    )
    action = re.search(r'action="([^"]+)"', form).group(1).replace("&amp;", "&")
    fields = dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', form))
    fields.pop("csrf_token", None)
    return action, fields


def test_categorize_from_the_results_comes_back_to_them(ledger):
    html = search(ledger, desc="*gas*", sort="amount").text
    action, fields = _form_data(html, "Shell Gas #12", "cat-form")
    assert action.startswith("/find/categorize?search=1")
    r = ledger.post(
        action, data={**fields, "category": "travel", "scope": "one"}, follow_redirects=False
    )
    assert r.status_code == 303
    target = urlsplit(r.headers["location"])
    assert target.path == "/find"
    assert parse_qs(target.query)["desc"] == ["*gas*"]
    assert parse_qs(target.query)["sort"] == ["amount"]
    assert target.fragment == f"t{fields['tid']}"
    with vaults.open_vault(1) as v:
        frame = v.list_transactions(descriptions="Shell Gas #12")
    assert frame["category"].tolist() == ["travel"]


def test_categorize_error_rerenders_the_search(ledger):
    html = search(ledger, desc="*gas*").text
    action, fields = _form_data(html, "Shell Gas #12", "cat-form")
    r = ledger.post(action, data={**fields, "category": "", "scope": "one"})
    assert r.status_code == 400
    assert "Pick a category first." in r.text
    assert descriptions(r.text) == ["Shell Gas #12", "Las Vegas Hotel"]


def test_set_date_from_the_results(ledger):
    html = search(ledger, acct="checking").text
    tid = re.search(r'id="d(\d+)"[^>]*>\s*<a[^>]*>12/30/2025', html).group(1)
    html = search(ledger, acct="checking", redate=tid).text
    action, fields = _form_data(html, "Shell Gas #12", "date-form")
    assert action.startswith("/find/set-date?search=1")
    r = ledger.post(
        action, data={**fields, "effective": "2026-01-02", "scope": "set"}, follow_redirects=False
    )
    assert r.status_code == 303
    assert r.headers["location"].startswith("/find?search=1&acct=checking")
    with vaults.open_vault(1) as v:
        frame = v.list_transactions(descriptions="Shell Gas #12")
    assert str(frame["date"].iloc[0].date()) == "2026-01-02"


def test_dates_carry_the_year(ledger):
    assert ">12/30/2025</a>" in search(ledger).text
