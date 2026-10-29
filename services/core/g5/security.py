"""Small explicit security utilities; never log submitted values."""

import base64
import hashlib
import hmac
import os


def password_hash(password, salt=None):
    salt = salt or os.urandom(16)
    value = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310000)
    return (
        "pbkdf2_sha256$310000$"
        + base64.b64encode(salt).decode()
        + "$"
        + base64.b64encode(value).decode()
    )


def verify_password(password, encoded):
    try:
        kind, rounds, salt, expected = encoded.split("$")
        if kind != "pbkdf2_sha256" or not 100000 <= int(rounds) <= 1000000:
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), base64.b64decode(salt, validate=True), int(rounds)
        )
        return hmac.compare_digest(actual, base64.b64decode(expected, validate=True))
    except (ValueError, TypeError):
        return False


def safe_csv(value):
    text = str(value)
    index = 0
    while index < len(text) and (
        text[index].isspace() or ord(text[index]) < 32 or text[index] == "\ufeff"
    ):
        index += 1
    if text[index:].startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


SECRET_KEYS = {
    "password",
    "secret",
    "token",
    "authorization",
    "storage_key",
    "database_url",
    "access_token",
    "jwt_secret",
}


def redact(value):
    if isinstance(value, dict):
        return {
            k: "[REDACTED]" if any(x in k.lower() for x in SECRET_KEYS) else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(x) for x in value]
    return value
