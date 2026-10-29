import os
from pathlib import Path

import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient

from services.core.config import Settings
from services.core.main import create_app

if os.environ.get("HOTEL_G1_TEST_MODE") == "true":
    load_dotenv(Path(__file__).resolve().parents[1] / ".runtime/test.env", override=True)


@pytest.fixture
def settings():
    if os.environ.get("HOTEL_G1_TEST_MODE") != "true":
        pytest.fail("Database tests require isolated mode: run infra/scripts/g1.ps1 test")
    settings = Settings.load()
    from urllib.parse import urlparse

    if urlparse(settings.database_url).path not in {
        "/hotel_project_test",
        "/hotel_project_g2_test",
        "/hotel_project_g3_test",
        "/hotel_project_g4_test",
        "/hotel_project_g5_test",
    }:
        pytest.fail("Refusing database tests outside hotel_project_test")
    return settings


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as client:
        yield client
