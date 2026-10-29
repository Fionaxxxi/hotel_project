import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")


def test_env_path():
    root = Path(__file__).resolve().parents[2] / ".runtime"
    path = Path(os.environ.get("HOTEL_TEST_ENV_PATH", str(root / "test.env"))).resolve()
    if not path.is_relative_to(root.resolve()) or path.suffix != ".env":
        raise ValueError("TEST_ENV_OUTSIDE_PROJECT_RUNTIME")
    return path


if os.environ.get("HOTEL_G1_TEST_MODE") == "true":
    load_dotenv(test_env_path(), override=True)


@dataclass(frozen=True)
class Settings:
    database_url: str = field(repr=False)
    migration_url: str = field(repr=False)
    jwt_secret: str = field(repr=False)
    environment: str
    dev_auth: bool

    @classmethod
    def load(cls):
        if os.environ.get("HOTEL_G1_TEST_MODE") == "true":
            load_dotenv(test_env_path(), override=True)
        settings = cls(
            database_url=os.environ["DATABASE_URL"],
            migration_url=os.environ.get("MIGRATION_DATABASE_URL", ""),
            jwt_secret=os.environ["JWT_SECRET"],
            environment=os.environ.get("APP_ENV", "development"),
            dev_auth=os.environ.get("DEV_AUTH_ENABLED", "false").lower() == "true",
        )
        if len(settings.jwt_secret) < 32:
            raise ValueError("JWT_SECRET must contain at least 32 characters")
        if settings.environment != "development" and settings.dev_auth:
            raise ValueError("Development authentication must be disabled outside development")
        if os.environ.get("G5_ISOLATED_SIMULATORS") == "true":
            from urllib.parse import urlparse

            if not urlparse(settings.database_url).path.startswith("/hotel_project_g5_restore_"):
                raise ValueError("ISOLATED_SIMULATORS_REQUIRE_RESTORE_DATABASE")
        return settings
