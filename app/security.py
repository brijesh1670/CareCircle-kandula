"""Passcodes, password hashing, signed session tokens and brute-force throttling."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Callable

import jwt

# Unambiguous alphabet (no 0/O, 1/I/L) so codes are easy to read and type.
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_PASSCODE_RE = re.compile(r"^([A-Z0-9]{4})-([A-Z0-9]{10})$")
_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}


def _rand(length: int) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def new_profile_code() -> str:
    return _rand(8)


def new_tag() -> str:
    return _rand(4)


def new_passcode(tag: str) -> str:
    """'TAG-SECRET': the tag finds the member, the ~50-bit secret proves identity."""
    return f"{tag}-{_rand(10)}"


def split_passcode(raw: str) -> tuple[str, str] | None:
    m = _PASSCODE_RE.match(raw.strip().upper())
    return (m.group(1), m.group(2)) if m else None


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def hash_passcode(passcode: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(passcode.encode(), salt=salt, **_SCRYPT)
    return f"scrypt${_b64(salt)}${_b64(digest)}"


def verify_passcode(passcode: str, stored: str) -> bool:
    try:
        scheme, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        candidate = hashlib.scrypt(passcode.encode(), salt=base64.b64decode(salt), **_SCRYPT)
        return hmac.compare_digest(candidate, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


# Verified against when the member is unknown, so timing does not reveal valid tags.
DUMMY_HASH = hash_passcode("timing-equaliser")


def issue_token(secret: str, member_id: int, profile_id: int, version: int,
                ttl_minutes: int, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    claims = {"sub": str(member_id), "pid": profile_id, "ver": version,
              "iat": now, "exp": now + timedelta(minutes=ttl_minutes)}
    return jwt.encode(claims, secret, algorithm="HS256")


def decode_token(secret: str, token: str) -> dict | None:
    try:
        return jwt.decode(token, secret, algorithms=["HS256"], options={"require": ["exp", "sub"]})
    except jwt.PyJWTError:
        return None


class LoginThrottle:
    """Locks a (profile, client) pair after repeated failures. In-memory: use a shared
    store (Redis / Db2 table) if you run more than one server process."""

    def __init__(self, max_failures: int, lockout_seconds: int,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._max, self._lockout, self._clock = max_failures, lockout_seconds, clock
        self._state: dict[str, tuple[int, float]] = {}  # key -> (failures, locked_until)
        self._lock = threading.Lock()

    def retry_after(self, key: str) -> int:
        with self._lock:
            _, locked_until = self._state.get(key, (0, 0.0))
            return max(0, int(locked_until - self._clock() + 0.999))

    def failure(self, key: str) -> None:
        with self._lock:
            now = self._clock()
            count, locked_until = self._state.get(key, (0, 0.0))
            if locked_until and locked_until <= now:
                count = 0  # previous lockout expired
            count += 1
            locked_until = now + self._lockout if count >= self._max else 0.0
            self._state[key] = (count, locked_until)
            if len(self._state) > 10_000:  # bound memory
                self._state = {k: v for k, v in self._state.items() if v[1] > now}

    def success(self, key: str) -> None:
        with self._lock:
            self._state.pop(key, None)
