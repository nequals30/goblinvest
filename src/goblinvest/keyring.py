"""Vault passwords, held in this process's memory and nowhere else.

An encrypted vault needs a password on every open, and core's normal way of
getting one is a terminal prompt — which a web server doesn't have. Core's
`password_scope(password)` is the way in for a program: a reusable context
manager, and inside `with scope:` every core call that needs a password uses
that one and never prompts.

This module keeps **one scope per user**, and `borrow()` enters it around a
vault operation. Scopes are per-thread in core, so two users' requests can each
be inside their own at once; nothing here serializes them.

Two things worth knowing:

- **Keep the scope, don't rebuild it.** Turning a password into a key is
  deliberately slow, and core keeps the keys it derives on the scope object.
  Reusing it is what makes a repeat open cost ~0.5ms instead of ~83ms.
- **Core's scopes never expire, so the idle timeout is ours.** Every `borrow()`
  restarts the clock; `GV_VAULT_TTL_MINUTES` of nobody using it and the scope is
  dropped.

Nothing here is written to disk, and a restart drops every scope.
"""

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from goblinvest.config import settings


class VaultLocked(Exception):
    """This user's vault needs a password that isn't in memory.

    `main.py` turns it into a redirect to `/unlock`, the same way
    `NotAuthenticated` becomes a redirect to `/login`.
    """


@dataclass
class _Held:
    scope: Any  # core's password scope; opaque to us
    expires_at: float


# Guards `_held` only. It is never held while a vault is in use, so requests
# don't wait on each other.
_lock = threading.Lock()
_held: dict[int, _Held] = {}


def _deadline() -> float:
    return time.monotonic() + settings().vault_ttl_minutes * 60


def remember(user_id: int, password: str) -> None:
    """Hold a vault password for this user, replacing any earlier one.

    Nothing is verified here: `vaults.unlock()` is what tries the password
    against the file and drops it again if it doesn't open.
    """
    from goblinvest_core import password_scope

    held = _Held(password_scope(password), _deadline())
    with _lock:
        _held[user_id] = held


def forget(user_id: int) -> None:
    """Lock this user's vault now. Their next vault page asks again."""
    with _lock:
        _held.pop(user_id, None)


def forget_all() -> None:
    """Lock every vault. For tests, and for anything that wants a panic button."""
    with _lock:
        _held.clear()


def _current(user_id: int) -> _Held | None:
    """This user's held password, dropping it first if it has gone idle."""
    with _lock:
        held = _held.get(user_id)
        if held is not None and time.monotonic() >= held.expires_at:
            del _held[user_id]
            return None
        return held


def is_unlocked(user_id: int) -> bool:
    return _current(user_id) is not None


@contextmanager
def borrow(user_id: int) -> Iterator[None]:
    """Make this user's password the one core uses, for the length of the block.

    Raises `VaultLocked` if there isn't one. Using it restarts the idle clock,
    so a session someone is actively working in never re-prompts.
    """
    held = _current(user_id)
    if held is None:
        raise VaultLocked(f"no password held for user {user_id}")
    held.expires_at = _deadline()
    with held.scope:
        yield
