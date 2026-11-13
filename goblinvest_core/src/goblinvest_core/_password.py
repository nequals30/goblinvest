"""In-memory password handling: prompt in the terminal, remember briefly,
never accept a password as plaintext in code and never store one on disk."""

import os
import time
from contextvars import ContextVar
from getpass import getpass

_TTL_SECONDS = 15 * 60
_PBKDF2_ITERATIONS = 600_000

# The terminal user's password and the keys derived from it.
_cache: dict = {}

# The password scopes entered in this thread (or async task), innermost last.
# A context variable rather than a module global, so two threads can each be
# inside a different scope at the same time.
_scopes: ContextVar[tuple[dict, ...]] = ContextVar("_scopes", default=())


def _room() -> dict:
    """The dict holding the password in force: the innermost password scope's,
    or the terminal cache when no scope is entered."""
    scopes = _scopes.get()
    return scopes[-1] if scopes else _cache


class _PasswordScope:
    """What `password_scope` returns. Reusable: each `with` makes its password
    the one in force, and the keys derived inside one `with` are still there
    for the next."""

    def __init__(self, password: str):
        if not password:
            raise ValueError("Password cannot be empty")
        self._room = {"password": password, "scoped": True}

    def __enter__(self) -> None:
        _scopes.set(_scopes.get() + (self._room,))

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        _scopes.set(_scopes.get()[:-1])

    def __repr__(self) -> str:
        return "<password scope>"


def password_scope(password: str) -> _PasswordScope:
    """Supply a password from a program instead of a terminal prompt.

    Deliberately left out of the documentation site: this is for the web UI,
    not for people using the library from a script or the REPL.

    !!! warning "For programs only"
        This exists for programs that have no terminal to prompt at, such as a
        web server holding a password its user typed into a form. Don't type a
        password into a script, a notebook or the REPL — it ends up in shell
        history, in files, and in git. At a terminal, let
        [`Vault.open`][goblinvest_core.Vault.open] prompt you, or use
        [`ask_password`][goblinvest_core.ask_password].

    The returned object is used in a `with` block. Inside the block, every
    call that needs a password uses this one and never prompts: opening or
    creating an encrypted vault, and reading or writing encrypted files. It
    takes the place of the remembered password for the length of the block
    only, and only in the thread that entered it, so a program can hold
    several people's passwords and use them at the same time.

    Keep the object and reuse it rather than making a new one each time.
    Turning a password into a key is deliberately slow, and the keys worked
    out inside one `with` block are kept on the object for the next, so
    reopening a vault with it costs about what opening an unencrypted one
    does.

    A scope does not expire. If a password turns out to be wrong, the call
    that found out raises `ValueError` and the scope forgets its password;
    any later use of it raises `ValueError` too, instead of prompting.

    Returns:
        A reusable context manager.

    Raises:
        ValueError: The password is empty.

    Examples:
        ```python
        scope = password_scope(password_from_login_form)

        with scope:
            with Vault.open("vault.db") as v:
                transactions = v.list_transactions()
        ```
    """
    return _PasswordScope(password)


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
    room = _room()
    scoped = room.get("scoped", False)
    room.clear()
    if scoped:
        room["scoped"] = True


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
    room = _room()
    if room.get("scoped"):
        if "password" not in room:
            raise ValueError("This password scope's password was wrong and has been forgotten")
        return room["password"]
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
    room = _room()
    if salt is None:
        salt = room.setdefault("session_salt", os.urandom(16))
    keys = room.setdefault("keys", {})
    if salt not in keys:
        # Imported here to keep `import goblinvest_core` fast.
        from cryptography.hazmat.primitives.hashes import SHA256
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

        kdf = PBKDF2HMAC(algorithm=SHA256(), length=32, salt=salt, iterations=_PBKDF2_ITERATIONS)
        keys[salt] = kdf.derive(password.encode())
    return salt, keys[salt]


def _get_vault_key(salt: bytes) -> bytes:
    """Return the raw SQLCipher key for a vault file whose first 16 bytes are
    `salt`, derived from the password in force exactly as SQLCipher derives it
    from a passphrase.

    Cached per salt beside the file keys, so a repeat open hands SQLCipher the
    finished key and skips its own derivation.
    """
    password = _get_password(confirm=False)
    keys = _room().setdefault("vault_keys", {})
    if salt not in keys:
        import hashlib

        keys[salt] = hashlib.pbkdf2_hmac(
            "sha512", password.encode(), salt, _SQLCIPHER_KDF_ITERATIONS, 32
        )
    return keys[salt]


def _drop_vault_key(salt: bytes) -> None:
    _room().get("vault_keys", {}).pop(salt, None)


# SQLCipher 4's defaults (`PRAGMA cipher_default_kdf_iter`, PBKDF2-HMAC-SHA512).
# A vault is always created with them; if they ever change, the raw key stops
# opening vaults and `vault._connect` falls back to the passphrase, so a
# mismatch costs speed, never a vault.
_SQLCIPHER_KDF_ITERATIONS = 256_000
