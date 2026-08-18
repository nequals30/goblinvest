"""The left pane: seeding, ordering, hiding, and user dashboards."""

import re

from goblinvest import nav

NAV_URL = "/settings/nav"


def sidebar_labels(html: str) -> list[str]:
    """The labels rendered in the left pane, in order (Settings excluded)."""
    pane = html.split('class="nav-main"', 1)[1].split("</nav>", 1)[0]
    return [m.strip() for m in re.findall(r'href="[^"]*">([^<]+)</a>', pane)]


def item_ids(html: str) -> list[int]:
    """Nav item ids in the settings table, in displayed order."""
    seen, out = set(), []
    for m in re.findall(r"/settings/nav/(\d+)/move", html):
        if m not in seen:
            seen.add(m)
            out.append(int(m))
    return out


def test_builtins_seeded_at_signup(signed_up):
    html = signed_up.get("/").text
    assert sidebar_labels(html) == ["CSV Import", "Month View", "Main Dashboard"]


def test_placeholder_pages_render(signed_up):
    for url, heading in [
        ("/import", "CSV Import"),
        ("/month", "Month View"),
        ("/dashboard", "Main Dashboard"),
    ]:
        r = signed_up.get(url)
        assert r.status_code == 200
        assert f"<h1>{heading}</h1>" in r.text


def test_placeholders_require_login(client):
    for url in ("/import", "/month", "/dashboard", "/settings", "/settings/nav"):
        r = client.get(url, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/login"


def test_active_item_is_marked(signed_up):
    html = signed_up.get("/month").text
    active = re.search(r'class="nav-item is-active"\s*\n?\s*href="([^"]+)"', html)
    assert active is not None and active.group(1) == "/month"


def test_move_reorders_the_pane(signed_up):
    ids = item_ids(signed_up.get(NAV_URL).text)
    signed_up.post(f"{NAV_URL}/{ids[2]}/move", data={"direction": "up"})
    assert sidebar_labels(signed_up.get("/").text) == [
        "CSV Import",
        "Main Dashboard",
        "Month View",
    ]
    signed_up.post(f"{NAV_URL}/{ids[2]}/move", data={"direction": "down"})
    assert sidebar_labels(signed_up.get("/").text) == [
        "CSV Import",
        "Month View",
        "Main Dashboard",
    ]


def test_move_past_the_ends_is_a_noop(signed_up):
    ids = item_ids(signed_up.get(NAV_URL).text)
    signed_up.post(f"{NAV_URL}/{ids[0]}/move", data={"direction": "up"})
    signed_up.post(f"{NAV_URL}/{ids[-1]}/move", data={"direction": "down"})
    assert item_ids(signed_up.get(NAV_URL).text) == ids


def test_hiding_removes_from_pane_but_not_from_settings(signed_up):
    ids = item_ids(signed_up.get(NAV_URL).text)
    signed_up.post(f"{NAV_URL}/{ids[1]}/visibility", data={"hidden": 1})

    assert sidebar_labels(signed_up.get("/").text) == ["CSV Import", "Main Dashboard"]
    settings_html = signed_up.get(NAV_URL).text
    assert "Month View" in settings_html and item_ids(settings_html) == ids

    signed_up.post(f"{NAV_URL}/{ids[1]}/visibility", data={"hidden": 0})
    assert "Month View" in sidebar_labels(signed_up.get("/").text)


def test_hidden_page_is_still_reachable(signed_up):
    ids = item_ids(signed_up.get(NAV_URL).text)
    signed_up.post(f"{NAV_URL}/{ids[1]}/visibility", data={"hidden": 1})
    assert signed_up.get("/month").status_code == 200


def test_create_dashboard_adds_a_placeholder_page(signed_up):
    r = signed_up.post(f"{NAV_URL}/dashboards", data={"label": "Retirement Stuff"})
    assert r.status_code == 200 and r.url.path == NAV_URL

    assert sidebar_labels(signed_up.get("/").text)[-1] == "Retirement Stuff"
    page = signed_up.get("/dashboards/retirement-stuff")
    assert page.status_code == 200 and "<h1>Retirement Stuff</h1>" in page.text


def test_dashboard_slugs_are_unique_per_user(signed_up):
    for _ in range(2):
        signed_up.post(f"{NAV_URL}/dashboards", data={"label": "Taxes"})
    assert signed_up.get("/dashboards/taxes").status_code == 200
    assert signed_up.get("/dashboards/taxes-2").status_code == 200


def test_blank_dashboard_name_is_rejected(signed_up):
    r = signed_up.post(f"{NAV_URL}/dashboards", data={"label": "   "})
    assert r.status_code == 400
    assert "Give the dashboard a name." in r.text


def test_dashboards_are_per_user(client):
    client.post("/signup", data={"username": "one", "password": "hoardgold"})
    client.post(f"{NAV_URL}/dashboards", data={"label": "Private"})
    client.post("/logout")

    client.post("/signup", data={"username": "two", "password": "hoardgold"})
    assert client.get("/dashboards/private").status_code == 404
    assert "Private" not in sidebar_labels(client.get("/").text)


def test_delete_removes_a_dashboard_but_not_a_builtin(signed_up):
    signed_up.post(f"{NAV_URL}/dashboards", data={"label": "Scratch"})
    ids = item_ids(signed_up.get(NAV_URL).text)

    signed_up.post(f"{NAV_URL}/{ids[-1]}/delete")
    assert signed_up.get("/dashboards/scratch").status_code == 404

    signed_up.post(f"{NAV_URL}/{ids[0]}/delete")
    assert sidebar_labels(signed_up.get("/").text) == [
        "CSV Import",
        "Month View",
        "Main Dashboard",
    ]


def test_missing_dashboard_is_404(signed_up):
    assert signed_up.get("/dashboards/nope").status_code == 404


def test_builtins_are_seeded_lazily_for_older_users(signed_up):
    """A user who predates a built-in item picks it up on the next page load.

    Standing in for that: drop a row, as if the user signed up before the item
    existed. Rendering any page re-seeds it, appended at the end so the user's
    own ordering of the items they *do* have is left alone.
    """
    from goblinvest import db

    with db.connect() as conn:
        conn.execute("DELETE FROM nav_items WHERE slug = 'month-view'")

    assert sidebar_labels(signed_up.get("/").text) == [
        "CSV Import",
        "Main Dashboard",
        "Month View",
    ]


def test_slugify():
    assert nav.slugify("Retirement Stuff") == "retirement-stuff"
    assert nav.slugify("  Q1 / Q2 !! ") == "q1-q2"
    assert nav.slugify("!!!") == "dashboard"
