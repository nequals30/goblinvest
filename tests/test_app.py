"""App-level wiring: what prod turns off."""

import pytest
from fastapi.testclient import TestClient

DOC_URLS = ["/docs", "/redoc", "/openapi.json"]


def app_client(tmp_path, monkeypatch, env: str):
    monkeypatch.setenv("GV_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GV_ENV", env)
    from goblinvest.config import settings

    settings.cache_clear()
    from goblinvest.main import create_app

    return TestClient(create_app())


@pytest.mark.parametrize("url", DOC_URLS)
def test_the_docs_are_there_in_dev(tmp_path, monkeypatch, url):
    with app_client(tmp_path, monkeypatch, "dev") as client:
        assert client.get(url).status_code == 200


@pytest.mark.parametrize("url", DOC_URLS)
def test_prod_does_not_publish_a_map_of_every_route(tmp_path, monkeypatch, url):
    with app_client(tmp_path, monkeypatch, "prod") as client:
        assert client.get(url).status_code == 404
