"""Where each user's files live on disk.

    data/
      goblinvest.db
      users/<user_id>/
        vault.db              # created when goblinvest-core is wired in
        vault_adjustments/    # ditto
        statements/           # the user's statement CSVs

Nothing here imports goblinvest_core or knows anything about finance — it only
owns the layout. `vault.db` and `vault_adjustments/` are named, not created:
`Vault.create(vault_path(uid))` will make both, because core derives its default
adjustments folder as `<stem>_adjustments` beside the vault file.
"""

from pathlib import Path

from goblinvest.config import settings

VAULT_FILENAME = "vault.db"
STATEMENTS_DIRNAME = "statements"


def user_dir(user_id: int) -> Path:
    return settings().users_dir / str(user_id)


def vault_path(user_id: int) -> Path:
    return user_dir(user_id) / VAULT_FILENAME


def adjustments_dir(user_id: int) -> Path:
    """The folder core will put the adjustments CSVs in, given `vault_path`."""
    return user_dir(user_id) / f"{Path(VAULT_FILENAME).stem}_adjustments"


def statements_dir(user_id: int) -> Path:
    return user_dir(user_id) / STATEMENTS_DIRNAME


def provision_user_storage(user_id: int) -> Path:
    """Create the directories a new user needs. Idempotent."""
    statements_dir(user_id).mkdir(parents=True, exist_ok=True)
    return user_dir(user_id)
