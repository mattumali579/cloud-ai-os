"""True DB round-trip tests (Agent 3). Skipped unless DATABASE_URL is set
(contract §16). Requires Agent 4's cloudos.db and applied migrations."""
from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"), reason="integration test needs DATABASE_URL"
)

MARKER = f"itest-{uuid.uuid4()}"


@pytest.fixture
def db_conn():
    db = pytest.importorskip("cloudos.db", reason="cloudos.db (Agent 4) not present yet")
    db.migrate()
    with db.get_conn() as conn:
        yield conn
        # best-effort cleanup of anything this test created
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM jobs WHERE payload->>'marker' = %s", (MARKER,))
            conn.commit()
        except Exception:
            conn.rollback()


def test_enqueue_claim_complete_round_trip(db_conn):
    from cloudos.worker import queue

    created = queue.enqueue(db_conn, "noop", {"marker": MARKER}, priority=1)
    assert created["status"] == "queued"

    claimed = queue.claim_next(db_conn, "itest-instance")
    assert claimed is not None
    # priority 1 sorts before default 100, so we should get our own job back
    assert claimed["id"] == created["id"]
    assert claimed["status"] == "running"
    assert claimed["locked_by"] == "itest-instance"

    queue.complete(db_conn, claimed["id"], {"ok": True})
    with db_conn.cursor() as cur:
        cur.execute("SELECT status, result FROM jobs WHERE id = %s", (claimed["id"],))
        row = cur.fetchone()
    db_conn.rollback()
    assert row["status"] == "succeeded"
    assert row["result"] == {"ok": True}


def test_fail_requeues_with_future_run_at(db_conn):
    from cloudos.worker import queue

    created = queue.enqueue(db_conn, "noop", {"marker": MARKER}, priority=1)
    status = queue.fail(db_conn, created, "INTERNAL_ERROR", "itest failure")
    assert status == "queued"
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT status, attempts, run_at > now() AS deferred FROM jobs WHERE id = %s",
            (created["id"],),
        )
        row = cur.fetchone()
    db_conn.rollback()
    assert row["status"] == "queued"
    assert row["attempts"] == 1
    assert row["deferred"] is True  # backoff pushed run_at into the future
