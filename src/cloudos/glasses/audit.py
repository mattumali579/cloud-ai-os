"""Best-effort, sanitized Jarvis glasses events for the private ops audit DB."""

from __future__ import annotations

import hashlib
import json
import os
import queue
import threading
import time
from typing import Any

from psycopg_pool import ConnectionPool


_AUDIT_URL_ENV = "JARVIS_AUDIT_DATABASE_URL"
_ALLOWED_DETAIL_KEYS = {
    "active",
    "actions",
    "channel",
    "classification",
    "conversation_id",
    "duplicate_of",
    "error_type",
    "job_id",
    "listen",
    "request_id",
    "source",
    "tool_call_id",
}
_TERMINAL_FAILURES = {
    "background_task_failed",
    "private_transcript_paused",
    "result_verification_failed",
    "speech_failed",
}
_TERMINAL_SUCCESSES = {"result_verified", "speech_acknowledged"}
_EVENTS_TO_PERSIST = {
    "accepted",
    "background_task_cancelled",
    "background_task_failed",
    "cancel_requested",
    "private_transcript_paused",
    "queued",
    "result_verification_failed",
    "result_verified",
    "speech_acknowledged",
    "speech_failed",
    "started",
}

_queue: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=512)
_thread: threading.Thread | None = None
_thread_lock = threading.Lock()
_recent_fingerprints: dict[str, float] = {}
_FINGERPRINT_COOLDOWN_S = 60.0


def _safe_text(value: Any, limit: int = 160) -> str:
    return str(value or "")[:limit]


def _outcome(event: str) -> str:
    if event in _TERMINAL_FAILURES or "failed" in event:
        return "failed"
    if event in _TERMINAL_SUCCESSES:
        return "succeeded"
    if "cancel" in event:
        return "cancelled"
    if event in {"accepted", "queued", "started"}:
        return event
    return "observed"


def build_activity_record(
    correlation_id: str,
    event: str,
    *,
    error: bool = False,
    **fields: Any,
) -> dict[str, Any] | None:
    """Build a record without transcripts, commands, results, tokens, or secrets."""
    if event not in _EVENTS_TO_PERSIST:
        return None
    details = {
        key: value
        for key, value in fields.items()
        if key in _ALLOWED_DETAIL_KEYS and value not in (None, "", [], {})
    }
    details["event_id"] = correlation_id
    outcome = _outcome(event)
    fingerprint_basis = ":".join(
        (
            "jarvis_glasses",
            event,
            _safe_text(fields.get("error_type")),
            _safe_text(fields.get("classification")),
            outcome,
        )
    )
    fingerprint = hashlib.sha256(fingerprint_basis.encode("utf-8")).hexdigest()[:24]
    return {
        "source": "jarvis_glasses",
        "source_id": correlation_id,
        "event_type": event,
        "outcome": outcome,
        "severity": "error" if error or outcome == "failed" else "info",
        "fingerprint": fingerprint,
        "details": details,
    }


def enqueue_activity_event(
    correlation_id: str,
    event: str,
    *,
    error: bool = False,
    **fields: Any,
) -> bool:
    """Queue one audit write when a dedicated private DB URL is configured."""
    if not os.environ.get(_AUDIT_URL_ENV):
        return False
    record = build_activity_record(correlation_id, event, error=error, **fields)
    if record is None:
        return False
    fingerprint = str(record.get("fingerprint") or "")
    if fingerprint:
        now = time.monotonic()
        last = _recent_fingerprints.get(fingerprint)
        if last is not None and now - last < _FINGERPRINT_COOLDOWN_S:
            return False
        _recent_fingerprints[fingerprint] = now
        if len(_recent_fingerprints) > 256:
            cutoff = now - _FINGERPRINT_COOLDOWN_S
            for key, seen in list(_recent_fingerprints.items()):
                if seen < cutoff:
                    _recent_fingerprints.pop(key, None)
    _ensure_worker()
    try:
        _queue.put_nowait(record)
    except queue.Full:
        return False
    return True


def _ensure_worker() -> None:
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    with _thread_lock:
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_audit_worker, name="jarvis-audit", daemon=True)
            _thread.start()


def _audit_worker() -> None:
    database_url = os.environ.get(_AUDIT_URL_ENV, "")
    if not database_url:
        return
    pool = ConnectionPool(
        database_url,
        min_size=0,
        max_size=2,
        timeout=5,
        kwargs={"prepare_threshold": None},
        name="jarvis-audit",
        open=True,
    )
    try:
        while True:
            record = _queue.get()
            if record is None:
                return
            try:
                with pool.connection() as conn:
                    conn.execute(
                        """
                        INSERT INTO ops_audit.activity_log
                            (source, source_id, event_type, outcome, severity, fingerprint, details)
                        VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                        """,
                        (
                            record["source"],
                            record["source_id"],
                            record["event_type"],
                            record["outcome"],
                            record["severity"],
                            record["fingerprint"],
                            json.dumps(record["details"], ensure_ascii=True, default=str),
                        ),
                    )
            except Exception:
                # Audit unavailability must never stop voice or desktop control.
                pass
            finally:
                _queue.task_done()
    finally:
        pool.close()
