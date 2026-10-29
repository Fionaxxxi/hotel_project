"""Database-backed per-peer and per-account attempt limits, shared by processes."""

import hashlib
import os

from fastapi import HTTPException

from services.core.g5.security import password_hash, verify_password


def login_allowed(db, peer, subject):
    keys = sorted(
        [
            hashlib.sha256(("peer:" + peer).encode()).hexdigest(),
            hashlib.sha256(("subject:" + subject).encode()).hexdigest(),
        ]
    )
    blocked = False
    with db.transaction() as c:
        for key in keys:
            row = c.execute(
                "INSERT INTO g5_login_bucket(key,failures) VALUES(%s,1) ON CONFLICT(key) DO UPDATE SET failures=CASE WHEN g5_login_bucket.window_start<clock_timestamp()-interval '1 minute' THEN 1 ELSE g5_login_bucket.failures+1 END, window_start=CASE WHEN g5_login_bucket.window_start<clock_timestamp()-interval '1 minute' THEN clock_timestamp() ELSE g5_login_bucket.window_start END RETURNING failures",
                (key,),
            ).fetchone()
            blocked = blocked or row["failures"] > 20
    if blocked:
        raise HTTPException(429, "LOGIN_RATE_LIMITED")


def credential_hash():
    if os.environ.get("HOTEL_G1_TEST_MODE") == "true" and os.environ.get("DEV_LOGIN_PASSWORD"):
        return password_hash(os.environ["DEV_LOGIN_PASSWORD"])
    encoded = os.environ.get("DEV_LOGIN_PASSWORD_HASH", "")
    if encoded:
        return encoded
    if os.environ.get("HOTEL_G1_TEST_MODE") == "true":
        # Prior isolated tests explicitly replace their fixture credential at request time.
        plain = os.environ.get("DEV_LOGIN_PASSWORD", "")
        return password_hash(plain) if plain else ""
    raise ValueError("DEV_LOGIN_PASSWORD_HASH_REQUIRED_RUN_G5_SETUP")


def valid(password, encoded):
    return bool(encoded) and verify_password(password, encoded)
