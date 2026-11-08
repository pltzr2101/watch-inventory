"""Passwortbehandlung (Argon2id) und kleine Security-Helfer."""
from __future__ import annotations

import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import (
    InvalidHashError,
    VerificationError,
    VerifyMismatchError,
)

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    """Erzeugt einen Argon2id-Hash (für ADMIN_PASSWORD_HASH)."""
    if len(password) < 8:
        raise ValueError("Passwort muss mindestens 8 Zeichen haben.")
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, VerifyMismatchError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return False


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_token_equal(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    return hmac.compare_digest(a, b)


def _main() -> None:
    import sys

    if len(sys.argv) != 2:
        print("Aufruf: python -m app.security 'mein-passwort'", file=sys.stderr)
        raise SystemExit(2)
    print(hash_password(sys.argv[1]))


if __name__ == "__main__":
    _main()
