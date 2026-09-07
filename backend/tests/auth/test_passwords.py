"""Password hashing contracts."""

import pytest
from argon2 import PasswordHasher
from argon2.low_level import Type

from app.auth.passwords import hash_password, verify_password


def test_password_hash_uses_argon2id_and_verifies() -> None:
    """Changing the hasher away from Argon2id must break credential verification."""

    encoded = hash_password("correct horse battery staple")

    assert encoded.startswith("$argon2id$")
    assert verify_password("correct horse battery staple", encoded)
    assert not verify_password("wrong", encoded)


def test_malformed_password_hash_is_not_accepted() -> None:
    """Changing malformed-hash handling to raise or accept it would be a security bug."""

    assert not verify_password("password", "not-an-argon2-hash")


@pytest.mark.parametrize("hash_type", [Type.I, Type.D])
def test_password_verification_rejects_non_argon2id_hashes(hash_type: Type) -> None:
    """Accepting Argon2i or Argon2d would weaken the explicit Argon2id policy."""

    encoded = PasswordHasher(type=hash_type).hash("correct horse battery staple")

    assert not verify_password("correct horse battery staple", encoded)
