"""Read business facts and persist only operations alerts; failures do not stop sales."""

import logging
import os
import time
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from infra.manage import uid
from services.core.config import Settings
from services.core.db import Database
from services.core.g5.operations import collect

_last_heartbeat = {}


def periodic_heartbeat(db, worker, lane):
    stamp = time.monotonic()
    if stamp - _last_heartbeat.get((worker, lane), 0) < 5:
        return
    _last_heartbeat[(worker, lane)] = stamp
    for label in ["A", "B"]:
        try:
            heartbeat(db, uid("tenant-" + label), worker, lane)
        except Exception as exc:
            logging.getLogger("hotel.operations").error(
                "heartbeat.failed error_type=%s", type(exc).__name__
            )


def heartbeat(db, tenant, worker, lane):
    slot = os.environ.get("HOTEL_WORKER_SLOT", "")
    key = uuid5(NAMESPACE_URL, "hotel-project/worker-slot/" + slot) if slot else UUID(str(worker))
    with db.transaction(tenant) as c:
        c.execute(
            "INSERT INTO g5_worker_heartbeat(tenant_id,worker_id,lane,slot) VALUES(%s,%s,%s,%s) ON CONFLICT(tenant_id,worker_id) DO UPDATE SET last_seen=clock_timestamp()",
            (tenant, key, lane, slot),
        )


def main():
    from services.core.g5.logging import configure

    configure("operations")
    db = Database(Settings.load().database_url)
    db.pool.open()
    owner = uuid4()
    try:
        while True:
            for label in ["A", "B"]:
                tenant = uid("tenant-" + label)
                try:
                    heartbeat(db, tenant, owner, "operations")
                    with db.transaction(tenant) as c:
                        collect(
                            c,
                            {
                                "tenant_id": tenant,
                                "subject": "system.operations",
                                "role": "operator",
                            },
                        )
                except Exception as exc:
                    logging.getLogger("hotel.operations").error(
                        "collector.failed error_type=%s", type(exc).__name__
                    )
            time.sleep(5)
    finally:
        db.pool.close()


if __name__ == "__main__":
    main()
