"""Postgres-backed job queue operations (contract §5 `jobs` table, §8 semantics).

Every function takes an open psycopg connection (dict rows per §5) and manages
its own commit/rollback. Plain SQL only — the queue IS Postgres, claimed with
``FOR UPDATE SKIP LOCKED``. No ORM, no Redis, no Celery (contract §3).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Optional

from cloudos.contracts import ErrorCode, JobStatus

log = logging.getLogger("cloudos.worker.queue")

MAX_ERROR_LEN = 2000

CLAIM_SQL = (
    "SELECT * FROM jobs "
    "WHERE status = 'queued' AND (run_at IS NULL OR run_at <= now()) "
    "ORDER BY priority, run_at "
    "LIMIT 1 "
    "FOR UPDATE SKIP LOCKED"
)

LOCK_SQL = (
    "UPDATE jobs SET status = 'running', locked_by = %s, locked_at = now(), "
    "started_at = now() "
    "WHERE id = %s RETURNING *"
)

COMPLETE_SQL = (
    "UPDATE jobs SET status = 'succeeded', result = %s::jsonb, finished_at = now(), "
    "locked_by = NULL, locked_at = NULL "
    "WHERE id = %s"
)

RETRY_SQL = (
    "UPDATE jobs SET status = 'queued', attempts = %s, error_code = %s, error = %s, "
    "run_at = now() + make_interval(mins => %s), "
    "locked_by = NULL, locked_at = NULL, started_at = NULL "
    "WHERE id = %s"
)

FAIL_SQL = (
    "UPDATE jobs SET status = 'failed', attempts = %s, error_code = %s, error = %s, "
    "finished_at = now(), locked_by = NULL, locked_at = NULL "
    "WHERE id = %s"
)

BLOCK_SQL = (
    "UPDATE jobs SET status = 'blocked', error_code = %s, error = %s, "
    "finished_at = now(), locked_by = NULL, locked_at = NULL "
    "WHERE id = %s"
)

ENQUEUE_SQL = (
    "INSERT INTO jobs (type, payload, priority, run_at, max_attempts) "
    "VALUES (%s, %s::jsonb, %s, COALESCE(%s, now()), %s) RETURNING *"
)

RECOVER_SQL = (
    "UPDATE jobs SET status = 'queued', locked_by = NULL, locked_at = NULL, "
    "started_at = NULL "
    "WHERE status = 'running' AND locked_by = %s"
)


def backoff_minutes(attempts: int) -> int:
    """Exponential backoff per §8: run_at = now() + 2^attempts minutes.

    ``attempts`` is the value AFTER the increment for the failure being recorded.
    """
    return 2 ** int(attempts)


def _code_str(error_code: ErrorCode | str | None) -> Optional[str]:
    if error_code is None:
        return None
    return error_code.value if isinstance(error_code, ErrorCode) else str(error_code)


def _clip_error(error: Optional[str]) -> str:
    return (error or "")[:MAX_ERROR_LEN]


def claim_next(conn: Any, instance_name: str) -> Optional[dict]:
    """Claim ONE due queued job for this instance, or None.

    SELECT ... FOR UPDATE SKIP LOCKED and the running/locked UPDATE happen in a
    single transaction so concurrent workers never claim the same job.
    """
    with conn.cursor() as cur:
        cur.execute(CLAIM_SQL)
        row = cur.fetchone()
        if row is None:
            conn.rollback()
            return None
        cur.execute(LOCK_SQL, (instance_name, row["id"]))
        job = cur.fetchone()
    conn.commit()
    return job


def complete(conn: Any, job_id: Any, result: Optional[dict]) -> None:
    """Mark a job succeeded and store its result."""
    with conn.cursor() as cur:
        cur.execute(COMPLETE_SQL, (json.dumps(result or {}, default=str), job_id))
    conn.commit()


def fail(conn: Any, job: dict, error_code: ErrorCode | str | None, error: Optional[str]) -> str:
    """Record a retryable failure.

    attempts += 1; while attempts < max_attempts the job goes back to 'queued'
    with run_at = now() + 2^attempts minutes; otherwise it becomes 'failed'.
    Returns the resulting status ('queued' or 'failed').
    """
    attempts = int(job.get("attempts") or 0) + 1
    max_attempts = int(job.get("max_attempts") or 3)
    code = _code_str(error_code)
    msg = _clip_error(error)
    if attempts < max_attempts:
        with conn.cursor() as cur:
            cur.execute(RETRY_SQL, (attempts, code, msg, backoff_minutes(attempts), job["id"]))
        conn.commit()
        return JobStatus.QUEUED.value
    with conn.cursor() as cur:
        cur.execute(FAIL_SQL, (attempts, code, msg, job["id"]))
    conn.commit()
    return JobStatus.FAILED.value


def block(conn: Any, job_id: Any, error_code: ErrorCode | str | None, error: Optional[str]) -> None:
    """Fail closed: status='blocked', NO retry (contract §0/§8)."""
    with conn.cursor() as cur:
        cur.execute(BLOCK_SQL, (_code_str(error_code), _clip_error(error), job_id))
    conn.commit()


def enqueue(
    conn: Any,
    type: str,  # noqa: A002 — parameter name pinned by contract §8
    payload: Optional[dict],
    priority: int = 100,
    run_at: Optional[datetime] = None,
    max_attempts: int = 3,
) -> Optional[dict]:
    """Insert a new queued job and return the created row."""
    with conn.cursor() as cur:
        cur.execute(
            ENQUEUE_SQL,
            (type, json.dumps(payload or {}, default=str), priority, run_at, max_attempts),
        )
        job = cur.fetchone()
    conn.commit()
    return job


def recover_stale(conn: Any, instance_name: str) -> int:
    """Requeue jobs left 'running' by a previous incarnation of THIS instance.

    Safe on restart: the old process holding these locks is dead (compute is
    disposable). Only touches rows locked_by our own instance name.
    """
    with conn.cursor() as cur:
        cur.execute(RECOVER_SQL, (instance_name,))
        count = getattr(cur, "rowcount", 0) or 0
    conn.commit()
    return max(int(count), 0)
