"""In-memory password handling: prompt in the terminal, remember briefly,
never accept a password as plaintext in code and never store one on disk."""

import os
import time
from getpass import getpass

_TTL_SECONDS = 15 * 60
_PBKDF2_ITERATIONS = 600_000

_cache: dict = {}


def ask_password() -> None:
    """Prompt for a password and remember it for the next 15 minutes.

    The password is typed at a hidden terminal prompt (nothing is echoed) and
    asked twice to catch typos. For the next 15 minutes, anything that needs
    it — opening or creating an encrypted vault — uses the remembered password
    instead of prompting again.

    The password is held only in this process's memory: it is never written to
    disk, and it never appears in your scripts or your shell history. Calling
    this up front is optional — anything that needs a password will prompt for
    one on its own if none is remembered.

    Returns:
        Nothing.

    Raises:
        ValueError: The two entries do not match, or the password is empty.

    Examples:
        ```python
        from goblinvest_core import Vault, ask_password

        ask_password()                # type it once...
        v = Vault.open("~/finance/MyVault.db")   # ...no prompt here
        ```
    """
    password = getpass("Enter password: ")
    confirm = getpass("Confirm password: ")
    if password != confirm:
        raise ValueError("Passwords do not match")
    _remember(password)


def forget_password() -> None:
    """Immediately forget the remembered password.

    The next thing that needs a password will prompt for it again. (Without
    this call, a remembered password expires on its own 15 minutes after it
    was entered.)

    Returns:
        Nothing.
    """
    _cache.clear()


def _remember(password: str) -> None:
    if not password:
        raise ValueError("Password cannot be empty")
    _cache["password"] = password
    _cache["expires_at"] = time.monotonic() + _TTL_SECONDS


def _get_password(*, confirm: bool) -> str:
    """Return the remembered password, prompting for one if there isn't any.

    confirm=True prompts twice (setting a brand-new password); confirm=False
    prompts once (an existing encrypted file will verify it anyway).
    """
    if "password" in _cache and time.monotonic() < _cache["expires_at"]:
        return _cache["password"]
    _cache.clear()
    if confirm:
        ask_password()
    else:
        _remember(getpass("Enter password: "))
    return _cache["password"]


def _get_key(salt: bytes | None, *, confirm: bool) -> tuple[bytes, bytes]:
    """Return (salt, 32-byte key) derived from the remembered password.

    Derivation is deliberately slow (PBKDF2), so keys are cached per salt for
    the password's lifetime — reading a pile of files derives once, not once
    per file. salt=None means "the session salt": one random salt per
    remembered password, shared by everything encrypted in that session.
    """
    password = _get_password(confirm=confirm)
    if salt is None:
        salt = _cache.setdefault("session_salt", os.urandom(16))
    keys = _cache.setdefault("keys", {})
    if salt not in keys:
        # Imported here to keep `import goblinvest_core` fast.
        from cryptography.hazmat.primitives.hashes import SHA256
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

        kdf = PBKDF2HMAC(
            algorithm=SHA256(), length=32, salt=salt, iterations=_PBKDF2_ITERATIONS
        )
        keys[salt] = kdf.derive(password.encode())
    return salt, keys[salt]
