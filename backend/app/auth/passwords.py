"""Argon2id password hashing helpers."""

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from argon2.low_level import Type

_password_hasher = PasswordHasher(type=Type.ID)


def hash_password(password: str) -> str:
    """Return an Argon2id password hash without retaining the plaintext."""

    return _password_hasher.hash(password)


def verify_password(password: str, encoded_hash: str) -> bool:
    """Return whether a plaintext password matches an Argon2id hash."""

    if not encoded_hash.startswith("$argon2id$"):
        return False
    try:
        return _password_hasher.verify(encoded_hash, password)
    except (InvalidHashError, VerificationError):
        return False
