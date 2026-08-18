import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A TestClient with an isolated data dir and cheap password hashing."""
    monkeypatch.setenv("GV_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GV_PBKDF2_ITERS", "1000")  # 600k per call makes tests crawl

    from goblinvest.config import settings

    settings.cache_clear()

    from goblinvest.main import create_app

    with TestClient(create_app()) as c:
        yield c

    settings.cache_clear()


@pytest.fixture
def signed_up(client):
    """A client with a logged-in user 'goblin'."""
    r = client.post("/signup", data={"username": "goblin", "password": "hoardgold"})
    assert r.status_code == 200 and r.url.path == "/"
    return client
