"""Integration tests for cloudos.db — require a live database (Agent 4).

Skipped entirely unless DATABASE_URL is set. Safe to run repeatedly: rows
created here are deleted in the same test.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="DATABASE_URL not set; integration tests need a live database",
)

import psycopg  # noqa: E402

from cloudos import db  # noqa: E402


def test_migrate_applies_then_is_idempotent():
    db.migrate()  # first run may or may not apply, depending on prior state
    assert db.migrate() == []  # second run must be a no-op
    with db.get_conn() as conn:
        rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
        versions = {row["version"] for row in rows}
    assert "001_init" in versions


def test_healthcheck_true_with_live_db():
    assert db.healthcheck() is True


def test_job_insert_select_round_trip():
    db.migrate()
    marker = f"test.roundtrip.{uuid.uuid4()}"
    with db.get_conn() as conn:
        row = conn.execute(
            "INSERT INTO jobs (type, payload) VALUES (%s, %s::jsonb) RETURNING id, status, priority",
            (marker, '{"k": 1}'),
        ).fetchone()
        assert row["status"] == "queued"      # dict_row + defaults
        assert row["priority"] == 100
        job_id = row["id"]

        got = conn.execute(
            "SELECT type, payload, attempts, max_attempts FROM jobs WHERE id = %s",
            (job_id,),
        ).fetchone()
        assert got["type"] == marker
        assert got["payload"] == {"k": 1}
        assert (got["attempts"], got["max_attempts"]) == (0, 3)

        conn.execute("DELETE FROM jobs WHERE id = %s", (job_id,))


def test_jobs_status_check_constraint():
    db.migrate()
    with pytest.raises(psycopg.errors.CheckViolation):
        with db.get_conn() as conn:
            conn.execute(
                "INSERT INTO jobs (type, status) VALUES ('test.badstatus', 'bogus')"
            )
    # connection context rolled back on exception — nothing persisted


def test_agent_runs_cost_usd_pinned_to_zero():
    db.migrate()
    with pytest.raises(psycopg.errors.CheckViolation):
        with db.get_conn() as conn:
            conn.execute(
                "INSERT INTO agent_runs (agent, cost_usd) VALUES ('test.paid', 0.01)"
            )


def test_pending_migrations_empty_after_migrate():
    db.migrate()
    assert db.pending_migrations() == []
