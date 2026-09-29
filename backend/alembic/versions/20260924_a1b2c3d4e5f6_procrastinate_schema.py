"""procrastinate queue schema

Applies the schema shipped with the installed Procrastinate version, so that the job queue
is created by the same migration path as the application tables (ADR-0003).

Upgrading Procrastinate later: add a new Alembic revision that executes the matching
``procrastinate/sql/migrations/*.sql`` files for the version range.

Revision ID: a1b2c3d4e5f6
Revises: 7d1e52c3ac29
Create Date: 2026-09-24
"""
from collections.abc import Sequence
from importlib import resources

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "7d1e52c3ac29"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    sql = resources.files("procrastinate.sql").joinpath("schema.sql").read_text(encoding="utf-8")
    # Execute on the raw psycopg cursor *without* parameters (same transaction as Alembic):
    # - SQLAlchemy text() would parse ':' casts as bind params;
    # - exec_driver_sql() passes an empty params tuple, which makes psycopg treat the '%' in
    #   plpgsql RAISE messages as placeholders ("only '%s', '%b', '%t' are allowed").
    dbapi_conn = op.get_bind().connection.driver_connection
    with dbapi_conn.cursor() as cur:
        cur.execute(sql)


def downgrade() -> None:
    op.execute(
        """
        DO $$
        DECLARE r record;
        BEGIN
          FOR r IN SELECT tablename FROM pg_tables
                   WHERE schemaname = current_schema() AND tablename LIKE 'procrastinate%' LOOP
            EXECUTE format('DROP TABLE IF EXISTS %I CASCADE', r.tablename);
          END LOOP;
          FOR r IN SELECT p.oid::regprocedure AS sig FROM pg_proc p
                   JOIN pg_namespace n ON n.oid = p.pronamespace
                   WHERE n.nspname = current_schema() AND p.proname LIKE 'procrastinate%' LOOP
            EXECUTE format('DROP FUNCTION IF EXISTS %s CASCADE', r.sig);
          END LOOP;
          FOR r IN SELECT t.typname FROM pg_type t
                   JOIN pg_namespace n ON n.oid = t.typnamespace
                   WHERE n.nspname = current_schema() AND t.typname LIKE 'procrastinate%'
                     AND t.typtype IN ('e', 'c') LOOP
            EXECUTE format('DROP TYPE IF EXISTS %I CASCADE', r.typname);
          END LOOP;
        END $$;
        """
    )
