"""Create persistent, isolated test DBs; never resets or deletes a database."""

import os

import psycopg
from dotenv import load_dotenv
from psycopg import sql

from infra.manage import ROOT, migrate, seed


def prepare():
    load_dotenv(ROOT / ".env")
    profile = os.environ.get("HOTEL_TEST_PROFILE", "g1")
    core_name = {
        "g5_benchmark": "hotel_project_g5_benchmark",
        "g5": "hotel_project_g5_test",
        "g4": "hotel_project_g4_test",
        "g3": "hotel_project_g3_test",
        "g2": "hotel_project_g2_test",
    }.get(profile, "hotel_project_test")
    sim_name = {
        "g5_benchmark": "hotel_project_sim_a_g5_benchmark",
        "g5": "hotel_project_sim_a_g5_test",
        "g4": "hotel_project_sim_a_g4_test",
        "g3": "hotel_project_sim_a_g3_test",
        "g2": "hotel_project_sim_g2_test",
    }.get(profile, "hotel_project_sim_test")
    names = {
        core_name: ("hotel_migrator", "hotel_app"),
        sim_name: ("hotel_sim_migrator", "hotel_sim_app"),
    }
    if profile in {"g3", "g4", "g5"}:
        names["hotel_project_sim_b_" + profile + "_test"] = (
            "hotel_sim_b_migrator",
            "hotel_sim_b_app",
        )
    with psycopg.connect(os.environ["BOOTSTRAP_DATABASE_URL"], autocommit=True) as c:
        for name, (owner, app) in names.items():
            if not c.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,)).fetchone():
                c.execute(
                    sql.SQL("CREATE DATABASE {} OWNER {}").format(
                        sql.Identifier(name), sql.Identifier(owner)
                    )
                )
            c.execute(
                sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(sql.Identifier(name))
            )
            c.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {},{}").format(
                    sql.Identifier(name), sql.Identifier(owner), sql.Identifier(app)
                )
            )
    values = {
        "DATABASE_URL": os.environ["DATABASE_URL"].replace("/hotel_project", "/" + core_name),
        "MIGRATION_DATABASE_URL": os.environ["MIGRATION_DATABASE_URL"].replace(
            "/hotel_project", "/" + core_name
        ),
        "SIM_DATABASE_URL": os.environ["SIM_DATABASE_URL"].replace(
            "/hotel_project_sim", "/" + sim_name
        ),
        "SIM_MIGRATION_URL": os.environ["SIM_MIGRATION_URL"].replace(
            "/hotel_project_sim", "/" + sim_name
        ),
        "HOTEL_ARTIFACT_ROOT": str(ROOT / ".runtime/test-artifacts"),
    }
    if profile in {"g3", "g4", "g5"}:
        values["SIM_B_DATABASE_URL"] = os.environ["SIM_B_DATABASE_URL"].replace(
            "/hotel_project_sim_b", "/hotel_project_sim_b_" + profile + "_test"
        )
        values["SIM_B_MIGRATION_URL"] = os.environ["SIM_B_MIGRATION_URL"].replace(
            "/hotel_project_sim_b", "/hotel_project_sim_b_" + profile + "_test"
        )
    env_path = ROOT / (
        ".runtime/g5-benchmark.env" if profile == "g5_benchmark" else ".runtime/test.env"
    )
    env_path.write_text(
        "\n".join(f"{key}={value}" for key, value in values.items()) + "\n", encoding="utf-8"
    )
    os.environ.update(values)
    migrate()
    seed()
    # Simulator SQL migration is idempotent and separately recorded.
    import hashlib

    with psycopg.connect(values["SIM_MIGRATION_URL"]) as c:
        c.execute(
            "CREATE TABLE IF NOT EXISTS migration_history(version text PRIMARY KEY,sha256 text NOT NULL)"
        )
        path = ROOT / "infra/simulator_migrations/001_simulator.sql"
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        row = c.execute("SELECT sha256 FROM migration_history WHERE version='001'").fetchone()
        if not row:
            c.execute(path.read_text(encoding="utf-8"))
            c.execute("INSERT INTO migration_history VALUES('001',%s)", (checksum,))
        elif row[0] != checksum:
            raise RuntimeError("Applied simulator test migration changed")
    from infra.manage import uid
    from services.core.db import Database

    db = Database(values["DATABASE_URL"])
    db.pool.open()
    try:
        for label in ["A", "B"]:
            with db.transaction(uid(f"tenant-{label}")) as c:
                c.execute(
                    "INSERT INTO channel_account(id,tenant_id,code) VALUES(%s,%s,'SIM-A') ON CONFLICT DO NOTHING",
                    (uid(f"{label}/SIM-A"), uid(f"tenant-{label}")),
                )
                from infra.g1_setup import seed_channel_codes

                seed_channel_codes(c, label)
    finally:
        db.pool.close()
    if profile in {"g3", "g4", "g5"}:
        from infra.g3_setup import migrate_sim, seed_g3

        migrate_sim(
            values["SIM_MIGRATION_URL"],
            sorted((ROOT / "infra/simulator_migrations").glob("00[23]*.sql")),
        )
        migrate_sim(
            values["SIM_B_MIGRATION_URL"],
            list((ROOT / "infra/simulator_b_migrations").glob("*.sql")),
        )
        seed_g3(values["DATABASE_URL"])
    print("Isolated test DBs ready; no developer database reset")


if __name__ == "__main__":
    prepare()
