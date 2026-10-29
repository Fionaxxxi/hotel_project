import logging
import time
from uuid import uuid4

from psycopg import OperationalError
from psycopg_pool import PoolTimeout

from services.core.config import Settings
from services.core.db import Database
from services.core.g3.engine import tick


def main():
    from services.core.g5.logging import configure

    configure("g3-distribution")
    db = Database(Settings.load().database_url, max_size=6)
    db.pool.open()
    owner = str(uuid4())
    try:
        while True:
            from services.core.g5.worker import periodic_heartbeat

            try:
                periodic_heartbeat(db, owner, "g3-distribution")
                tick(db, owner)
            except (PoolTimeout, OperationalError) as exc:
                # Failed transactions roll back; durable leases/fences decide
                # whether work can be retried. Never mark business success here.
                logging.getLogger("hotel.g3").warning(
                    "G3 database temporarily unavailable: %s", type(exc).__name__
                )
                time.sleep(1)
                continue
            time.sleep(0.05)
    finally:
        db.pool.close()


if __name__ == "__main__":
    main()
