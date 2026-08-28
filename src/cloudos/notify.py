"""Owner notification. Owned by the lead (Agent 1).

Fail-closed events call notify_owner(). A notifications row is ALWAYS written
(durable); webhook delivery is best-effort (n8n forwards to email/Discord/etc.).
Never include secret material in message or meta.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

import httpx

from .config import get_settings
from .contracts import ErrorCode

log = logging.getLogger("cloudos.notify")


def notify_owner(
    db_conn: Optional[Any],
    severity: str,
    code: ErrorCode | str,
    message: str,
    meta: Optional[dict] = None,
) -> None:
    """Persist a notification and attempt webhook delivery. Never raises."""
    code_str = code.value if isinstance(code, ErrorCode) else str(code)
    meta = meta or {}
    delivered = False

    settings = get_settings()
    if settings.notify_webhook_url:
        try:
            resp = httpx.post(
                settings.notify_webhook_url,
                json={"severity": severity, "code": code_str, "message": message, "meta": meta},
                timeout=10.0,
            )
            delivered = resp.status_code < 300
        except Exception as exc:  # noqa: BLE001 — notification must never crash the caller
            log.warning("notify webhook failed: %s", exc)

    if db_conn is not None:
        try:
            with db_conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO notifications (severity, code, message, meta, delivered) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (severity, code_str, message, json.dumps(meta), delivered),
                )
            db_conn.commit()
        except Exception as exc:  # noqa: BLE001
            log.error("notification row insert failed: %s", exc)

    log.warning("OWNER NOTIFY [%s] %s: %s %s", severity, code_str, message, meta)
