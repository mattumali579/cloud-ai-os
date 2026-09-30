"""Internal status mail through the three AgentMail inboxes (never prospect-facing).

    manager   interested/question replies, sender failures, big exceptions, the daily summary
    research  progress toward today's 300 Ready, discovery/email-finding problems
    followup  replies detected, sequences stopped, unsubscribes, follow-up status

Every message is first written to `notifications` under a unique dedupe_key
(prefix "am:"), so one event produces one email even across crashes and reruns.
Undelivered rows are retried by flush(). Without an AgentMail key the row stays
queued AND the text goes to Discord, so a failure still reaches the owner.
"""
from __future__ import annotations

import json
import os
from typing import Callable

import httpx

API = "https://api.agentmail.to/v0"
Poster = Callable[[str, dict], tuple[bool, str]]


def _key(cfg: dict) -> str:
    return (os.environ.get(cfg["agentmail"]["api_env"]) or "").strip()


def _to(cfg: dict) -> str:
    return (os.environ.get(cfg["agentmail"]["owner_env"]) or "").strip() or cfg["agentmail"]["inboxes"]["manager"]


def http_poster(cfg: dict) -> Poster | None:
    key = _key(cfg)
    if not key:
        return None

    def post(inbox: str, payload: dict) -> tuple[bool, str]:
        try:
            r = httpx.post(f"{API}/inboxes/{inbox}/messages/send", json=payload, timeout=30,
                           headers={"Authorization": f"Bearer {key}"})
            if 200 <= r.status_code < 300:
                return True, "agentmail " + str(r.json().get("message_id", ""))[:120]
            return False, f"agentmail HTTP {r.status_code}"
        except Exception as exc:  # noqa: BLE001 - a notice must never crash the sender
            return False, f"agentmail {type(exc).__name__}"
    return post


def notify(conn, cfg: dict, *, role: str, dedupe_key: str, subject: str, text: str, severity: str = "normal",
           company_id: str | None = None, post: Poster | None = None, discord=None) -> dict:
    inbox = cfg["agentmail"]["inboxes"][role]
    row = conn.execute(
        "INSERT INTO notifications (severity, code, message, meta, delivered, dedupe_key, company_id) "
        "VALUES (%s,%s,%s,%s,false,%s,%s) ON CONFLICT (dedupe_key) WHERE dedupe_key IS NOT NULL DO NOTHING RETURNING id",
        (severity, f"agentmail.{role}", text, json.dumps({"role": role, "inbox": inbox, "subject": subject}),
         f"am:{dedupe_key}", company_id)).fetchone()
    conn.commit()
    if row is None:
        return {"created": False}
    return {"created": True, **_deliver(conn, cfg, row["id"], inbox, subject, text, severity, post, discord)}


def _deliver(conn, cfg, nid, inbox, subject, text, severity, post, discord) -> dict:
    post = post or http_poster(cfg)
    if post is None:
        ok, receipt = False, "no AGENTMAIL_API_KEY yet"
        if discord and severity in ("high", "urgent"):
            try:
                d_ok, d_receipt = discord({"embeds": [{"description": f"**{subject}**\n\n{text}"[:4000]}]})
                receipt += f"; discord {'delivered' if d_ok else 'failed'} ({d_receipt})"
            except Exception as exc:  # noqa: BLE001
                receipt += f"; discord failed ({type(exc).__name__})"
    else:
        ok, receipt = post(inbox, {"to": [_to(cfg)], "subject": subject, "text": text,
                                   "labels": ["brightreach", severity]})
    conn.execute("UPDATE notifications SET delivered = %s, delivered_at = CASE WHEN %s THEN now() END, "
                 "delivery_receipt = %s, attempts = attempts + 1 WHERE id = %s", (ok, ok, receipt[:200], nid))
    conn.commit()
    return {"delivered": ok, "receipt": receipt}


def flush(conn, cfg: dict, post: Poster | None = None, max_attempts: int = 8) -> dict:
    post = post or http_poster(cfg)
    if post is None:
        return {"retried": 0, "delivered": 0, "waiting": conn.execute(
            "SELECT count(*) n FROM notifications WHERE NOT delivered AND dedupe_key LIKE 'am:%%'").fetchone()["n"]}
    # older than a day = stale news (e.g. "can't send yet" after it was fixed): the daily digest covers it
    rows = conn.execute("SELECT id, severity, message, meta FROM notifications WHERE NOT delivered AND dedupe_key LIKE 'am:%%' "
                        "AND attempts < %s AND ts > now() - interval '1 day' ORDER BY ts LIMIT 40",
                        (max_attempts,)).fetchall()
    out = {"retried": 0, "delivered": 0}
    for r in rows:
        meta = r["meta"] if isinstance(r["meta"], dict) else json.loads(r["meta"] or "{}")
        out["retried"] += 1
        out["delivered"] += int(_deliver(conn, cfg, r["id"], meta.get("inbox"), meta.get("subject", "BrightReach"),
                                         r["message"], r["severity"], post, None)["delivered"])
    return out
