"""Worker poll loop (contract §8).

Claims one queued job at a time (queue.claim_next → FOR UPDATE SKIP LOCKED),
dispatches by type to handlers.HANDLERS, and enforces the fail-closed doctrine
(§0): LIMIT REACHED → stop the task (block, no retry) → persist → log event →
notify owner. Other errors retry with exponential backoff until max_attempts.

Once per loop-day it ensures a `retention.prune` job exists (run_at at the next
03:00 UTC) — this daily op is the system's ONLY legitimate keepalive. No
per-minute anything (contract §0/§8).
"""
from __future__ import annotations

import json
import logging
import signal
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from cloudos.config import get_settings
from cloudos.contracts import CloudOSError, ErrorCode, FAIL_CLOSED_CODES, JobStatus
from cloudos.notify import notify_owner

from . import handlers, queue

log = logging.getLogger("cloudos.worker.loop")

PRUNE_JOB_TYPE = "retention.prune"
PRUNE_HOUR_UTC = 3
PRUNE_JOB_PRIORITY = 200  # housekeeping runs after default-priority (100) work

# A prune job counts as "already handled for today" when one is queued/running
# (whatever day it is scheduled for) or already succeeded today.
PRUNE_PENDING_SQL = (
    "SELECT count(*) AS n FROM jobs "
    "WHERE type = %s AND (status IN ('queued','running') "
    "OR (status = 'succeeded' AND finished_at >= date_trunc('day', now())))"
)

EVENT_INSERT_SQL = (
    "INSERT INTO events (level, source, code, message, meta) "
    "VALUES (%s, %s, %s, %s, %s::jsonb)"
)


