"""Retention pruning (contract §8).

Every retention window comes from Settings (the four *_RETENTION_DAYS env vars)
— NEVER hardcoded. Never touches queued/running/blocked jobs. Emits a metrics
event row BEFORE pruning. dry_run counts what WOULD be deleted but deletes
nothing.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from cloudos.config import Settings, get_settings

log = logging.getLogger("cloudos.worker.retention")

METRICS_EVENT_CODE = "RETENTION_METRICS"
EVENT_SOURCE = "worker.retention"

_METRICS_EVENT_SQL = (
    "INSERT INTO events (level, source, code, message, meta) "
    "VALUES (%s, %s, %s, %s, %s::jsonb)"
)


def _targets(settings: Settings) -> list[tuple[str, str, int]]:
    """(name, 'FROM <table> WHERE <due-for-deletion>', configured_days).

    Job targets select ONLY terminal statuses succeeded/cancelled/failed —
    queued/running/blocked rows can never match (contract §8). Blocked jobs are
    kept until the owner resolves them.
    """
    return [
        (
            "jobs_completed",
            "FROM jobs WHERE status IN ('succeeded','cancelled') "
            "AND finished_at < now() - make_interval(days => %s)",
            settings.completed_job_retention_days,
        ),
        (
            "jobs_failed",
            "FROM jobs WHERE status = 'failed' "
            "AND finished_at < now() - make_interval(days => %s)",
            settings.failed_job_retention_days,
        ),
        (
            "agent_runs",
            "FROM agent_runs WHERE coalesce(finished_at, started_at) "
            "< now() - make_interval(days => %s)",
            settings.agent_run_retention_days,
        ),
        (
            "events",
            "FROM events WHERE ts < now() - make_interval(days => %s)",
            settings.event_retention_days,
        ),
        (
            "notifications",
            "FROM notifications WHERE delivered = true "
            "AND ts < now() - make_interval(days => %s)",
            settings.event_retention_days,
        ),
    ]


def prune(dry_run: Optional[bool] = None, conn: Any = None) -> dict[str, dict]:
    """Prune aged rows. Returns {table: {selected, deleted, failures}}.

    dry_run defaults to settings.retention_dry_run. `conn` is injectable for
    tests; when omitted a pooled connection is taken from cloudos.db.
    """
    settings = get_settings()
    effective_dry_run = settings.retention_dry_run if dry_run is None else bool(dry_run)
    if conn is None:
        import cloudos.db as db  # lazy: Agent 4 writes db.py in parallel

        with db.get_conn() as pooled:
            return _prune(pooled, effective_dry_run, settings)
    return _prune(conn, effective_dry_run, settings)


def _prune(conn: Any, dry_run: bool, settings: Settings) -> dict[str, dict]:
    targets = _targets(settings)
    report: dict[str, dict] = {}

    # 1. Count what is due for deletion.
    for name, where, days in targets:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n " + where, (days,))
            row = cur.fetchone() or {}
        report[name] = {"selected": int(row.get("n") or 0), "deleted": 0, "failures": 0}

    total_selected = sum(v["selected"] for v in report.values())

    # 2. Metrics event BEFORE pruning (contract §8).
    meta = {
        "dry_run": dry_run,
        "selected": {k: v["selected"] for k, v in report.items()},
        "retention_days": {
            "agent_runs": settings.agent_run_retention_days,
            "events": settings.event_retention_days,
            "completed_jobs": settings.completed_job_retention_days,
            "failed_jobs": settings.failed_job_retention_days,
        },
    }
    try:
        with conn.cursor() as cur:
            cur.execute(
                _METRICS_EVENT_SQL,
                (
                    "info",
                    EVENT_SOURCE,
                    METRICS_EVENT_CODE,
                    f"retention prune ({'dry-run' if dry_run else 'live'}): "
                    f"{total_selected} row(s) selected",
                    json.dumps(meta, default=str),
                ),
            )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 — metrics failure must not stop pruning
        log.warning("retention metrics event insert failed: %s", exc)
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass

    # 3. Delete (unless dry-run).
    if dry_run:
        log.info(
            "retention DRY-RUN: would delete %s",
            {k: v["selected"] for k, v in report.items()},
        )
        return report

    for name, where, days in targets:
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE " + where, (days,))
                deleted = getattr(cur, "rowcount", 0) or 0
            conn.commit()
            report[name]["deleted"] = max(int(deleted), 0)
        except Exception as exc:  # noqa: BLE001 — one table failing must not stop the rest
            log.error("retention delete failed for %s: %s", name, exc)
            report[name]["failures"] += 1
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001
                pass

    log.info("retention pruned: %s", {k: v["deleted"] for k, v in report.items()})
    return report
