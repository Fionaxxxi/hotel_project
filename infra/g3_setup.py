"""Provision only project-local independent simulators and append missing secrets."""

import hashlib
import os
import secrets

import psycopg
from dotenv import load_dotenv
from psycopg import sql

from infra.manage import ROOT, uid
from services.core.db import Database
from services.core.g3.contract import PROFILES


def migrate_sim(url, paths):
    with psycopg.connect(url) as c:
        c.execute(
            "CREATE TABLE IF NOT EXISTS migration_history(version text PRIMARY KEY,sha256 text NOT NULL)"
        )
        for path in paths:
            version = path.stem
            hash_ = hashlib.sha256(path.read_bytes()).hexdigest()
            old = c.execute(
                "SELECT sha256 FROM migration_history WHERE version=%s", (version,)
            ).fetchone()
            if old:
                if old[0] != hash_:
                    raise RuntimeError("Applied simulator migration changed: " + version)
            else:
                c.execute(path.read_text(encoding="utf-8"))
                c.execute("INSERT INTO migration_history VALUES(%s,%s)", (version, hash_))


def seed_g3(url):
    db = Database(url)
    db.pool.open()
    try:
        for label in ["A", "B"]:
            tenant = uid("tenant-" + label)
            with db.transaction(tenant) as c:
                for code, profile in PROFILES.items():
                    channel = uid(label + "/" + code)
                    c.execute(
                        "INSERT INTO channel_account(id,tenant_id,code,capability_version) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                        (
                            channel,
                            tenant,
                            code,
                            "sim-a-v1" if code == "SIM-A" else profile["capability"],
                        ),
                    )
                    c.execute(
                        "INSERT INTO g3_account(id,tenant_id,channel_id,code,capability) VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                        (uid(label + "/G3/" + code), tenant, channel, code, profile["capability"]),
                    )
                    if code == "SIM-B":
                        for r in c.execute(
                            "SELECT * FROM channel_mapping WHERE channel_id=%s",
                            (uid(label + "/SIM-A"),),
                        ).fetchall():
                            c.execute(
                                "INSERT INTO channel_mapping(id,tenant_id,channel_id,property_id,room_id,plan_id,external_code) VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                                (
                                    uid(label + "/SIM-B/" + str(r["id"])),
                                    tenant,
                                    channel,
                                    r["property_id"],
                                    r["room_id"],
                                    r["plan_id"],
                                    "B-" + r["external_code"],
                                ),
                            )
    finally:
        db.pool.close()


def setup():
    load_dotenv(ROOT / ".env")
    additions = {
        "G3_CONTROL_TOKEN": secrets.token_urlsafe(32),
        "G3_FAULTS_ENABLED": "false",
        "SIM_B_MIGRATION_PASSWORD": secrets.token_urlsafe(24),
        "SIM_B_APP_PASSWORD": secrets.token_urlsafe(24),
    }
    for ch in ["SIM_A", "SIM_B"]:
        for label in ["A", "B"]:
            additions[f"G3_{ch}_TENANT_{label}_TOKEN"] = secrets.token_urlsafe(32)
    for k, v in additions.items():
        if k not in os.environ:
            with (ROOT / ".env").open("a", encoding="utf-8") as f:
                f.write(k + "=" + v + "\n")
            os.environ[k] = v
    urls = {
        "SIM_B_DATABASE_URL": f"postgresql://hotel_sim_b_app:{os.environ['SIM_B_APP_PASSWORD']}@127.0.0.1:55432/hotel_project_sim_b",
        "SIM_B_MIGRATION_URL": f"postgresql://hotel_sim_b_migrator:{os.environ['SIM_B_MIGRATION_PASSWORD']}@127.0.0.1:55432/hotel_project_sim_b",
    }
    for k, v in urls.items():
        if k not in os.environ:
            with (ROOT / ".env").open("a", encoding="utf-8") as f:
                f.write(k + "=" + v + "\n")
            os.environ[k] = v
    with psycopg.connect(os.environ["BOOTSTRAP_DATABASE_URL"], autocommit=True) as c:
        for role, secret in [
            ("hotel_sim_b_migrator", "SIM_B_MIGRATION_PASSWORD"),
            ("hotel_sim_b_app", "SIM_B_APP_PASSWORD"),
        ]:
            if not c.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone():
                c.execute(
                    sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS").format(
                        sql.Identifier(role), sql.Literal(os.environ[secret])
                    )
                )
        if not c.execute(
            "SELECT 1 FROM pg_database WHERE datname='hotel_project_sim_b'"
        ).fetchone():
            c.execute("CREATE DATABASE hotel_project_sim_b OWNER hotel_sim_b_migrator")
        c.execute("REVOKE CONNECT ON DATABASE hotel_project_sim_b FROM PUBLIC")
        c.execute(
            "GRANT CONNECT ON DATABASE hotel_project_sim_b TO hotel_sim_b_app,hotel_sim_b_migrator"
        )
    from infra.manage import migrate

    migrate()
    migrate_sim(
        os.environ["SIM_MIGRATION_URL"],
        sorted((ROOT / "infra/simulator_migrations").glob("00[23]*.sql")),
    )
    migrate_sim(
        os.environ["SIM_B_MIGRATION_URL"],
        list((ROOT / "infra/simulator_b_migrations").glob("*.sql")),
    )
    seed_g3(os.environ["DATABASE_URL"])
    print(
        "G3 configured: isolated SIM-A / SIM-B roles, credentials and schemas; no developer data reset"
    )


if __name__ == "__main__":
    setup()
