"""Owner notifications for replies: useful, specific, and exactly once per event.

Delivery reuses the existing path: the Cloudflare Worker /notify relay
(CLOUDOS_NOTIFY_URL + CLOUDOS_NOTIFY_TOKEN) or a direct DISCORD_WEBHOOK_URL,
via cloudos.email_alerts.notifier. Every notification is first written to the
`notifications` table under a unique dedupe_key; a second attempt to notify the
same event finds the row and does nothing.
"""
from __future__ import annotations

import json
import os
from typing import Callable

from cloudos.conversations.text import one_line

AIRTABLE_BASE = os.environ.get("AIRTABLE_BASE_ID_PUBLIC", "appQNVOTVqdIYjSho")
AIRTABLE_TABLE = os.environ.get("AIRTABLE_LEADS_TABLE_ID", "tblggJQ6ttPCvXygu")
SEVERITY_COLOR = {"urgent": 15158332, "high": 15844367, "normal": 3447003}

# which classifications ping the owner (routine ones are recorded silently)
NOTIFY_LABELS = {"INTERESTED", "PRICE_QUESTION", "MORE_INFORMATION", "MEETING_REQUEST", "READY_TO_BUY", "OBJECTION",
                 "REFERRAL", "NEEDS_REVIEW"}

Sender = Callable[[dict], tuple[bool, str]]


def record_link(rec_id: str | None) -> str | None:
    return f"https://airtable.com/{AIRTABLE_BASE}/{AIRTABLE_TABLE}/{rec_id}" if rec_id else None


def reply_card(*, company: str, contact: str, prev_status: str, new_status: str, label: str, said: str, context: str,
               interpretation: str, next_step: str, offer: str | None, price: str | None, action_needed: str,
               link: str | None, title: str = "BRIGHTREACH REPLY") -> str:
    lines = [
        f"**{title}**", "",
        f"**Company:** {company}",
        f"**Contact:** {contact}",
        f"**Status:** {prev_status.upper()} → {new_status.upper()}  ({label})",
        f"**They said:** “{one_line(said, 420)}”",
        f"**Context:** {one_line(context, 420)}",
        f"**Interpretation:** {interpretation}",
        f"**Recommended next step:** {next_step}",
    ]
    if offer:
        lines.append(f"**Current offer:** {offer}")
    if price:
        lines.append(f"**Price on the table:** {price}")
    lines.append(f"**Action needed from you:** {action_needed}")
    if link:
        lines.append(f"**Record:** {link}")
    return "\n".join(lines)


def default_sender(payload: dict) -> tuple[bool, str]:
    from cloudos.email_alerts import notifier
    url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    try:
        status = notifier._post(url, payload)
        return 200 <= status < 300, f"HTTP {status}"
    except Exception as exc:  # noqa: BLE001 - never crash the pipeline over a ping
        return False, type(exc).__name__


def notify_once(conn, *, dedupe_key: str, code: str, severity: str, text: str, company_id: str | None = None,
                meta: dict | None = None, send: Sender | None = None, deliver: bool = True) -> dict:
    """Insert the notification under dedupe_key; deliver only if this call created it.

    The row is committed BEFORE delivery, so a crash after posting can't cause a
    duplicate ping on the next run; an undelivered row is retried by flush().
    """
    row = conn.execute(
        "INSERT INTO notifications (severity, code, message, meta, delivered, dedupe_key, company_id) "
        "VALUES (%s,%s,%s,%s,false,%s,%s) ON CONFLICT (dedupe_key) WHERE dedupe_key IS NOT NULL DO NOTHING RETURNING id",
        (severity, code, text, json.dumps(meta or {}, default=str), dedupe_key, company_id)).fetchone()
    if row is None:
        return {"created": False, "delivered": None}
    conn.commit()
    if not deliver:
        return {"created": True, "delivered": False, "id": row["id"]}
    return {"created": True, **_deliver(conn, row["id"], severity, text, send)}


def _deliver(conn, nid: int, severity: str, text: str, send: Sender | None) -> dict:
    payload = {"embeds": [{"description": text[:4000], "color": SEVERITY_COLOR.get(severity, SEVERITY_COLOR["normal"])}]}
    ok, receipt = (send or default_sender)(payload)
    conn.execute("UPDATE notifications SET delivered = %s, delivered_at = CASE WHEN %s THEN now() END, "
                 "delivery_receipt = %s, attempts = attempts + 1 WHERE id = %s", (ok, ok, receipt[:200], nid))
    conn.commit()
    return {"delivered": ok, "receipt": receipt, "id": nid}


def flush(conn, send: Sender | None = None, max_attempts: int = 5) -> dict:
    rows = conn.execute("SELECT id, severity, message FROM notifications WHERE NOT delivered AND dedupe_key LIKE 'br:%%' "
                        "AND attempts < %s ORDER BY ts LIMIT 50", (max_attempts,)).fetchall()
    out = {"retried": 0, "delivered": 0}
    for r in rows:
        out["retried"] += 1
        out["delivered"] += int(_deliver(conn, r["id"], r["severity"], r["message"], send)["delivered"])
    return out
