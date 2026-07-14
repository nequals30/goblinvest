import pandas as pd
import pytest

from goblinvest_core import (
    Vault,
    ask_password,
    decrypt_file,
    encrypt_file,
    forget_password,
    read_encrypted_file,
)
from goblinvest_core import _password

CSV = b"date,description,amount\n2026-07-01,coffee,-5.00\n2026-07-03,groceries,-40.00\n"


@pytest.fixture
def csv_file(tmp_path):
    f = tmp_path / "chase_2026-06.csv"
    f.write_bytes(CSV)
    return f


@pytest.fixture(autouse=True)
def forget_between_tests():
    forget_password()
    yield
    forget_password()


def _typed(monkeypatch, *entries):
    """Make the hidden password prompt 'type' these entries, in order."""
    it = iter(entries)
    monkeypatch.setattr(_password, "getpass", lambda prompt="": next(it))


def _no_prompt(monkeypatch):
    def fail(prompt=""):
        raise AssertionError("password prompt should not have appeared")

    monkeypatch.setattr(_password, "getpass", fail)


def test_roundtrip(csv_file, monkeypatch):
    _typed(monkeypatch, "hunter2", "hunter2")  # encrypt: enter + confirm
    encrypt_file(csv_file)

    on_disk = csv_file.read_bytes()
    assert on_disk != CSV
    assert b"coffee" not in on_disk  # nothing readable leaks through

    assert read_encrypted_file(csv_file).read() == CSV


def test_reading_never_rewrites_the_file(csv_file, monkeypatch):
    _typed(monkeypatch, "hunter2", "hunter2")
    encrypt_file(csv_file)
    on_disk = csv_file.read_bytes()

    read_encrypted_file(csv_file)
    read_encrypted_file(csv_file)

    assert csv_file.read_bytes() == on_disk  # byte-for-byte stable: git stays quiet


def test_plugs_into_pandas(csv_file, monkeypatch):
    _typed(monkeypatch, "hunter2", "hunter2")
    encrypt_file(csv_file)

    df = pd.read_csv(read_encrypted_file(csv_file))
    assert df["description"].tolist() == ["coffee", "groceries"]
    assert df["amount"].tolist() == [-5.00, -40.00]


def test_decrypt_file_restores_exact_bytes(csv_file, monkeypatch):
    _typed(monkeypatch, "hunter2", "hunter2")
    encrypt_file(csv_file)
    decrypt_file(csv_file)

    assert csv_file.read_bytes() == CSV


def test_one_password_entry_covers_many_files(tmp_path, monkeypatch):
    files = [tmp_path / f"statement_{i}.csv" for i in range(3)]
    for f in files:
        f.write_bytes(CSV)

    _typed(monkeypatch, "hunter2", "hunter2")
    encrypt_file(files[0])
    _no_prompt(monkeypatch)  # still remembered: no further prompts
    for f in files[1:]:
        encrypt_file(f)
    for f in files:
        assert read_encrypted_file(f).read() == CSV


def test_same_password_memory_as_the_vault(tmp_path, monkeypatch):
    _typed(monkeypatch, "hunter2", "hunter2")
    ask_password()

    _no_prompt(monkeypatch)
    Vault.create(tmp_path / "Vault.db", encrypted=True).close()
    f = tmp_path / "statement.csv"
    f.write_bytes(CSV)
    encrypt_file(f)
    assert read_encrypted_file(f).read() == CSV


def test_wrong_password_fails_loudly_and_is_forgotten(csv_file, monkeypatch):
    _typed(monkeypatch, "hunter2", "hunter2")
    encrypt_file(csv_file)
    forget_password()

    _typed(monkeypatch, "wrong")
    with pytest.raises(ValueError, match="wrong password"):
        read_encrypted_file(csv_file)

    # The wrong password was not kept: the next read prompts again.
    _typed(monkeypatch, "hunter2")
    assert read_encrypted_file(csv_file).read() == CSV


