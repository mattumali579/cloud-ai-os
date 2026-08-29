"""DB access for the Agent API.

`cloudos.db` (Agent 4) is imported LAZILY inside functions so the API module
imports (and /healthz answers) even when the DB layer is missing or down.
All SQL is parametrized; rows come back as dicts (db.py uses dict_row).
"""
from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

from cloudos.contracts import CloudOSError, ErrorCode, JobStatus

JOB_COLUMNS = (
    "id, type, status, priority, payload, result, error_code, error, "
    "attempts, max_attempts, run_at, created_at, started_at, finished_at, "
    "locked_by, locked_at"
)

RUN_COLUMNS = (
    "run_id, job_id, agent, workflow, model, level, started_at, finished_at, "
    "status, prompt_tokens, completion_tokens, cost_usd, error_code, "
    "input_hash, output_hash"
)


@contextmanager
def _conn():
    """Yield a pooled connection; map unavailability to DEPENDENCY_UNAVAILABLE.

    Never put connection strings or credentials in the error — class name only.
    """
    try:
        from cloudos import db
    except Exception as exc:  # module missing (built in parallel) or import error
        raise CloudOSError(
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            "database layer unavailable",
            {"reason": exc.__class__.__name__},
        ) from exc
    try:
        with db.get_conn() as conn:
            yield conn
    except CloudOSError:
        raise
    except Exception as exc:  # pool/connection/SQL failure — DB is down or unreachable
        raise CloudOSError(
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            "database unavailable",
            {"reason": exc.__class__.__name__},
        ) from exc


def to_jsonable(value: Any) -> Any:
    """Recursively convert DB row values (UUID/datetime/Decimal/Enum) to JSON-safe types."""
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Enum):
        return value.value
    return value


def parse_job_id(job_id: str) -> str:
    try:
        return str(uuid.UUID(job_id))
    except (ValueError, AttributeError, TypeError):
        raise CloudOSError(
            ErrorCode.VALIDATION_ERROR, "invalid job id (must be a UUID)", {"id": str(job_id)[:64]}
        ) from None


def create_job(
    type: str,
    payload: dict,
    priority: int = 100,
    run_at: Optional[datetime] = None,
    max_attempts: int = 3,
) -> dict:
    sql = (
        f"INSERT INTO jobs (type, payload, priority, run_at, max_attempts) "
        f"VALUES (%s, %s::jsonb, %s, COALESCE(%s, now()), %s) "
        f"RETURNING {JOB_COLUMNS}"
    )
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (type, json.dumps(payload), priority, run_at, max_attempts))
            row = cur.fetchone()
        conn.commit()
    return to_jsonable(row)


def get_job(job_id: str) -> dict:
    job_id = parse_job_id(job_id)
    sql = f"SELECT {JOB_COLUMNS} FROM jobs WHERE id = %s"
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (job_id,))
            row = cur.fetchone()
    if row is None:
        from .errors import NotFound

        raise NotFound(f"job {job_id} not found")
    return to_jsonable(row)


def list_jobs(
    status: Optional[str] = None, type: Optional[str] = None, limit: int = 50
) -> list[dict]:
    if status is not None:
        try:
            status = JobStatus(status).value
        except ValueError:
            raise CloudOSError(
                ErrorCode.VALIDATION_ERROR,
                "invalid status filter",
                {"status": str(status)[:64], "allowed": [s.value for s in JobStatus]},
            ) from None
    limit = max(1, min(int(limit), 200))

    conditions: list[str] = []
    params: list[Any] = []
    if status is not None:
        conditions.append("status = %s")
        params.append(status)
    if type is not None:
        conditions.append("type = %s")
        params.append(type)
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    sql = f"SELECT {JOB_COLUMNS} FROM jobs{where} ORDER BY created_at DESC LIMIT %s"
    params.append(limit)

    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
    return [to_jsonable(r) for r in rows]


def cancel_job(job_id: str) -> dict:
    """Cancel a QUEUED job. Non-queued → VALIDATION_ERROR; missing → NotFound."""
    job_id = parse_job_id(job_id)
    sql = (
        f"UPDATE jobs SET status = 'cancelled', finished_at = now() "
        f"WHERE id = %s AND status = 'queued' RETURNING {JOB_COLUMNS}"
    )
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (job_id,))
            row = cur.fetchone()
        conn.commit()
    if row is not None:
        return to_jsonable(row)

    # Distinguish "not found" from "not cancellable".
    current = get_job(job_id)  # raises NotFound if the job does not exist
    raise CloudOSError(
        ErrorCode.VALIDATION_ERROR,
        "only queued jobs can be cancelled",
        {"id": job_id, "status": current.get("status")},
    )


def list_runs(limit: int = 50) -> list[dict]:
    limit = max(1, min(int(limit), 200))
    sql = (
        f"SELECT {RUN_COLUMNS} FROM agent_runs "
        f"ORDER BY started_at DESC NULLS LAST LIMIT %s"
    )
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (limit,))
            rows = cur.fetchall()
    return [to_jsonable(r) for r in rows]


def provider_status_rows() -> list[dict]:
    """Subscription provider observability rows. Contains NO credentials —
    auth_mode is a label (e.g. 'subscription_oauth'), never a token."""
    sql = (
        "SELECT provider, auth_mode, provider_type, state, last_success, "
        "last_auth_validation, task_count, last_failure_reason, cooldown_until, "
        "updated_at FROM provider_status ORDER BY provider"
    )
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, ())
            rows = cur.fetchall()
    return [to_jsonable(r) for r in rows]


def quota_today() -> list[dict]:
    sql = (
        "SELECT provider, day, requests, units FROM quota_usage "
        "WHERE day = CURRENT_DATE ORDER BY provider"
    )
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, ())
            rows = cur.fetchall()
    return [to_jsonable(r) for r in rows]
