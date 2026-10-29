"""Real process exits only against the exact isolated G4 database."""

import os
import sys
from uuid import UUID

from infra.manage import uid
from services.core.config import Settings
from services.core.db import Database
from services.core.g1.common import get
from services.core.g4.workflow import advance


def main():
    settings = Settings.load()
    if os.environ.get("HOTEL_G1_TEST_MODE") != "true" or not settings.database_url.endswith(
        ("/hotel_project_g4_test", "/hotel_project_g5_test")
    ):
        raise SystemExit("Exact G4 isolated test database required")
    db = Database(settings.database_url)
    db.pool.open()
    window, rid = sys.argv[1:]
    with db.transaction(uid("tenant-A")) as c:
        advance(c, get(c, "g4_run", UUID(rid)))
        if window == "before":
            os._exit(91)
    os._exit(92)


if __name__ == "__main__":
    main()
