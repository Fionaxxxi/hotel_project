import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

from fastapi import HTTPException

from infra.manage import uid
from services.core.g3 import domain
from tests.g3_helpers import prepared


def test_concurrent_allocation_versions_commit_only_once(client, settings):
    bundle = prepared(client, settings)
    db = client.app.state.db
    barrier = threading.Barrier(2)

    def attempt(amounts):
        barrier.wait()
        try:
            with db.transaction(uid("tenant-A")) as c:
                result = domain.allocate(
                    c,
                    {"tenant_id": uid("tenant-A"), "subject": "a.operator", "role": "operator"},
                    UUID(bundle["gen"]["activation_id"]),
                    amounts,
                    1,
                    1,
                )
                assert result["version"] == 2
            return "COMMITTED"
        except HTTPException as exc:
            return exc.detail

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(attempt, [{"SIM-A": 2, "SIM-B": 1}, {"SIM-A": 1, "SIM-B": 2}]))
    assert sorted(results) == ["ALLOCATION_VERSION_CONFLICT", "COMMITTED"]
    with db.transaction(uid("tenant-A")) as c:
        rows = c.execute(
            "SELECT version FROM g3_allocation WHERE source_key=%s ORDER BY version",
            (bundle["allocation"]["source_key"],),
        ).fetchall()
        assert [r["version"] for r in rows] == [1, 2]
