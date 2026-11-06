"""The owner password (FR-001, research R10): one Argon2id hash in the settings table.

`dentalsim studio set-password` stores it, sign-in checks it, and `dentalsim studio serve` refuses to start while
there is none. A password needs at least 10 characters (FR-001).
"""

from __future__ import annotations

import logging
from typing import Protocol

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from dentalsim.errors import Refused
from dentalsim.persistence import SettingsRepo

logger = logging.getLogger(__name__)

PASSWORD_HASH_KEY = "auth.password_hash"
MIN_LENGTH = 10
SET_PASSWORD_COMMAND = "uv run dentalsim studio set-password"

# argon2-cffi's defaults: Argon2id with the RFC 9106 low-memory parameters.
_hasher = PasswordHasher()


class HasSettings(Protocol):
    @property
    def settings(self) -> SettingsRepo: ...


def check_new_password(password: str) -> None:
    """Refuse a password that is too short."""
    if len(password) < MIN_LENGTH:
        raise Refused(
            f"The owner password must be at least {MIN_LENGTH} characters long. Nothing was changed.",
            code="validation_failed",
        )


def set_password(repos: HasSettings, password: str) -> None:
    check_new_password(password)
    repos.settings.set(PASSWORD_HASH_KEY, _hasher.hash(password))


def password_is_set(repos: HasSettings) -> bool:
    return isinstance(repos.settings.get(PASSWORD_HASH_KEY), str)


def verify_password(repos: HasSettings, password: str) -> bool:
    """True when `password` is the owner's. Rehashes it when the stored hash used older parameters."""
    stored = repos.settings.get(PASSWORD_HASH_KEY)
    if not isinstance(stored, str):
        return False
    try:
        _hasher.verify(stored, password)
    except VerificationError:
        return False
    except InvalidHashError:
        logger.error("the stored owner password hash is not a valid Argon2 hash; set the password again")
        return False
    if _hasher.check_needs_rehash(stored):
        repos.settings.set(PASSWORD_HASH_KEY, _hasher.hash(password))
    return True
