"""Built-in job handlers (contract §8).

``HANDLERS`` maps job type -> Callable[[payload dict], result dict].

router / second_brain / db are owned by other agents and written in parallel —
they are imported lazily INSIDE the handler bodies so this module always
imports cleanly on its own.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Optional

import httpx

from cloudos.config import get_settings
from cloudos.contracts import CloudOSError, ErrorCode, PrivacyLabel, RouteRequest
from cloudos.worker.task_exec import handle_task_run

log = logging.getLogger("cloudos.worker.handlers")

NOTIFY_FLUSH_BATCH = 50


def handle_noop(payload: dict) -> dict:
    """Echo the payload back — pipeline smoke test."""
    return dict(payload or {})


def handle_ai_generate(payload: dict) -> dict:
    """Build a RouteRequest from the payload and call the AI router (§9)."""
    from cloudos.router import route  # lazy: Agent 8 writes this in parallel

    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise CloudOSError(
            ErrorCode.VALIDATION_ERROR,
            "ai.generate requires a non-empty 'prompt' string",
            {"field": "prompt"},
        )
    raw_label = payload.get("privacy_label", PrivacyLabel.INTERNAL.value)
    try:
        label = raw_label if isinstance(raw_label, PrivacyLabel) else PrivacyLabel(str(raw_label).lower())
    except ValueError as exc:
        raise CloudOSError(
            ErrorCode.VALIDATION_ERROR, "invalid privacy_label", {"privacy_label": str(raw_label)}
        ) from exc
    try:
        max_tokens = int(payload.get("max_tokens", 1024))
    except (TypeError, ValueError) as exc:
        raise CloudOSError(
            ErrorCode.VALIDATION_ERROR, "max_tokens must be an integer", {"field": "max_tokens"}
        ) from exc

    req = RouteRequest(
        prompt=prompt,
        task=str(payload.get("task", "general")),
        privacy_label=label,
        max_tokens=max_tokens,
        model_hint=payload.get("model_hint"),
        meta=dict(payload.get("meta") or {}),
    )
    result = route(req)
    if not result.ok:
        # Propagate the router's error code so the loop can decide
        # fail-closed (block) vs retry. Never re-wrap as INTERNAL unless absent.
        raise CloudOSError(
            result.error_code or ErrorCode.INTERNAL_ERROR,
            result.error or "router returned not-ok",
            {"level": result.level, "model": result.model},
        )
    return result.to_dict()


def handle_sb_reindex(payload: dict) -> dict:
    """Reindex the Second Brain metadata into sb_index (§11)."""
    from cloudos.second_brain import reindex  # lazy: Agent 9 writes this in parallel

    import cloudos.db as db  # lazy: Agent 4 writes db.py in parallel

    with db.get_conn() as conn:
        result = reindex(conn)
    return dict(result or {})


def handle_retention_prune(payload: dict) -> dict:
    """Run the retention pruner (worker-owned, see retention.py)."""
    from . import retention

    dry = payload.get("dry_run") if isinstance(payload, dict) else None
    return retention.prune(dry_run=dry if isinstance(dry, bool) else None)


def handle_notify_flush(payload: dict) -> dict:
    """Re-attempt webhook delivery for undelivered notifications rows."""
    import cloudos.db as db  # lazy: Agent 4 writes db.py in parallel

    settings = get_settings()
    try:
        limit = int(payload.get("limit", NOTIFY_FLUSH_BATCH))
    except (TypeError, ValueError):
        limit = NOTIFY_FLUSH_BATCH

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, severity, code, message, meta FROM notifications "
                "WHERE delivered = false ORDER BY ts LIMIT %s",
                (limit,),
            )
            rows = cur.fetchall() or []
        if not settings.notify_webhook_url:
            conn.rollback()
            return {"attempted": 0, "delivered": 0, "pending": len(rows)}

        delivered = 0
        for row in rows:
            meta = row.get("meta")
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except ValueError:
                    meta = {"raw": meta}
            try:
                resp = httpx.post(
                    settings.notify_webhook_url,
                    json={
                        "severity": row.get("severity"),
                        "code": row.get("code"),
                        "message": row.get("message"),
                        "meta": meta or {},
                    },
                    timeout=10.0,
                )
                ok = resp.status_code < 300
            except Exception as exc:  # noqa: BLE001 — a bad webhook must not fail the batch
                log.warning("notify.flush webhook failed for row %s: %s", row.get("id"), exc)
                ok = False
            if ok:
                with conn.cursor() as cur:
                    cur.execute("UPDATE notifications SET delivered = true WHERE id = %s", (row.get("id"),))
                conn.commit()
                delivered += 1
        return {"attempted": len(rows), "delivered": delivered, "pending": len(rows) - delivered}


HANDLERS: dict[str, Callable[[dict], dict]] = {
    "noop": handle_noop,
    "task.run": handle_task_run,
    "ai.generate": handle_ai_generate,
    "sb.reindex": handle_sb_reindex,
    "retention.prune": handle_retention_prune,
    "notify.flush": handle_notify_flush,
}


def dispatch(job: dict) -> dict:
    """Look up the handler for job['type'] and invoke it with the payload.

    Unknown type -> CloudOSError(VALIDATION_ERROR) (non-retryable by meaning,
    but routed through normal fail/retry — it will exhaust attempts quickly).
    """
    job_type = str(job.get("type") or "")
    handler = HANDLERS.get(job_type)
    if handler is None:
        raise CloudOSError(
            ErrorCode.VALIDATION_ERROR,
            f"unknown job type: {job_type!r}",
            {"job_type": job_type, "known_types": sorted(HANDLERS)},
        )
    payload: Any = job.get("payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload) if payload.strip() else {}
        except ValueError as exc:
            raise CloudOSError(
                ErrorCode.VALIDATION_ERROR, "payload is not valid JSON", {"job_type": job_type}
            ) from exc
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise CloudOSError(
            ErrorCode.VALIDATION_ERROR, "payload must be a JSON object", {"job_type": job_type}
        )
    result = handler(payload)
    return result if isinstance(result, dict) else {"result": result}