def next_prune_run_at(now: Optional[datetime] = None) -> datetime:
    """Next 03:00 UTC strictly after `now`."""
    now = now or datetime.now(timezone.utc)
    candidate = now.replace(hour=PRUNE_HOUR_UTC, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def write_event(
    conn: Any, level: str, source: str, code: str, message: str, meta: Optional[dict] = None
) -> None:
    """Best-effort events row — never raises (an event must not break the loop)."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                EVENT_INSERT_SQL,
                (level, source, code, message, json.dumps(meta or {}, default=str)),
            )
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        log.error("event insert failed (%s: %s): %s", code, message, exc)
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass


class Worker:
    """Single-threaded poll-loop worker. One instance per process."""

    def __init__(self, instance_name: Optional[str] = None, poll_interval: Optional[float] = None):
        settings = get_settings()
        self.instance_name = instance_name or settings.instance_name
        self.poll_interval = (
            poll_interval if poll_interval is not None else settings.worker_poll_interval_seconds
        )
        self.stop_event = threading.Event()
        self._prune_day = None  # date the daily-prune check last ran

    # -- shutdown ---------------------------------------------------------
    def request_stop(self) -> None:
        self.stop_event.set()

    def _install_signal_handlers(self) -> None:
        def _handle(signum: int, frame: Any) -> None:  # noqa: ARG001
            log.info("signal %s received — graceful shutdown requested", signum)
            self.request_stop()

        for name in ("SIGTERM", "SIGINT"):
            sig = getattr(signal, name, None)
            if sig is None:
                continue
            try:
                signal.signal(sig, _handle)
            except (ValueError, OSError):  # not the main thread / unsupported
                pass

    # -- job processing ---------------------------------------------------
    def process_one(self, conn: Any, job: dict) -> str:
        """Dispatch a claimed job; returns the resulting job status."""
        job_id = job.get("id")
        job_type = job.get("type")
        log.info("job %s (%s) claimed by %s", job_id, job_type, self.instance_name)
        try:
            result = handlers.dispatch(job)
        except CloudOSError as exc:
            if exc.code in FAIL_CLOSED_CODES:
                # Contract §0: stop task → persist → log → notify. NO retry.
                queue.block(conn, job_id, exc.code, exc.message)
                write_event(
                    conn,
                    "error",
                    "worker",
                    exc.code.value,
                    f"job {job_id} ({job_type}) blocked: {exc.message}",
                    {"job_id": str(job_id), "job_type": job_type, "details": exc.details},
                )
                notify_owner(
                    conn,
                    "critical",
                    exc.code,
                    f"Fail-closed: job {job_id} ({job_type}) blocked — {exc.message}",
                    {"job_id": str(job_id), "job_type": job_type},
                )
                log.error(
                    "job %s (%s) BLOCKED [%s]: %s", job_id, job_type, exc.code.value, exc.message
                )
                return JobStatus.BLOCKED.value
            status = queue.fail(conn, job, exc.code, exc.message)
            log.warning(
                "job %s (%s) error [%s] → %s: %s",
                job_id, job_type, exc.code.value, status, exc.message,
            )
            return status
        except Exception as exc:  # noqa: BLE001 — a handler crash must not kill the loop
            status = queue.fail(
                conn, job, ErrorCode.INTERNAL_ERROR, f"{type(exc).__name__}: {exc}"
            )
            log.exception("job %s (%s) crashed → %s", job_id, job_type, status)
            return status
        queue.complete(conn, job_id, result)
        log.info("job %s (%s) succeeded", job_id, job_type)
        return JobStatus.SUCCEEDED.value

    # -- daily retention scheduling --------------------------------------
    def ensure_daily_prune(self, conn: Any, now: Optional[datetime] = None) -> bool:
        """Once per loop-day, make sure a retention.prune job exists.

        Enqueues one with run_at at the next 03:00 UTC if none is queued/running
        (and none already succeeded today). Returns True if a job was enqueued.
        """
        now = now or datetime.now(timezone.utc)
        today = now.date()
        if self._prune_day == today:
            return False
        with conn.cursor() as cur:
            cur.execute(PRUNE_PENDING_SQL, (PRUNE_JOB_TYPE,))
            row = cur.fetchone() or {}
        pending = int(row.get("n") or 0)
        if pending:
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001
                pass
            self._prune_day = today
            log.debug("daily %s already scheduled/ran (%d pending)", PRUNE_JOB_TYPE, pending)
            return False
        run_at = next_prune_run_at(now)
        job = queue.enqueue(
            conn,
            PRUNE_JOB_TYPE,
            {"scheduled_for": today.isoformat()},
            priority=PRUNE_JOB_PRIORITY,
            run_at=run_at,
        )
        self._prune_day = today
        log.info(
            "scheduled daily %s job %s for %s",
            PRUNE_JOB_TYPE,
            (job or {}).get("id"),
            run_at.isoformat(),
        )
        return True

    # -- main loop --------------------------------------------------------
    def run_forever(self, max_iterations: Optional[int] = None) -> None:
        import cloudos.db as db  # lazy: Agent 4 writes db.py in parallel

        try:
            applied = db.migrate()
            if applied:
                log.info("migrations applied at startup: %s", applied)
        except Exception as exc:  # noqa: BLE001 — idempotent; api may have migrated already
            log.warning("startup migrate failed (continuing): %s", exc)

        self._install_signal_handlers()
        log.info(
            "worker %s started (poll every %ss)", self.instance_name, self.poll_interval
        )

        # Requeue jobs a previous incarnation of this instance left 'running'.
        try:
            with db.get_conn() as conn:
                recovered = queue.recover_stale(conn, self.instance_name)
                if recovered:
                    log.warning(
                        "requeued %d stale running job(s) locked by %s",
                        recovered, self.instance_name,
                    )
                    write_event(
                        conn,
                        "warning",
                        "worker",
                        "STALE_JOBS_REQUEUED",
                        f"requeued {recovered} job(s) after restart",
                        {"instance": self.instance_name, "count": recovered},
                    )
        except Exception as exc:  # noqa: BLE001
            log.warning("stale-job recovery skipped: %s", exc)

        iterations = 0
        try:
            while not self.stop_event.is_set():
                if max_iterations is not None and iterations >= max_iterations:
                    break
                iterations += 1
                claimed = False
                try:
                    with db.get_conn() as conn:
                        self.ensure_daily_prune(conn)
                        job = queue.claim_next(conn, self.instance_name)
                        if job is not None:
                            claimed = True
                            self.process_one(conn, job)
                except KeyboardInterrupt:
                    raise
                except Exception as exc:  # noqa: BLE001 — e.g. DB briefly unreachable
                    log.exception("worker loop iteration failed: %s", exc)
                if not claimed:
                    # Idle → honor the poll interval; drain immediately otherwise.
                    self.stop_event.wait(self.poll_interval)
        except KeyboardInterrupt:
            log.info("keyboard interrupt — graceful shutdown")
        log.info("worker %s stopped", self.instance_name)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    Worker().run_forever()
