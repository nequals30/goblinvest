import pytest

from goblinvest.auth import hash_password, verify_password


def test_round_trip():
    stored = hash_password("hoardgold", iters=1000)
    assert verify_password("hoardgold", stored)


def test_wrong_password_rejected():
    stored = hash_password("hoardgold", iters=1000)
    assert not verify_password("hoardgolD", stored)


def test_salt_is_random():
    assert hash_password("same", iters=1000) != hash_password("same", iters=1000)


def test_format_records_algo_and_iters():
    algo, iters, salt, dk = hash_password("x", iters=1234).split("$")
    assert algo == "pbkdf2_sha256"
    assert iters == "1234"
    assert salt and dk


@pytest.mark.parametrize(
    "stored",
    ["", "nonsense", "pbkdf2_sha256$1000$onlythree", "bcrypt$1000$c2FsdA$aGFzaA", "a$b$c$d"],
)
def test_malformed_stored_hash_is_false_not_raise(stored):
    assert verify_password("hoardgold", stored) is False
