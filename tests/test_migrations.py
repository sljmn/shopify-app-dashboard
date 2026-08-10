from pathlib import Path

import psycopg
import pytest


def test_core_tables_exist(db):
    cur = db.execute("""
        select table_name from information_schema.tables
        where table_schema='public' order by table_name
    """)
    names = {r[0] for r in cur.fetchall()}
    assert {"raw_app_events","charges","app_events","subscriptions",
            "shops","tracking_events","sync_state","schema_migrations",
            "organizations","apps","operations_state"} <= names


def test_every_app_owned_table_has_a_required_app_id(db):
    tables = {
        "raw_app_events", "app_events", "charges", "subscriptions", "shops",
        "transactions", "sync_state", "usage_events", "ga4_daily",
        "annotations", "tracking_events",
    }
    rows = db.execute(
        """
        select table_name, is_nullable
        from information_schema.columns
        where table_schema = 'public' and column_name = 'app_id'
        """
    ).fetchall()
    assert {table for table, nullable in rows if nullable == "NO"} == tables


def test_multi_app_migration_refuses_ambiguous_existing_rows(db):
    migrations = Path(__file__).parents[1] / "src/app_dashboard/migrations"
    schema = "migration_refusal_test"
    db.execute(f"drop schema if exists {schema} cascade")
    db.execute(f"create schema {schema}")
    db.execute(f"set search_path to {schema}")
    try:
        for path in sorted(migrations.glob("0[01][0-9]_*.sql")):
            if path.name == "011_multi_app.sql":
                break
            db.execute(path.read_text())
        db.execute("insert into raw_app_events (id) values ('ambiguous')")
        with pytest.raises(psycopg.errors.RaiseException, match="new empty database"):
            db.execute((migrations / "011_multi_app.sql").read_text())
    finally:
        db.execute("set search_path to public")
        db.execute(f"drop schema if exists {schema} cascade")

def test_migrations_are_idempotent(db):
    from app_dashboard.db import run_migrations
    run_migrations(db)   # second run must not raise