def test_encrypted_file_is_plain_text(csv_file, monkeypatch):
    _typed(monkeypatch, "hunter2", "hunter2")
    encrypt_file(csv_file)

    text = csv_file.read_bytes().decode("ascii")  # decodable: it's text, not binary
    lines = text.splitlines()
    assert lines[0] == "GVENC1"
    assert max(len(line) for line in lines) <= 76  # wrapped like PEM/PGP armor


def test_survives_editor_appending_newlines(csv_file, monkeypatch):
    _typed(monkeypatch, "hunter2", "hunter2")
    encrypt_file(csv_file)
    csv_file.write_bytes(csv_file.read_bytes() + b"\n\n")

    assert read_encrypted_file(csv_file).read() == CSV


def test_survives_crlf_line_ending_conversion(csv_file, monkeypatch):
    _typed(monkeypatch, "hunter2", "hunter2")
    encrypt_file(csv_file)
    csv_file.write_bytes(csv_file.read_bytes().replace(b"\n", b"\r\n"))

    assert read_encrypted_file(csv_file).read() == CSV


def test_survives_rewrapped_lines(csv_file, monkeypatch):
    _typed(monkeypatch, "hunter2", "hunter2")
    encrypt_file(csv_file)
    header, body = csv_file.read_bytes().split(b"\n", 1)
    csv_file.write_bytes(header + b"\n" + body.replace(b"\n", b""))  # one long line

    assert read_encrypted_file(csv_file).read() == CSV


def test_tampered_content_is_detected(csv_file, monkeypatch):
    _typed(monkeypatch, "hunter2", "hunter2")
    encrypt_file(csv_file)

    blob = bytearray(csv_file.read_bytes())
    i = 20  # inside the base64 payload
    blob[i] = ord("B") if blob[i] != ord("B") else ord("C")
    csv_file.write_bytes(bytes(blob))

    _typed(monkeypatch, "hunter2")
    with pytest.raises(ValueError, match="modified"):
        read_encrypted_file(csv_file)


def test_truncated_file_is_detected(csv_file, monkeypatch):
    _typed(monkeypatch, "hunter2", "hunter2")
    encrypt_file(csv_file)
    csv_file.write_bytes(csv_file.read_bytes()[:20])

    with pytest.raises(ValueError, match="corrupted"):
        read_encrypted_file(csv_file)


def test_encrypting_twice_refuses(csv_file, monkeypatch):
    _typed(monkeypatch, "hunter2", "hunter2")
    encrypt_file(csv_file)

    with pytest.raises(ValueError, match="already encrypted"):
        encrypt_file(csv_file)


def test_reading_a_plaintext_file_refuses(csv_file, monkeypatch):
    _no_prompt(monkeypatch)
    with pytest.raises(ValueError, match="not encrypted"):
        read_encrypted_file(csv_file)
    with pytest.raises(ValueError, match="not encrypted"):
        decrypt_file(csv_file)


def test_missing_file(tmp_path, monkeypatch):
    _no_prompt(monkeypatch)
    with pytest.raises(FileNotFoundError):
        encrypt_file(tmp_path / "nope.csv")
    with pytest.raises(FileNotFoundError):
        read_encrypted_file(tmp_path / "nope.csv")


def test_unknown_format_version(csv_file, monkeypatch):
    _typed(monkeypatch, "hunter2", "hunter2")
    encrypt_file(csv_file)
    csv_file.write_bytes(csv_file.read_bytes().replace(b"GVENC1", b"GVENC2", 1))

    with pytest.raises(ValueError, match="newer version"):
        read_encrypted_file(csv_file)


def test_arbitrary_binary_content(tmp_path, monkeypatch):
    f = tmp_path / "blob.bin"
    payload = bytes(range(256)) * 3
    f.write_bytes(payload)

    _typed(monkeypatch, "hunter2", "hunter2")
    encrypt_file(f)
    assert read_encrypted_file(f).read() == payload
