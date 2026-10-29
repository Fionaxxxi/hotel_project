from datetime import datetime, timedelta, timezone

import jwt
from fastapi import HTTPException

ISSUER = "hotel-project-dev"
AUDIENCE = "hotel-core"
ROLES = {"tenant_admin", "operator", "reviewer", "auditor"}


def issue_token(subject: str, secret: str, expires_minutes=30):
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": subject,
            "iss": ISSUER,
            "aud": AUDIENCE,
            "iat": now,
            "exp": now + timedelta(minutes=expires_minutes),
        },
        secret,
        algorithm="HS256",
    )


def decode_token(token: str, secret: str):
    try:
        return jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            issuer=ISSUER,
            audience=AUDIENCE,
            options={"require": ["sub", "exp", "iat"]},
        )["sub"]
    except jwt.InvalidTokenError:
        raise HTTPException(401, "INVALID_TOKEN") from None


def require_role(role: str, allowed: set[str]):
    if role not in allowed:
        raise HTTPException(403, "FORBIDDEN")
