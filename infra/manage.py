"""Explicit development DB initialization, atomic migrations and deterministic seeds."""

import argparse
import hashlib
import json
import os
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import psycopg
from dotenv import load_dotenv
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def uid(name):
    return uuid5(NAMESPACE_URL, f"hotel-project/{name}")


def migrate():
    with psycopg.connect(os.environ["MIGRATION_DATABASE_URL"]) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(7140026)")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS migration_history(version text PRIMARY KEY, sha256 text NOT NULL)"
        )
        for path in sorted((ROOT / "infra/migrations").glob("*.sql")):
            checksum = hashlib.sha256(path.read_bytes()).hexdigest()
            row = conn.execute(
                "SELECT sha256 FROM migration_history WHERE version=%s", (path.stem,)
            ).fetchone()
            if row:
                if row[0] != checksum:
                    raise RuntimeError(f"Applied migration modified: {path.name}")
                continue
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO migration_history VALUES(%s,%s)", (path.stem, checksum))
    from services.core.version import SCHEMA_VERSION

    print(f"Migrations current: {SCHEMA_VERSION}")


def provision():
    """Run once against the isolated development instance, never an external/shared DB."""
    if os.environ.get("APP_ENV") != "development":
        raise RuntimeError("Provisioning is development-only")
    with psycopg.connect(os.environ["BOOTSTRAP_DATABASE_URL"], autocommit=True) as conn:
        for role, password_key in [
            ("hotel_migrator", "MIGRATION_PASSWORD"),
            ("hotel_app", "APP_DB_PASSWORD"),
        ]:
            if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone():
                conn.execute(
                    sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS").format(
                        sql.Identifier(role), sql.Literal(os.environ[password_key])
                    )
                )
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname='hotel_project'").fetchone():
            conn.execute("CREATE DATABASE hotel_project OWNER hotel_migrator")
    print("Development roles and database provisioned")


def seed():
    with psycopg.connect(os.environ["MIGRATION_DATABASE_URL"]) as conn:
        # FORCE RLS also applies to owners; migrations seed under an explicit temporary policy.
        tables = [
            "tenant",
            "app_user",
            "membership",
            "supplier",
            "property",
            "property_version",
            "room_type",
            "room_version",
            "rate_plan",
            "policy_version",
            "audit_event",
        ]
        for table in tables:
            conn.execute(
                sql.SQL(
                    "CREATE POLICY seed_owner ON {} TO hotel_migrator USING (true) WITH CHECK (true)"
                ).format(sql.Identifier(table))
            )
        for label in ["A", "B"]:
            tenant = uid(f"tenant-{label}")
            conn.execute(
                "INSERT INTO tenant VALUES(%s,%s,'active') ON CONFLICT DO NOTHING",
                (tenant, f"Tenant {label}"),
            )
            for role in ["tenant_admin", "operator", "reviewer", "auditor"]:
                subject = f"{label.lower()}.{role}"
                user = uid(subject)
                conn.execute(
                    "INSERT INTO app_user VALUES(%s,%s) ON CONFLICT DO NOTHING", (user, subject)
                )
                conn.execute(
                    "INSERT INTO membership VALUES(%s,%s,%s,'active') ON CONFLICT DO NOTHING",
                    (tenant, user, role),
                )
            for code in ["S-A", "S-B"] if label == "A" else ["S-A"]:
                conn.execute(
                    "INSERT INTO supplier(id,tenant_id,code,name,contract_version) VALUES(%s,%s,%s,%s,'1.0') ON CONFLICT DO NOTHING",
                    (uid(f"{label}/{code}"), tenant, code, f"Fictional supplier {label}/{code}"),
                )
            prop = uid(f"property-{label}")
            pv = uid(f"property-{label}/v1")
            conn.execute(
                "INSERT INTO property VALUES(%s,%s,NULL) ON CONFLICT DO NOTHING", (prop, tenant)
            )
            conn.execute(
                "INSERT INTO property_version(id,tenant_id,property_id,version,name,country_code,city,address,timezone) VALUES(%s,%s,%s,1,'海岚滨海酒店','CN','厦门','虚构海湾路1号','Asia/Shanghai') ON CONFLICT DO NOTHING",
                (pv, tenant, prop),
            )
            conn.execute(
                "UPDATE property SET current_version_id=%s WHERE id=%s AND current_version_id IS NULL",
                (pv, prop),
            )
            for bed in ["KING", "TWIN"]:
                room, rv = uid(f"{label}/{bed}"), uid(f"{label}/{bed}/v1")
                conn.execute(
                    "INSERT INTO room_type VALUES(%s,%s,%s,NULL) ON CONFLICT DO NOTHING",
                    (room, tenant, prop),
                )
                conn.execute(
                    "INSERT INTO room_version VALUES(%s,%s,%s,1,%s,%s,2) ON CONFLICT DO NOTHING",
                    (rv, tenant, room, f"Deluxe {bed}", bed),
                )
                conn.execute(
                    "UPDATE room_type SET current_version_id=%s WHERE id=%s AND current_version_id IS NULL",
                    (rv, room),
                )
                for kind in ["FLEX", "NONREF"]:
                    plan, pv_id = uid(f"{label}/{bed}/{kind}"), uid(f"{label}/{bed}/{kind}/v1")
                    policy = {
                        "kind": kind,
                        "deadline_hours": 72 if kind == "FLEX" else 0,
                        "penalty_basis": "stay_total",
                    }
                    conn.execute(
                        "INSERT INTO rate_plan VALUES(%s,%s,%s,NULL) ON CONFLICT DO NOTHING",
                        (plan, tenant, room),
                    )
                    conn.execute(
                        "INSERT INTO policy_version VALUES(%s,%s,%s,1,'1.0','BREAKFAST_2',%s,'AT_PROPERTY','NET_EXCLUSIVE',%s) ON CONFLICT DO NOTHING",
                        (pv_id, tenant, plan, json.dumps(policy), f"Fictional {kind} policy"),
                    )
                    conn.execute(
                        "UPDATE rate_plan SET current_version_id=%s WHERE id=%s AND current_version_id IS NULL",
                        (pv_id, plan),
                    )
            conn.execute(
                "INSERT INTO audit_event(id,tenant_id,actor,action,trace_id,reason) VALUES(%s,%s,'seed','seed.initialized',%s,'G0 synthetic dataset v1') ON CONFLICT DO NOTHING",
                (uid(f"audit-{label}"), tenant, uid(f"trace-{label}")),
            )
        for table in tables:
            conn.execute(sql.SQL("DROP POLICY seed_owner ON {}").format(sql.Identifier(table)))
    print("Synthetic seeds current: tenants A/B")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["provision", "migrate", "seed"])
    args = parser.parse_args()
    {"provision": provision, "migrate": migrate, "seed": seed}[args.command]()
