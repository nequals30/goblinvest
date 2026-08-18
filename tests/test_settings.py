"""The settings menu and account page."""

ACCOUNT_URL = "/settings/account"
PASSWORD_URL = "/settings/account/password"


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
