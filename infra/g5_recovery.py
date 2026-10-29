"""Local-only backup/restore. Caller stops project services before backup.

Locks all source tables against writes across all three databases while dumping.
Restores only into new prefixed databases; never drops/replaces a database.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlparse
from uuid import uuid4

import psycopg
from dotenv import load_dotenv
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / ".runtime/g5-backups"
BIN = Path(sys.prefix) / "Library/bin"
SOURCE = {"core": "hotel_project", "sim-a": "hotel_project_sim", "sim-b": "hotel_project_sim_b"}


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def connection(name):
    from psycopg.conninfo import make_conninfo

    parsed = urlparse(os.environ["BOOTSTRAP_DATABASE_URL"])
    if parsed.hostname != "127.0.0.1" or parsed.port != 55432:
        raise ValueError("RECOVERY_REQUIRES_PROJECT_LOOPBACK_CLUSTER")
    return make_conninfo(
        host=parsed.hostname,
        port=parsed.port,
        user=unquote(parsed.username),
        password=unquote(parsed.password),
        dbname=name,
    )


def pg(tool, name, *args):
    parsed = urlparse(os.environ["BOOTSTRAP_DATABASE_URL"])
    connection(name)  # validate before executing
    env = os.environ.copy()
    env.update(
        PGHOST="127.0.0.1",
        PGPORT="55432",
        PGUSER=unquote(parsed.username),
        PGPASSWORD=unquote(parsed.password),
        PGDATABASE=name,
    )
    target_args = ["--dbname", name] if tool == "pg_restore" and "--list" not in args else []
    result = subprocess.run(
        [str(BIN / (tool + ".exe")), *target_args, *map(str, args)],
        env=env,
        capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if result.returncode:
        # Raw postgres stderr may contain deployment details. Keep it out of public reports.
        raise RuntimeError("POSTGRES_TOOL_FAILED:" + tool)


def bounded(base, path):
    base = base.resolve()
    path = path.resolve()
    if not path.is_relative_to(base) or path == base:
        raise ValueError("PATH_OUTSIDE_RECOVERY_ROOT")
    return path


def inventory(c):
    tables = c.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
    ).fetchall()
    result = {}
    for (table,) in tables:
        # Ordered JSON row hashes permit cross-database content comparison, without exporting values.
        cursor = c.cursor(name="g5_" + uuid4().hex)
        cursor.execute(
            sql.SQL("SELECT md5(to_jsonb(t)::text) AS hash FROM {} t ORDER BY hash").format(
                sql.Identifier(table)
            )
        )
        h = hashlib.sha256()
        count = 0
        for (value,) in cursor:
            h.update(value.encode())
            count += 1
        cursor.close()
        result[table] = {"rows": count, "content_sha256": h.hexdigest()}
    return result


def backup():
    BASE.mkdir(parents=True, exist_ok=True)
    for name in [
        "api",
        "sim",
        "sim-b",
        "g3-worker-0",
        "g3-worker-1",
        "g2-worker-0",
        "g2-worker-1",
        "g4-flow",
        "g4-ai",
        "g5-ops",
    ]:
        if (ROOT / f".runtime/{name}.pid").exists():
            raise ValueError("STOP_VERIFIED_PROJECT_SERVICES_BEFORE_BACKUP")
    target = BASE / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8])
    target.mkdir()
    started = time.monotonic()
    manifest = {
        "schema": "g5-backup-v1",
        "status": "INCOMPLETE",
        "started_at": datetime.now(UTC).isoformat(),
        "databases": {},
        "files": {},
        "secrets_included": False,
        "scope": "three local development databases and protected artifacts; same-disk copy is not disaster protection",
    }
    with ExitStack() as stack:
        conns = {}
        for label, name in SOURCE.items():
            c = stack.enter_context(psycopg.connect(connection(name)))
            c.execute("SET LOCAL lock_timeout='5s'")
            tables = c.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
            ).fetchall()
            for (table,) in tables:
                c.execute(sql.SQL("LOCK TABLE {} IN SHARE MODE").format(sql.Identifier(table)))
            conns[label] = c
        for label, name in SOURCE.items():
            pg(
                "pg_dump",
                name,
                "--format=custom",
                "--no-owner",
                "--file",
                target / (label + ".dump"),
            )
            manifest["databases"][label] = {"source": name, "inventory": inventory(conns[label])}
        manifest["core_schema"] = (
            conns["core"].execute("SELECT version FROM schema_revision").fetchone()[0]
        )
        manifest["migration_checksums"] = [
            {"version": r[0], "sha256": r[1]}
            for r in conns["core"]
            .execute("SELECT version,sha256 FROM migration_history ORDER BY version")
            .fetchall()
        ]
        from infra.g5_release import source_files

        manifest["source_code"] = {
            name: sha(path)
            for path, name in source_files()
            if path.suffix in {".py", ".sql", ".ps1", ".tsx", ".css", ".toml"}
            or name in {"requirements.lock.txt", "apps/admin/package-lock.json"}
        }
        artifacts = ROOT / ".runtime/artifacts"
        if artifacts.exists():
            for path in artifacts.rglob("*"):
                if path.is_symlink():
                    raise ValueError("ARTIFACT_SYMLINK_FORBIDDEN")
            shutil.copytree(artifacts, target / "artifacts")
        model = ROOT / ".runtime/models/multilingual-minilm/manifest.json"
        if model.exists():
            shutil.copyfile(model, target / "model-manifest.json")
        for path in sorted(target.rglob("*")):
            if path.is_file():
                manifest["files"][path.relative_to(target).as_posix()] = {
                    "sha256": sha(path),
                    "bytes": path.stat().st_size,
                }
        manifest.update(
            status="COMPLETE",
            completed_at=datetime.now(UTC).isoformat(),
            duration_seconds=time.monotonic() - started,
        )
        (target / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
    print(
        json.dumps(
            {"backup": str(target), "status": "COMPLETE", "seconds": manifest["duration_seconds"]}
        )
    )
    return target


def verify(path):
    target = bounded(BASE, Path(path))
    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("status") != "COMPLETE" or manifest.get("schema") != "g5-backup-v1":
        raise ValueError("INCOMPLETE_BACKUP")
    if set(manifest["databases"]) != set(SOURCE):
        raise ValueError("DATABASE_SET_MISMATCH")
    for name, entry in manifest["files"].items():
        f = bounded(target, target / name)
        if (
            f.is_symlink()
            or not f.is_file()
            or f.stat().st_size != entry["bytes"]
            or sha(f) != entry["sha256"]
        ):
            raise ValueError("BACKUP_FILE_INTEGRITY_FAILED")
    for label in SOURCE:
        if label + ".dump" not in manifest["files"]:
            raise ValueError("DATABASE_DUMP_MISSING")
        pg("pg_restore", "postgres", "--list", target / (label + ".dump"))
    return manifest


def restore(path, run_id):
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,20}", run_id):
        raise ValueError("INVALID_RESTORE_ID")
    target = bounded(BASE, Path(path))
    manifest = verify(target)
    started = time.monotonic()
    names = {
        label: "hotel_project_g5_restore_" + run_id + "_" + label.replace("-", "_")
        for label in SOURCE
    }
    with psycopg.connect(connection("postgres"), autocommit=True) as c:
        if any(
            c.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,)).fetchone()
            for name in names.values()
        ):
            raise ValueError("RESTORE_TARGET_ALREADY_EXISTS")
        for label, name in names.items():
            owner = {
                "core": "hotel_migrator",
                "sim-a": "hotel_sim_migrator",
                "sim-b": "hotel_sim_b_migrator",
            }[label]
            c.execute(
                sql.SQL("CREATE DATABASE {} OWNER {}").format(
                    sql.Identifier(name), sql.Identifier(owner)
                )
            )
            c.execute(
                sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(sql.Identifier(name))
            )
            app = {"core": "hotel_app", "sim-a": "hotel_sim_app", "sim-b": "hotel_sim_b_app"}[label]
            c.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(name), sql.Identifier(app)
                )
            )
    results = {}
    for label, name in names.items():
        pg("pg_restore", name, "--exit-on-error", "--no-owner", target / (label + ".dump"))
        with psycopg.connect(connection(name)) as c:
            actual = inventory(c)
            owner = {
                "core": "hotel_migrator",
                "sim-a": "hotel_sim_migrator",
                "sim-b": "hotel_sim_b_migrator",
            }[label]
            # Change only objects IN this new database. REASSIGN OWNED would also
            # affect shared database/tablespace ownership and is deliberately avoided.
            for (table,) in c.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname='public'"
            ).fetchall():
                c.execute(
                    sql.SQL("ALTER TABLE {} OWNER TO {}").format(
                        sql.Identifier(table), sql.Identifier(owner)
                    )
                )
            for function, args in c.execute(
                "SELECT p.proname,pg_get_function_identity_arguments(p.oid) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public'"
            ).fetchall():
                c.execute(
                    sql.SQL("ALTER FUNCTION {}({}) OWNER TO {}").format(
                        sql.Identifier(function), sql.SQL(args), sql.Identifier(owner)
                    )
                )
        results[label] = {
            "database": name,
            "content_matches": actual == manifest["databases"][label]["inventory"],
            "tables": len(actual),
        }
        if not results[label]["content_matches"]:
            raise ValueError("RESTORED_CONTENT_MISMATCH")
    files = ROOT / ".runtime/g5-restored-artifacts" / run_id
    if files.exists():
        raise ValueError("ARTIFACT_TARGET_ALREADY_EXISTS")
    if (target / "artifacts").exists():
        files.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(target / "artifacts", files)
    report = {
        "schema": "g5-restore-v1",
        "backup_id": target.name,
        "run_id": run_id,
        "restored_at": datetime.now(UTC).isoformat(),
        "duration_seconds": time.monotonic() - started,
        "databases": results,
        "artifact_root": str(files),
        "dispatch": "BLOCKED_BY_DEFAULT",
        "remote_state": "UNMODIFIED; reconciliation required before any release",
        "rto_note": "restore and hash-check only; full RTO includes reconciliation and explicit safe release",
    }
    out = ROOT / "docs/acceptance" / ("g5-restore-" + run_id + ".json")
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(out), "status": "RESTORED_DISPATCH_BLOCKED"}))
    return report


def main():
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["backup", "verify", "restore"])
    parser.add_argument("--path")
    parser.add_argument("--run-id")
    args = parser.parse_args()
    if args.action == "backup":
        backup()
    elif args.action == "verify":
        verify(args.path)
        print("Backup integrity and PostgreSQL dump format verified")
    else:
        restore(args.path, args.run_id)


if __name__ == "__main__":
    main()
