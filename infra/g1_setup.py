"""Provision isolated simulator; append only missing project-local settings."""

import hashlib
import os
import secrets

import psycopg
from dotenv import load_dotenv
from psycopg import sql

from infra.manage import ROOT, migrate, uid
from services.core.db import Database


def setup():
    load_dotenv(ROOT / ".env")
    additions = {
        "SIM_MIGRATION_PASSWORD": secrets.token_urlsafe(24),
        "SIM_APP_PASSWORD": secrets.token_urlsafe(24),
        "SIM_A_TOKEN": secrets.token_urlsafe(32),
        "SIM_B_TOKEN": secrets.token_urlsafe(32),
        "DEMO_CLOCK_ENABLED": "true",
        "SIM_FAULTS_ENABLED": "false",
    }
    for key, value in additions.items():
        if key not in os.environ:
            with (ROOT / ".env").open("a", encoding="utf-8") as f:
                f.write(f"{key}={value}\n")
            os.environ[key] = value
    urls = {
        "SIM_DATABASE_URL": f"postgresql://hotel_sim_app:{os.environ['SIM_APP_PASSWORD']}@127.0.0.1:55432/hotel_project_sim",
        "SIM_MIGRATION_URL": f"postgresql://hotel_sim_migrator:{os.environ['SIM_MIGRATION_PASSWORD']}@127.0.0.1:55432/hotel_project_sim",
    }
    for key, value in urls.items():
        if key not in os.environ:
            with (ROOT / ".env").open("a", encoding="utf-8") as f:
                f.write(f"{key}={value}\n")
            os.environ[key] = value
    with psycopg.connect(os.environ["BOOTSTRAP_DATABASE_URL"], autocommit=True) as c:
        for role, password in [
            ("hotel_sim_migrator", os.environ["SIM_MIGRATION_PASSWORD"]),
            ("hotel_sim_app", os.environ["SIM_APP_PASSWORD"]),
        ]:
            if not c.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone():
                c.execute(
                    sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS").format(
                        sql.Identifier(role), sql.Literal(password)
                    )
                )
        if not c.execute("SELECT 1 FROM pg_database WHERE datname='hotel_project_sim'").fetchone():
            c.execute("CREATE DATABASE hotel_project_sim OWNER hotel_sim_migrator")
        c.execute("REVOKE CONNECT ON DATABASE hotel_project_sim FROM PUBLIC")
        c.execute("GRANT CONNECT ON DATABASE hotel_project_sim TO hotel_sim_app,hotel_sim_migrator")
        c.execute("REVOKE CONNECT ON DATABASE hotel_project FROM PUBLIC")
        c.execute("GRANT CONNECT ON DATABASE hotel_project TO hotel_app,hotel_migrator")
    with psycopg.connect(os.environ["SIM_MIGRATION_URL"]) as c:
        c.execute(
            "CREATE TABLE IF NOT EXISTS migration_history(version text PRIMARY KEY,sha256 text NOT NULL)"
        )
        path = ROOT / "infra/simulator_migrations/001_simulator.sql"
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        existing = c.execute("SELECT sha256 FROM migration_history WHERE version='001'").fetchone()
        if not existing:
            c.execute(path.read_text(encoding="utf-8"))
            c.execute("INSERT INTO migration_history VALUES('001',%s)", (checksum,))
        elif existing[0] != checksum:
            raise RuntimeError("Applied simulator migration changed")
    migrate()
    db = Database(os.environ["DATABASE_URL"])
    db.pool.open()
    try:
        for label in ["A", "B"]:
            with db.transaction(uid(f"tenant-{label}"), f"{label.lower()}.tenant_admin") as c:
                c.execute(
                    "INSERT INTO channel_account(id,tenant_id,code) VALUES(%s,%s,'SIM-A') ON CONFLICT DO NOTHING",
                    (uid(f"{label}/SIM-A"), uid(f"tenant-{label}")),
                )
                seed_channel_codes(c, label)
    finally:
        db.pool.close()
    from services.core.version import SCHEMA_VERSION

    print(
        f"Setup ready: core schema {SCHEMA_VERSION}, isolated simulator schema 001, two channel accounts"
    )


def seed_channel_codes(c, label):
    from psycopg import sql

    for table, field, prefix in [
        ("property", "property_id", "H"),
        ("room_type", "room_id", "R"),
        ("rate_plan", "plan_id", "P"),
    ]:
        for row in c.execute(sql.SQL("SELECT id FROM {}").format(sql.Identifier(table))).fetchall():
            target = row["id"]
            c.execute(
                sql.SQL(
                    "INSERT INTO channel_mapping(id,tenant_id,channel_id,{},external_code) VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING"
                ).format(sql.Identifier(field)),
                (
                    uid(f"{label}/SIM-A/{target}"),
                    uid(f"tenant-{label}"),
                    uid(f"{label}/SIM-A"),
                    target,
                    f"SIM-{prefix}-{target}",
                ),
            )


if __name__ == "__main__":
    setup()
