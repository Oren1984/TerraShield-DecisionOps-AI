"""Password hashing (Argon2id) and minimal password policy."""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()

MIN_LENGTH = 12


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def password_problems(password: str, username: str) -> list[str]:
    problems = []
    if len(password) < MIN_LENGTH:
        problems.append(f"Password must be at least {MIN_LENGTH} characters.")
    if len(password) > 256:
        problems.append("Password must be at most 256 characters.")
    if username and username.lower() in password.lower():
        problems.append("Password must not contain the username.")
    return problems


# Pre-computed hash used to equalise timing when a username does not exist.
DUMMY_HASH = _hasher.hash("timing-equaliser-not-a-credential")
