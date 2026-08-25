"""Schema versioning: an existing database gets upgraded in place, and ends up
identical to one created from scratch.

The failure mode this guards against is quiet: `SCHEMA` is all `CREATE TABLE IF
NOT EXISTS`, so a new column added there reaches a fresh database and no
existing one — which is every database except a developer's.
"""

import sqlite3
from contextlib import closing

import pytest

from goblinvest import db


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """An isolated data dir, without building the app around it."""
    monkeypatch.setenv("GV_DATA_DIR", str(tmp_path / "data"))
    from goblinvest.config import settings

    settings.cache_clear()
    yield tmp_path / "data"
    settings.cache_clear()


def version(conn) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def columns(conn, table) -> list[str]:
    return [row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def make_v1_database(with_session: bool = True) -> None:
    """A database as it was before migrations existed: the frozen schema, no
    version stamp, and a live session in it."""
    with closing(db.connect()) as conn, conn:
        conn.executescript(db.SCHEMA)
        conn.execute(
            "INSERT INTO users(id, username, password_hash) VALUES (1, 'goblin', 'x')",
        )
        if with_session:
            conn.execute(
                """
                INSERT INTO sessions(token, user_id, expires_at)
                VALUES ('old-token', 1, datetime('now', '+30 days'))
                """
            )
        assert version(conn) == 0


def test_a_fresh_database_is_at_the_current_version(data_dir):
    db.init_db()
    with closing(db.connect()) as conn:
        assert version(conn) == db.SCHEMA_VERSION
        assert "csrf_token" in columns(conn, "sessions")


def test_a_pre_migration_database_is_upgraded_in_place(data_dir):
    data_dir.mkdir(parents=True)
    make_v1_database()

    db.init_db()

    with closing(db.connect()) as conn:
        assert version(conn) == db.SCHEMA_VERSION
        row = conn.execute("SELECT * FROM sessions WHERE token = 'old-token'").fetchone()
        assert row is not None, "the migration logged everyone out"
        assert row["csrf_token"], "an existing session got no CSRF token"
        assert conn.execute("SELECT username FROM users").fetchone()["username"] == "goblin"


def test_an_upgraded_database_matches_a_fresh_one(data_dir, tmp_path):
    """The property the whole scheme rests on: upgrade and create converge."""
    data_dir.mkdir(parents=True)
    make_v1_database(with_session=False)
    db.init_db()
    with closing(db.connect()) as conn:
        upgraded = {t: columns(conn, t) for t in ("users", "sessions", "nav_items")}

    fresh_path = tmp_path / "fresh.db"
    with closing(db.connect(fresh_path)) as conn, conn:
        conn.executescript(db.SCHEMA)
        db.migrate(conn)
        fresh = {t: columns(conn, t) for t in ("users", "sessions", "nav_items")}

    assert upgraded == fresh


def test_init_db_is_idempotent(data_dir):
    db.init_db()
    db.init_db()
    with closing(db.connect()) as conn:
        assert version(conn) == db.SCHEMA_VERSION
        assert columns(conn, "sessions").count("csrf_token") == 1


def test_a_database_from_a_newer_build_is_refused(data_dir):
    db.init_db()
    with closing(db.connect()) as conn, conn:
        conn.execute(f"PRAGMA user_version = {db.SCHEMA_VERSION + 7}")

    with pytest.raises(db.SchemaTooNew):
        db.init_db()


def test_every_step_is_reachable_from_zero(data_dir):
    """No gaps in the numbering — `migrate` walks the range, so a missing key
    would be a KeyError on somebody's server."""
    assert sorted(db.MIGRATIONS) == list(range(1, db.SCHEMA_VERSION + 1))


def test_a_session_row_still_needs_its_columns(data_dir):
    """A sanity check that the frozen SCHEMA really is the v1 shape: inserting
    the way v1 did fails against the current code, which is why the migration
    has to exist at all."""
    db.init_db()
    with closing(db.connect()) as conn, conn:
        conn.execute("INSERT INTO users(id, username, password_hash) VALUES (1, 'g', 'x')")
        with pytest.raises(sqlite3.IntegrityError):
            db.insert_session(conn, "t", 1, 30, csrf_token=None)


def test_a_session_from_before_the_migration_still_works(signed_up):
    """The server's own upgrade, end to end: a browser that was logged in
    before the column existed keeps its cookie, and its forms work after."""
    with closing(db.connect()) as conn, conn:
        conn.execute("ALTER TABLE sessions DROP COLUMN csrf_token")
        conn.execute("PRAGMA user_version = 0")

    db.init_db()

    r = signed_up.post("/settings/nav/dashboards", data={"label": "after"})
    assert r.status_code == 200 and "after" in r.text
