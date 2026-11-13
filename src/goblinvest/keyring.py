"""Vault passwords, held in this process's memory and nowhere else.

An encrypted vault needs a password on every open, and core's normal way of
getting one is a terminal prompt — which a web server doesn't have. Core's
`password_scope(password)` is the way in for a program: a reusable context
manager, and inside `with scope:` every core call that needs a password uses
that one and never prompts.

This module keeps **one scope per user**, and `borrow()` enters it around a
vault operation. Scopes are per-thread in core, so two users' requests can each
be inside their own at once; nothing here serializes them.

**Logged in means unlocked.** Login puts the password here and logout takes it
away; there is no separate lock and no idle timeout (user, 2026-11-13). Keep the
scope rather than rebuilding it: core keeps the keys it derives on the scope
object, which is what makes a repeat open cost ~0.5ms instead of ~83ms.

Nothing here is written to disk, and a restart drops every scope — which
`main.py` turns into a logout.
"""

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


class VaultLocked(Exception):
    """This user's vault needs a password that isn't in memory.

    `main.py` turns it into a logout: no password in memory means the user
    isn't logged in, as far as their vault is concerned.
    """


# Guards `_held` only. It is never held while a vault is in use, so requests
# don't wait on each other. Values are core's password scopes, opaque to us.
_lock = threading.Lock()
_held: dict[int, Any] = {}


def remember(user_id: int, password: str) -> None:
    """Hold a vault password for this user, replacing any earlier one.

    Nothing is verified here: `vaults.unlock()` is what tries the password
    against the file and drops it again if it doesn't open.
    """
    from goblinvest_core import password_scope

    scope = password_scope(password)
    with _lock:
        _held[user_id] = scope


def forget(user_id: int) -> None:
    """Drop this user's password. Their next vault page logs them out."""
    with _lock:
        _held.pop(user_id, None)


def forget_all() -> None:
    """Lock every vault. For tests, and for anything that wants a panic button."""
    with _lock:
        _held.clear()


def is_unlocked(user_id: int) -> bool:
    with _lock:
        return user_id in _held


@contextmanager
def borrow(user_id: int) -> Iterator[None]:
    """Make this user's password the one core uses, for the length of the block.

    Raises `VaultLocked` if there isn't one.
    """
    with _lock:
        scope = _held.get(user_id)
    if scope is None:
        raise VaultLocked(f"no password held for user {user_id}")
    with scope:
        yield
