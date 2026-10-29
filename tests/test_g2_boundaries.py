import zipfile
from uuid import uuid4

import psycopg
import pytest

from infra.manage import uid
from services.core.g2 import formats, jobs, processing, workflow
from tests.g1_helpers import approve_mappings
from tests.g2_helpers import activate, ready, start_run


@pytest.mark.parametrize(
    "name,content",
    [
        ("xl/vbaProject.bin", b"macro"),
        ("xl/externalLinks/link1.xml", b"link"),
        ("../outside.xml", b"escape"),
        ("xl/large.xml", b"0" * 1000000),
    ],
    ids=["macro", "external-link", "path-traversal", "expansion-ratio"],
)
def test_xlsx_archive_guards(tmp_path, name, content):
    path = tmp_path / "data.xlsx"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(name, content)
    with pytest.raises(ValueError, match="XLSX_UNSAFE_CONTENT"):
        formats.inspect_xlsx(path)


def test_duplicate_json_key_is_package_error(tmp_path):
    path = tmp_path / "data.json"
    path.write_text('{"rooms":[{"room_code":"001","room_code":"002"}]}')
    with pytest.raises(ValueError, match="DUPLICATE_JSON_KEY"):
        formats.inspect_structure(path, ["rooms"])


def test_changed_original_is_not_parsed(client, settings):
    _, run = start_run(client, settings)
    db = client.app.state.db
    tenant = uid("tenant-A")
    with db.transaction(tenant) as c:
        artifact = c.execute(
            "SELECT a.* FROM artifact a JOIN processing_run p ON p.batch_id=a.batch_id WHERE p.id=%s AND a.name='rates.csv'",
            (run["id"],),
        ).fetchone()
    path = processing.STORE / artifact["storage_key"]
    original = path.read_bytes()
    try:
        path.write_bytes(original + b"corrupt")
        from tests.g2_helpers import parse

        result = parse(client, settings, run)
        assert result["status"] == "QUALITY_REVIEW"
        with db.transaction(tenant) as c:
            assert c.execute(
                "SELECT 1 FROM dq_issue WHERE run_id=%s AND code='HASH_MISMATCH' AND partition_key='*'",
                (run["id"],),
            ).fetchone()
    finally:
        path.write_bytes(original)


def test_stale_mapping_event_is_recorded_without_wake(client, settings):
    run = ready(client, settings)
    generation = activate(client, settings, run)
    approve_mappings(client, settings, generation["activation_id"])
    with client.app.state.db.transaction(uid("tenant-A")) as c:
        event = c.execute(
            "SELECT e.* FROM outbox_event e JOIN supplier_entity_version s ON e.aggregate_id=s.id WHERE s.activation_id=%s AND e.event_type='mapping.decided' LIMIT 1",
            (generation["activation_id"],),
        ).fetchone()
        stale = {**event, "id": uuid4(), "aggregate_version": 99999}
        # Add a real immutable event so inbox FK remains valid.
        stale["id"] = jobs.emit(
            c, uid("tenant-A"), "mapping.decided", event["aggregate_id"], 99999, {}
        )
        assert workflow.consume(c, stale) == "STALE"
        assert (
            c.execute(
                "SELECT result FROM consumer_inbox WHERE event_id=%s", (stale["id"],)
            ).fetchone()["result"]
            == "STALE"
        )
        assert not c.execute(
            "SELECT 1 FROM execution_job WHERE logical_key LIKE %s", ("%:" + str(stale["id"]),)
        ).fetchone()


def test_new_tables_cross_tenant_write_rejected(client, settings):
    run = ready(client, settings)
    tenant = uid("tenant-A")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with client.app.state.db.transaction(uid("tenant-B")) as c:
            jobs.emit(c, tenant, "test.cross.tenant", uuid4(), 1, {})
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with client.app.state.db.transaction(uid("tenant-B")) as c:
            c.execute(
                "INSERT INTO workflow_run(id,tenant_id,processing_id,definition,state,created_by) VALUES(%s,%s,%s,'g2-flow-v1','PROCESSING','b.operator')",
                (uuid4(), uid("tenant-B"), run["id"]),
            )
