"""Tests for extracted authentication primitives."""

from services.auth import hash_password, verify_password


def test_password_hash_round_trip():
    hashed = hash_password("correct horse battery staple")
    assert hashed != "correct horse battery staple"
    assert verify_password("correct horse battery staple", hashed)
    assert not verify_password("wrong", hashed)


def test_password_hash_limits_bcrypt_input_to_bytes():
    password = "x" * 100
    hashed = hash_password(password)
    assert verify_password(password, hashed)
    assert verify_password(password[:72], hashed)
