"""True DB round-trip tests (Agent 3). Skipped unless DATABASE_URL is set
(contract §16). Requires Agent 4's cloudos.db and applied migrations.

Isolation from the live worker
------------------------------
These tests run against the real shared ``jobs`` table, where a production
worker polls every few seconds and claims ANY due queued job (the claim query
has no type filter). A job committed as 'queued' can therefore be stolen and
completed by that worker before the test claims it — which made the round-trip
test fail about half the time with ``'succeeded' == 'queued'``.

Fix, without touching production code or the worker: the test job is inserted
through ``enqueue`` on a connection whose ``commit`` is deferred, so the INSERT
stays inside an open transaction that no other session can see. The very next
queue call (``claim_next`` / ``fail``) transitions the row and commits both
steps atomically, so the job is never visible to anyone else as a due 'queued'
row. The priority is also the lowest possible int so ``claim_next`` returns
our own job ahead of any real work.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"), reason="integration test needs DATABASE_URL"
)

MARKER = f"itest-{uuid.uuid4()}"
ITEST_PRIORITY = -2147483648  # min int4: always sorts before any real job


class _DeferredCommit:
    """Connection proxy whose commit() is a no-op, so a queue helper's write
    stays in the open transaction until the real connection commits."""

    def __init__(self, conn):
        self._conn = conn

    def commit(self):
        pass

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _enqueue_invisible(conn, queue):
    """Enqueue a test job inside an uncommitted transaction (invisible to the
    live worker until the caller's next queue call commits)."""
    return queue.enqueue(_DeferredCommit(conn), "noop", {"marker": MARKER}, priority=ITEST_PRIORITY)


@pytest.fixture
def db_conn():
    db = pytest.importorskip("cloudos.db", reason="cloudos.db (Agent 4) not present yet")
    db.migrate()
    with db.get_conn() as conn:
        yield conn
        # best-effort cleanup of anything this test created
        try:
            conn.rollback()
            with conn.cursor() as cur:
                cur.execute("DELETE FROM jobs WHERE payload->>'marker' = %s", (MARKER,))
            conn.commit()
        except Exception:
            conn.rollback()


def test_enqueue_claim_complete_round_trip(db_conn):
    from cloudos.worker import queue

    created = _enqueue_invisible(db_conn, queue)
    assert created["status"] == "queued"

    # claim_next sees our own uncommitted row and commits INSERT + lock together
    claimed = queue.claim_next(db_conn, "itest-instance")
    assert claimed is not None
    if claimed["id"] != created["id"]:
        # Never leave a real job stranded as 'running' under the test's name.
        with db_conn.cursor() as cur:
            cur.execute(queue.RECOVER_SQL, ("itest-instance",))
        db_conn.commit()
        pytest.fail(f"claimed foreign job {claimed['id']} instead of {created['id']}")
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

    created = _enqueue_invisible(db_conn, queue)
    # fail() commits INSERT + requeue together: the row first becomes visible
    # already deferred into the future, so the live worker cannot claim it.
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
