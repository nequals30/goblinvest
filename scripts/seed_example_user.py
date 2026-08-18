#!/usr/bin/env python3
"""Build a demo account you can log into: username `example`, password `example123`.

    uv run scripts/seed_example_user.py

It creates the user (or resets an existing one), then hands the two example
scripts that ship with goblinvest_core the paths inside that user's data
folder, so the demo data lands exactly where the web app expects to find it:

    data/users/<id>/raw_data/<account>/<year>.csv   generate_fake_data.py
    data/users/<id>/vault.db                        load_vault.py
    data/users/<id>/vault_adjustments/              made by Vault.create

Nothing here knows anything about finance — core's scripts own all of that.
This only decides *where*.

The vault step pulls market prices from Yahoo Finance, so it needs internet and
takes a few seconds. `--reset` wipes the example user's folder first.
"""

import argparse
import functools
import shutil
import subprocess
import sys
from pathlib import Path

from goblinvest import auth, db, nav, storage
from goblinvest.config import settings

# The example scripts write straight to the inherited stdout, so unflushed
# prints here would surface after their output rather than before it.
print = functools.partial(print, flush=True)

USERNAME = "example"
PASSWORD = "example123"

REPO = Path(__file__).resolve().parent.parent
EXAMPLE_DIR = REPO / "goblinvest_core" / "examples" / "load_transactions"
GENERATE = EXAMPLE_DIR / "generate_fake_data.py"
LOAD_VAULT = EXAMPLE_DIR / "load_vault.py"


def upsert_example_user(conn) -> int:
    """Return the example user's id, creating them or resetting the password."""
    user = db.find_user_by_username(conn, USERNAME)
    password_hash = auth.hash_password(PASSWORD)
    if user is None:
        user_id = db.insert_user(conn, USERNAME, password_hash)
        print(f"Created user {USERNAME!r} (id {user_id}).")
    else:
        user_id = user.id
        db.update_password(conn, user_id, password_hash)
        print(f"User {USERNAME!r} already exists (id {user_id}); password reset.")
    nav.ensure_builtins(conn, user_id)
    return user_id


def run(script: Path, *script_args: str) -> None:
    """Run one of core's example scripts in this project's interpreter.

    They're written as scripts, not importable modules — `load_vault.py` does
    its work at import time — so a subprocess is the honest way to call them.
    goblinvest_core is an editable path dependency, so `sys.executable` already
    has it (and pandas, and yfinance) importable.
    """
    print(f"\n$ {script.name} {' '.join(script_args)}")
    result = subprocess.run(
        [sys.executable, str(script), *script_args], cwd=EXAMPLE_DIR, check=False
    )
    if result.returncode != 0:
        sys.exit(f"\n{script.name} failed (exit {result.returncode}). Nothing further run.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--reset",
        action="store_true",
        help="delete the example user's data folder before rebuilding it",
    )
    args = parser.parse_args()

    for script in (GENERATE, LOAD_VAULT):
        if not script.is_file():
            sys.exit(f"Missing {script}. Is the goblinvest_core/ subfolder checked out?")

    db.init_db()
    conn = db.connect()
    try:
        user_id = upsert_example_user(conn)
        conn.commit()
    finally:
        conn.close()

    user_dir = storage.user_dir(user_id)
    if args.reset and user_dir.exists():
        print(f"Removing {user_dir}")
        shutil.rmtree(user_dir)
    storage.provision_user_storage(user_id)

    raw_data = storage.raw_data_dir(user_id)
    vault = storage.vault_path(user_id)

    run(GENERATE, "--data-dir", str(raw_data))
    # --overwrite keeps it non-interactive: the script otherwise prompts before
    # replacing an existing vault, and this is meant to be re-runnable.
    run(LOAD_VAULT, "--data-dir", str(raw_data), "--vault", str(vault), "--overwrite")

    print(
        f"\nDone. Log in as {USERNAME} / {PASSWORD}."
        f"\n  data dir:    {settings().data_dir}"
        f"\n  raw data:    {raw_data}"
        f"\n  vault:       {vault}"
        f"\n  adjustments: {storage.adjustments_dir(user_id)}"
    )


if __name__ == "__main__":
    main()
