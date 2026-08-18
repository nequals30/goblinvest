"""Environment-driven settings.

Read once and cached. Tests reset the cache with `settings.cache_clear()` after
pointing GV_DATA_DIR at a tmp_path.
"""

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

# src/goblinvest/config.py -> repo root
REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    is_prod: bool
    pbkdf2_iters: int
    session_ttl_days: int

    @property
    def db_path(self) -> Path:
        return self.data_dir / "goblinvest.db"

    @property
    def users_dir(self) -> Path:
        return self.data_dir / "users"

    def ensure_dirs(self) -> None:
        self.users_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def settings() -> Settings:
    return Settings(
        data_dir=Path(os.environ.get("GV_DATA_DIR", REPO_ROOT / "data")).expanduser(),
        is_prod=os.environ.get("GV_ENV", "dev").lower() == "prod",
        pbkdf2_iters=int(os.environ.get("GV_PBKDF2_ITERS", "600000")),
        session_ttl_days=int(os.environ.get("GV_SESSION_TTL_DAYS", "30")),
    )
