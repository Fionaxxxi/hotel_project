import csv
import io
import zipfile
from uuid import UUID, uuid4

import pytest

from infra.manage import uid
from services.core.g1.common import j
from services.core.g2.formats import inspect_structure, inspect_xlsx
from tests.g1_helpers import auth
from tests.g2_helpers import ready


def test_quality_csv_actual_export_escapes_leading_control_formula(client, settings):
    run = ready(client, settings)
    with client.app.state.db.transaction(uid("tenant-A")) as c:
        before = c.execute(
            "SELECT id,sha256 FROM artifact WHERE batch_id=%s ORDER BY id", (UUID(run["batch_id"]),)
        ).fetchall()
        c.execute(
            "INSERT INTO dq_issue(id,tenant_id,run_id,code,partition_key,severity,location,detail) VALUES(%s,%s,%s,%s,%s,'BLOCKING',%s,'synthetic formula export guard')",
            (
                uuid4(),
                uid("tenant-A"),
                UUID(run["id"]),
                "\x01 =1+1",
                "\ufeff\t@SUM(A1)",
                j({"record": 2}),
            ),
        )
    response = client.get(
        "/v2/processing-runs/" + run["id"] + "/issues.csv", headers=auth(settings, "auditor")
    )
    assert response.status_code == 200
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert any(
        r["code"].startswith("'\x01 =") and r["partition"].startswith("'\ufeff\t@") for r in rows
    )
    with client.app.state.db.transaction(uid("tenant-A")) as c:
        assert (
            c.execute(
                "SELECT id,sha256 FROM artifact WHERE batch_id=%s ORDER BY id",
                (UUID(run["batch_id"]),),
            ).fetchall()
            == before
        )
    assert (
        client.get(
            "/v2/processing-runs/" + run["id"] + "/issues.csv",
            headers=auth(settings, "auditor", "B"),
        ).status_code
        == 404
    )


def test_deep_json_rejected_before_record_materialization(tmp_path):
    path = tmp_path / "data.json"
    path.write_text('{"rooms":[' + "[" * 70 + "0" + "]" * 70 + "]}", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON_NESTING_LIMIT"):
        inspect_structure(path, ["rooms"])


@pytest.mark.parametrize("name", ["..\\evil.xml", "C:/evil.xml", "/evil.xml"])
def test_windows_xlsx_traversal_rejected(tmp_path, name):
    path = tmp_path / "data.xlsx"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(name, "<xml/>")
    with pytest.raises(ValueError, match="XLSX_UNSAFE_CONTENT"):
        inspect_xlsx(path)
