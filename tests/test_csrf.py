"""CSRF: every form on a signed-in page carries the session's token, and an
unsafe request without it is refused.

`conftest.BrowserClient` puts the token in automatically, the way a browser
would — these tests are the ones that take it back out.
"""

import re

import pytest

from goblinvest import vaults

FORMS = re.compile(r'<form\b[^>]*method="post"[^>]*>(.*?)</form>', re.DOTALL | re.IGNORECASE)

# One page of each shape that carries a form, including the pages a form is
# reached from rather than just the settings index.
SIGNED_IN_PAGES = [
    "/",
    "/month",
    "/settings",
    "/settings/account",
    "/settings/nav",
    "/settings/vault/accounts",
    "/settings/vault/assets",
    "/settings/vault/categories",
]


@pytest.fixture
def stocked(signed_up):
    """A vault with something in it, so the delete confirmation page renders."""
    with vaults.open_vault(1) as v:
        v.add_account("checking")
        v.add_category("groceries")
    return signed_up


@pytest.mark.parametrize("url", SIGNED_IN_PAGES)
def test_every_form_on_a_signed_in_page_carries_the_token(stocked, url):
    forms = FORMS.findall(stocked.get(url).text)
    assert forms, f"{url} has no POST form — did the page change?"
    for form in forms:
        assert 'name="csrf_token"' in form, f"a form on {url} is missing the token"


def test_the_delete_confirmation_page_carries_it_too(stocked):
    html = stocked.get("/settings/vault/accounts/delete?name=checking").text
    forms = FORMS.findall(html)
    assert forms and all('name="csrf_token"' in form for form in forms)


def test_a_post_without_the_token_is_refused(stocked):
    r = stocked.post("/settings/vault/assets/add", data={"name": "VTI", "csrf_token": ""})
    assert r.status_code == 403
    with vaults.open_vault(1) as v:
        assert "VTI" not in list(v.list_assets()["asset_name"]), "the route ran anyway"


def test_a_post_with_the_wrong_token_is_refused(stocked):
    r = stocked.post(
        "/settings/vault/assets/add", data={"name": "VTI", "csrf_token": "not-the-token"}
    )
    assert r.status_code == 403


def test_another_session_s_token_does_not_work(signed_up):
    """The token is the session's, not the app's — a second account's token is
    no use against the first one's cookie."""
    from conftest import BrowserClient

    with BrowserClient(signed_up.app) as other:
        other.post("/signup", data={"username": "other", "password": "hoardgold"})
        stolen = other.csrf_token()
    assert stolen

    r = signed_up.post("/settings/nav/dashboards", data={"label": "mine", "csrf_token": stolen})
    assert r.status_code == 403


def test_the_token_is_stable_within_a_session(signed_up):
    assert signed_up.csrf_token() == signed_up.csrf_token()


def test_logging_out_needs_the_token(signed_up):
    assert signed_up.post("/logout", data={"csrf_token": ""}).status_code == 403
    assert signed_up.get("/").url.path == "/", "still signed in"
    assert signed_up.post("/logout").url.path == "/login"


def test_login_and_signup_do_not_need_one(client):
    """They have no session yet, so there's no token to carry."""
    r = client.post(
        "/signup", data={"username": "goblin", "password": "hoardgold", "csrf_token": ""}
    )
    assert r.status_code == 200 and r.url.path == "/"
    client.post("/logout")
    r = client.post(
        "/login", data={"username": "goblin", "password": "hoardgold", "csrf_token": ""}
    )
    assert r.status_code == 200 and r.url.path == "/"


def test_a_refused_post_explains_itself(stocked):
    r = stocked.post("/settings/vault/assets/add", data={"name": "VTI", "csrf_token": ""})
    assert "Reload the page" in r.text


def test_reading_is_never_blocked(stocked):
    for url in SIGNED_IN_PAGES:
        assert stocked.get(url).status_code == 200
