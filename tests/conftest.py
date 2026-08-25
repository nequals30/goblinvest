import re

import pytest
from fastapi.testclient import TestClient

CSRF_FIELD = re.compile(r'name="csrf_token" value="([^"]*)"')


class BrowserClient(TestClient):
    """A TestClient that fills in the CSRF field the way a browser would.

    Every form on a signed-in page renders the session's token in a hidden
    input, so a POST that leaves it out is exercising the guard rather than the
    route. Pass `csrf_token=` in `data` (`""` for the empty case) to do that
    deliberately; otherwise this puts the real one in.
    """

    def csrf_token(self) -> str:
        """The token as it appears on a page — scraped, not read from the db,
        so a form that stopped rendering it would fail these tests too."""
        found = CSRF_FIELD.search(super().get("/").text)
        return found.group(1) if found else ""

    def post(self, url, *, data=None, **kwargs):
        if not isinstance(data, dict) or "csrf_token" not in data:
            token = self.csrf_token()
            if token:
                data = {**(data or {}), "csrf_token": token}
        return super().post(url, data=data, **kwargs)


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A TestClient with an isolated data dir and cheap password hashing."""
    monkeypatch.setenv("GV_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GV_PBKDF2_ITERS", "1000")  # 600k per call makes tests crawl

    from goblinvest.config import settings

    settings.cache_clear()

    from goblinvest.main import create_app

    with BrowserClient(create_app()) as c:
        yield c

    settings.cache_clear()


@pytest.fixture
def signed_up(client):
    """A client with a logged-in user 'goblin'."""
    r = client.post("/signup", data={"username": "goblin", "password": "hoardgold"})
    assert r.status_code == 200 and r.url.path == "/"
    return client
