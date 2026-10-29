import argparse
import logging
import time
from uuid import uuid4

from infra.manage import uid
from services.core.config import Settings
from services.core.db import Database
from services.core.g4 import ai, workflow
from services.core.g4.local_model import LocalModel


def main(lane):
    from services.core.g5.logging import configure

    configure("g4-" + lane)
    db = Database(Settings.load().database_url, max_size=4)
    db.pool.open()
    model = LocalModel()
    owner = str(uuid4())
    try:
        while True:
            from services.core.g5.worker import periodic_heartbeat

            periodic_heartbeat(db, owner, "g4-" + lane)
            for tenant in [uid("tenant-A"), uid("tenant-B")]:
                try:
                    if lane == "ai":
                        ai.tick(db, tenant, owner, model)
                    else:
                        workflow.tick(db, tenant)
                except Exception as exc:
                    logging.getLogger("hotel.g4").error("G4 %s: %s", lane, type(exc).__name__)
            time.sleep(0.3)
    finally:
        model.close()
        db.pool.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--lane", choices=["ai", "flow"], required=True)
    main(parser.parse_args().lane)
