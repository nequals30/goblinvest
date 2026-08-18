from goblinvest import storage
from goblinvest.auth import SESSION_COOKIE
from goblinvest.db import connect


def test_home_redirects_to_login_when_anonymous(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_healthz_is_public(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_stylesheet_is_served_locally(client):
    r = client.get("/static/styles.css")
    assert r.status_code == 200
    assert "text/css" in r.headers["content-type"]


def test_no_external_assets_referenced(client):
    # The old UI pulled Tabulator and ECharts from CDNs on every load.
    assert "//" not in client.get("/login").text.split("<body")[0].replace("<!doctype", "")


def test_signup_sets_cookie_provisions_storage_and_lands_on_welcome(client):
    r = client.post("/signup", data={"username": "goblin", "password": "hoardgold"})
    assert r.status_code == 200
    assert r.url.path == "/"
    assert "Welcome, goblin" in r.text
    assert client.cookies.get(SESSION_COOKIE)
    assert storage.statements_dir(1).is_dir()


def test_welcome_shows_logout_and_username(signed_up):
    body = signed_up.get("/").text
    assert "goblin" in body
    assert 'action="/logout"' in body


def test_duplicate_username_rejected_case_insensitively(signed_up):
    signed_up.cookies.clear()
    r = signed_up.post("/signup", data={"username": "GOBLIN", "password": "otherpass"})
    assert r.status_code == 400
    assert "taken" in r.text
    assert not signed_up.cookies.get(SESSION_COOKIE)


def test_short_password_rejected_and_no_user_created(client):
    r = client.post("/signup", data={"username": "goblin", "password": "short"})
    assert r.status_code == 400
    assert "at least 8" in r.text
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


def test_blank_username_rejected(client):
    r = client.post("/signup", data={"username": "   ", "password": "hoardgold"})
    assert r.status_code == 400
    assert "Username must be" in r.text


def test_login_with_bad_password_sets_no_cookie(signed_up):
    signed_up.cookies.clear()
    r = signed_up.post("/login", data={"username": "goblin", "password": "wrongpass"})
    assert r.status_code == 401
    assert "Invalid username or password" in r.text
    assert not signed_up.cookies.get(SESSION_COOKIE)


def test_login_with_unknown_user_gives_same_message(client):
    r = client.post("/login", data={"username": "nobody", "password": "hoardgold"})
    assert r.status_code == 401
    assert "Invalid username or password" in r.text


def test_login_round_trip(signed_up):
    signed_up.post("/logout")
    signed_up.cookies.clear()
    r = signed_up.post("/login", data={"username": "goblin", "password": "hoardgold"})
    assert r.status_code == 200
    assert "Welcome, goblin" in r.text


def test_logout_clears_session_row_and_cookie(signed_up):
    token = signed_up.cookies.get(SESSION_COOKIE)
    signed_up.post("/logout")

    with connect() as conn:
        assert (
            conn.execute("SELECT COUNT(*) FROM sessions WHERE token = ?", (token,)).fetchone()[0]
            == 0
        )

    r = signed_up.get("/", follow_redirects=False)
    assert r.status_code == 303


def test_expired_session_reads_as_logged_out(signed_up):
    with connect() as conn:
        conn.execute("UPDATE sessions SET expires_at = datetime('now', '-1 day')")
        conn.commit()

    r = signed_up.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_garbage_cookie_reads_as_logged_out(client):
    client.cookies.set(SESSION_COOKIE, "not-a-real-token")
    assert client.get("/", follow_redirects=False).status_code == 303


def test_login_page_redirects_when_already_signed_in(signed_up):
    for path in ("/login", "/signup"):
        r = signed_up.get(path, follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/"


def test_session_cookie_is_httponly_and_lax(client):
    r = client.post("/signup", data={"username": "goblin", "password": "hoardgold"})
    setcookie = r.history[0].headers["set-cookie"]
    assert "HttpOnly" in setcookie
    assert "SameSite=lax" in setcookie
    assert "Secure" not in setcookie  # dev default


def test_two_users_get_separate_directories(client):
    client.post("/signup", data={"username": "one", "password": "hoardgold"})
    client.cookies.clear()
    client.post("/signup", data={"username": "two", "password": "hoardgold"})

    assert storage.statements_dir(1).is_dir()
    assert storage.statements_dir(2).is_dir()
    assert storage.user_dir(1) != storage.user_dir(2)
