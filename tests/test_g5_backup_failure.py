import errno
import json

import pytest

from infra import g5_recovery


def test_dump_disk_full_never_creates_complete_backup(tmp_path, monkeypatch):
    base = tmp_path / "backups"
    monkeypatch.setattr(g5_recovery, "ROOT", tmp_path)
    monkeypatch.setattr(g5_recovery, "BASE", base)
    monkeypatch.setattr(g5_recovery, "connection", lambda name: "controlled-test")

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, statement, *args):
            return self

        def fetchall(self):
            return [("controlled_table",)]

    monkeypatch.setattr(g5_recovery.psycopg, "connect", lambda *a, **kw: Connection())

    def disk_full(*args):
        raise OSError(errno.ENOSPC, "Controlled backup output full")

    monkeypatch.setattr(g5_recovery, "pg", disk_full)
    with pytest.raises(OSError) as exc:
        g5_recovery.backup()
    assert exc.value.errno == errno.ENOSPC
    folders = list(base.iterdir())
    assert len(folders) == 1
    manifest = folders[0] / "manifest.json"
    assert not manifest.exists() or json.loads(manifest.read_text())["status"] != "COMPLETE"
    with pytest.raises((FileNotFoundError, ValueError)):
        g5_recovery.verify(folders[0])


def test_backup_refuses_managed_process_marker_before_dump(tmp_path, monkeypatch):
    (tmp_path / ".runtime").mkdir()
    (tmp_path / ".runtime/api.pid").write_text("1234")
    monkeypatch.setattr(g5_recovery, "ROOT", tmp_path)
    monkeypatch.setattr(g5_recovery, "BASE", tmp_path / "backups")
    with pytest.raises(ValueError, match="STOP_VERIFIED_PROJECT_SERVICES"):
        g5_recovery.backup()
    assert not list((tmp_path / "backups").iterdir())
