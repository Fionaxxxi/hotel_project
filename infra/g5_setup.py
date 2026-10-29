"""Append missing local hash verifier without replacing existing local credentials."""

import os

from dotenv import load_dotenv

from infra.manage import ROOT
from services.core.g5.security import password_hash, verify_password


def main():
    path = ROOT / ".env"
    load_dotenv(path)
    if not os.environ.get("DEV_LOGIN_PASSWORD_HASH"):
        plain = os.environ.get("DEV_LOGIN_PASSWORD", "")
        if not plain:
            raise ValueError("PROVIDE_LOCAL_DEVELOPMENT_CREDENTIAL_BEFORE_SETUP")
        with path.open("a", encoding="utf-8") as f:
            f.write("\nDEV_LOGIN_PASSWORD_HASH=" + password_hash(plain) + "\n")
        print(
            "Development PBKDF2 verifier prepared; existing excluded local demo credential retained for operator scripts"
        )
    else:
        if os.environ.get("DEV_LOGIN_PASSWORD") and not verify_password(
            os.environ["DEV_LOGIN_PASSWORD"], os.environ["DEV_LOGIN_PASSWORD_HASH"]
        ):
            raise ValueError("LOCAL_DEMO_CREDENTIAL_AND_VERIFIER_DIFFER")
        print("Development verifier already present")


if __name__ == "__main__":
    main()
