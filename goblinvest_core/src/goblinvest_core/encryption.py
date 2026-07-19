"""Encrypt files at rest — statement CSVs above all — so a private git repo of
financial history is unreadable without the password."""

import base64
import binascii
import io
import os
import re
from pathlib import Path

from goblinvest_core._password import _get_key, forget_password

# An encrypted file is plain text: a "GVENC1" header line, then the payload
# (16-byte salt + 12-byte nonce + AES-256-GCM ciphertext) as base64 lines.
# Text survives text tools: reading ignores all whitespace, so appended
# newlines, CRLF/LF conversion, and re-wrapped lines can never damage a file.
_MAGIC = b"GVENC"
_VERSION = 1
_SALT_LEN = 16
_NONCE_LEN = 12
_TAG_LEN = 16


def _write_atomically(filepath: Path, data: bytes) -> None:
    # A crash mid-write must never destroy the only copy of a statement:
    # write a sibling temp file, then atomically swap it into place.
    tmp = filepath.with_name(filepath.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, filepath)


def encrypt_file(filepath: str | Path) -> None:
    """Encrypt a file in place.

    The file's contents become unreadable without your password, and can be
    recovered with [`decrypt_file`][goblinvest_core.decrypt_file] or read
    with [`read_encrypted_file`][goblinvest_core.read_encrypted_file]. It is
    the same password the vault uses, remembered for 15 minutes by
    [`ask_password`][goblinvest_core.ask_password]; if none is remembered you
    are prompted (and asked to confirm, since a typo here would encrypt the
    file under a password you don't know).

    The encrypted file is stored as plain text, so the small liberties other
    programs take with text files — an editor adding a newline when you
    save, git converting line endings between operating systems — do it no
    harm.

    Args:
        filepath: File to encrypt, e.g. ``"statements/chase_2026-06.csv"``.
            ``~`` is expanded. Any file works — contents are treated as bytes.

    Returns:
        Nothing.

    Raises:
        FileNotFoundError: No file exists at ``filepath``.
        ValueError: The file is already encrypted.

    Examples:
        ```python
        from goblinvest_core import encrypt_file

        encrypt_file("statements/chase_2026-06.csv")
        ```
    """
    filepath = Path(filepath).expanduser()
    plaintext = filepath.read_bytes()
    if plaintext.startswith(_MAGIC):
        raise ValueError(f"{filepath} is already encrypted")

    salt, key = _get_key(None, confirm=True)
    # Imported here to keep `import goblinvest_core` fast.
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = os.urandom(_NONCE_LEN)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, None)
    armored = (
        _MAGIC + str(_VERSION).encode() + b"\n" + base64.encodebytes(salt + nonce + ciphertext)
    )
    _write_atomically(filepath, armored)


def _decrypt(filepath: str | Path) -> bytes:
    filepath = Path(filepath).expanduser()
    blob = filepath.read_bytes()
    if not blob.startswith(_MAGIC):
        raise ValueError(f"{filepath} is not encrypted (or not by this package)")

    first_line, _, body = blob.partition(b"\n")
    version = first_line[len(_MAGIC) :].strip()
    if not version.isdigit():
        raise ValueError(f"{filepath} is not encrypted (or not by this package)")
    if int(version) != _VERSION:
        raise ValueError(
            f"{filepath} was encrypted by a newer version of this package "
            f"(format {int(version)}); upgrade goblinvest-core to read it"
        )

    try:
        payload = base64.b64decode(re.sub(rb"\s+", b"", body), validate=True)
    except binascii.Error:
        raise ValueError(
            f"{filepath} is corrupted: the encrypted payload is not readable"
        ) from None
    if len(payload) < _SALT_LEN + _NONCE_LEN + _TAG_LEN:
        raise ValueError(f"{filepath} is corrupted: the encrypted payload is incomplete")

    salt = payload[:_SALT_LEN]
    nonce = payload[_SALT_LEN : _SALT_LEN + _NONCE_LEN]
    ciphertext = payload[_SALT_LEN + _NONCE_LEN :]

    _, key = _get_key(salt, confirm=False)
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    try:
        return AESGCM(key).decrypt(nonce, ciphertext, None)
    except InvalidTag:
        forget_password()
        raise ValueError(
            f"Cannot decrypt {filepath}: wrong password, or the file was modified"
        ) from None


def read_encrypted_file(filepath: str | Path) -> io.BytesIO:
    """Decrypt a file into memory, leaving the file on disk untouched.

    The file is opened read-only — its bytes on disk never change, so a git
    repository of encrypted statements stays clean no matter how often they
    are read. The password comes from the same 15-minute memory as the
    vault's; you are prompted if none is remembered.

    Reading is forgiving about form and strict about content. Cosmetic
    changes to the file (an added newline, converted line endings) are
    ignored entirely. But the content itself is verified: a wrong password
    or a genuinely damaged file fails with a clear error rather than
    yielding garbage rows. After a failure the remembered password is
    forgotten, so the next attempt prompts again.

    Args:
        filepath: An encrypted file, e.g. ``"statements/chase_2026-06.csv"``.
            ``~`` is expanded.

    Returns:
        An in-memory binary buffer (``io.BytesIO``) of the decrypted
        contents. Anything that accepts a file also accepts this — most
        usefully ``pd.read_csv``.

    Raises:
        FileNotFoundError: No file exists at ``filepath``.
        ValueError: The file is not encrypted, the password is wrong, or the
            file is corrupted or was modified.

    Examples:
        ```python
        import pandas as pd
        from goblinvest_core import read_encrypted_file

        df = pd.read_csv(read_encrypted_file("statements/chase_2026-06.csv"))
        ```
    """
    return io.BytesIO(_decrypt(filepath))


def decrypt_file(filepath: str | Path) -> None:
    """Decrypt a file in place, writing the plaintext back to disk.

    The escape hatch for editing: decrypt, fix the CSV, then
    [`encrypt_file`][goblinvest_core.encrypt_file] it again. For *loading*
    statements, use
    [`read_encrypted_file`][goblinvest_core.read_encrypted_file] instead — it
    never modifies the file.

    Args:
        filepath: An encrypted file. ``~`` is expanded.

    Returns:
        Nothing.

    Raises:
        FileNotFoundError: No file exists at ``filepath``.
        ValueError: The file is not encrypted, the password is wrong, or the
            file is corrupted or was modified.

    Examples:
        ```python
        from goblinvest_core import decrypt_file, encrypt_file

        decrypt_file("statements/chase_2026-06.csv")
        # ...fix the bad row in a text editor...
        encrypt_file("statements/chase_2026-06.csv")
        ```
    """
    filepath = Path(filepath).expanduser()
    _write_atomically(filepath, _decrypt(filepath))
