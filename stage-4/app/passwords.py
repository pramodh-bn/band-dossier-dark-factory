"""Password storage: scrypt, so no password is ever kept in plaintext (§6)."""
from __future__ import annotations

import hashlib
import hmac
import secrets

SCHEME = "scrypt"
_COST = 2 ** 14
_BLOCK = 8
_PARALLEL = 1
_LENGTH = 32
_SALT_BYTES = 16


def hash_password(password: str) -> str:
    """A self-describing scrypt hash of `password`: `scrypt$<salt>$<digest>`."""
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_COST, r=_BLOCK,
                            p=_PARALLEL, dklen=_LENGTH)
    return f"{SCHEME}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Whether `password` matches a hash produced by :func:`hash_password`."""
    try:
        scheme, salt_hex, digest_hex = stored.split("$")
        if scheme != SCHEME:
            return False
        digest = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt_hex),
                                n=_COST, r=_BLOCK, p=_PARALLEL, dklen=_LENGTH)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


def new_token() -> str:
    """An opaque bearer token."""
    return secrets.token_urlsafe(32)
