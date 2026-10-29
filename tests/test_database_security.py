import sys
from uuid import uuid4

import psycopg
import pytest
from psycopg import errors

from infra.manage import migrate, seed, uid
from services.core.db import Database


def test_environment():
    import site

    assert "hotel_project" in sys.prefix
    assert sys.version_info[:2] == (3, 12)
    assert site.ENABLE_USER_SITE is False


def test_migrate_and_seed_repeat(settings):
    migrate()
    seed()
    seed()
    db = Database(settings.database_url)
    db.pool.open()
    try:
        for label, expected in [("A", 2), ("B", 1)]:
            with db.transaction(uid(f"tenant-{label}")) as conn:
                assert (
                    conn.execute("SELECT count(*) AS n FROM supplier").fetchone()["n"] == expected
                )
    finally:
        db.pool.close()


def test_application_role_and_policies(settings):
    with psycopg.connect(settings.database_url) as conn:
        role = conn.execute(
            "SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"
        ).fetchone()
        assert role == (False, False)
        tables = conn.execute(
            "SELECT relname,relrowsecurity,relforcerowsecurity,pg_get_userbyid(relowner) FROM pg_class WHERE relnamespace='public'::regnamespace AND relname IN ('supplier','property','room_type','membership')"
        ).fetchall()
        assert len(tables) == 4
        assert all(r[1] and r[2] and r[3] != "hotel_app" for r in tables)
        assert conn.execute("SELECT count(*) FROM supplier").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM membership").fetchone()[0] == 0


def test_direct_rls_reads_writes_rollback(settings):
    db = Database(settings.database_url)
    db.pool.open()
    a, b = uid("tenant-A"), uid("tenant-B")
    try:
        with db.transaction(a, "a.operator") as conn:
            assert len(conn.execute("SELECT * FROM supplier").fetchall()) == 2
            assert (
                conn.execute(
                    "UPDATE supplier SET name='forbidden' WHERE tenant_id=%s", (b,)
                ).rowcount
                == 0
            )
            assert conn.execute("DELETE FROM supplier WHERE tenant_id=%s", (b,)).rowcount == 0
        with pytest.raises(errors.InsufficientPrivilege):
            with db.transaction(a, "a.operator") as conn:
                conn.execute(
                    "INSERT INTO supplier(id,tenant_id,code,name,contract_version) VALUES(%s,%s,'X','bad','1')",
                    (uuid4(), b),
                )
        # Reuse pooled connections after an exception and after a successful transaction.
        with db.transaction(b, "b.operator") as conn:
            assert len(conn.execute("SELECT * FROM supplier").fetchall()) == 1
        with db.transaction() as conn:
            assert conn.execute("SELECT count(*) AS n FROM supplier").fetchone()["n"] == 0
            assert conn.execute("SELECT count(*) AS n FROM membership").fetchone()["n"] == 0
    finally:
        db.pool.close()


def test_cross_tenant_fk_and_version_ownership(settings):
    with psycopg.connect(settings.database_url) as conn:
        conn.execute("SELECT set_config('app.tenant_id',%s,true)", (str(uid("tenant-A")),))
        with pytest.raises(errors.ForeignKeyViolation):
            with conn.transaction():
                conn.execute(
                    "INSERT INTO room_type(id,tenant_id,property_id) VALUES(%s,%s,%s)",
                    (uuid4(), uid("tenant-A"), uid("property-B")),
                )
        with pytest.raises(errors.ForeignKeyViolation):
            with conn.transaction():
                conn.execute(
                    "UPDATE property SET current_version_id=%s WHERE id=%s",
                    (uid("property-B/v1"), uid("property-A")),
                )
                conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
        with pytest.raises(errors.ForeignKeyViolation):
            with conn.transaction():
                conn.execute(
                    "UPDATE room_type SET current_version_id=%s WHERE id=%s",
                    (uid("A/TWIN/v1"), uid("A/KING")),
                )
                conn.execute("SET CONSTRAINTS ALL IMMEDIATE")


@pytest.mark.parametrize(
    "table", ["property_version", "room_version", "policy_version", "audit_event"]
)
def test_history_and_audit_are_append_only(settings, table):
    from psycopg import sql

    with psycopg.connect(settings.database_url) as conn:
        conn.execute("SELECT set_config('app.tenant_id',%s,true)", (str(uid("tenant-A")),))
        with pytest.raises(errors.InsufficientPrivilege):
            with conn.transaction():
                conn.execute(sql.SQL("DELETE FROM {}").format(sql.Identifier(table)))
