"""The sender's side: check right before sending, confirm right after.

    check()          -> outreach_send_check() in the database (single source of truth)
    check_and_alert()-> same, and pings the owner ONCE if a duplicate first touch was attempted
    confirm_send()   -> store the provider-confirmed message, THEN write Airtable "Emailed At"
Owner actions: owner_set_status(), mark_won(), mark_lost().
"""
from __future__ import annotations

import os
import urllib.parse
from datetime import datetime, timezone
from typing import Callable

import httpx

from cloudos.conversations import notify, store

AirtableWriter = Callable[[str, datetime], bool]


def check(conn, email: str, kind: str, company_id: str | None = None) -> dict:
    return store.send_check(conn, email, kind, company_id)


def check_and_alert(conn, email: str, kind: str, company_id: str | None = None, send: notify.Sender | None = None) -> dict:
    r = check(conn, email, kind, company_id)
    if not r["allowed"] and "already_contacted" in r["reasons"]:
        key = r.get("company_id") or email.lower()
        name = None
        if r.get("company_id"):
            c = store.company(conn, r["company_id"])
            name = c["company_name"] if c else None
        text = "\n".join([
            "**BRIGHTREACH - DUPLICATE EMAIL BLOCKED**", "",
            f"**Company:** {name or 'unknown'}", f"**Contact:** {email}",
            f"**What happened:** the sender tried to send a first-touch email to a company we already emailed.",
            "**Result:** blocked - nothing was sent.",
            "**Action needed from you:** None. (This means the sender's list has a stale row; it is being skipped.)"])
        r["alert"] = notify.notify_once(conn, dedupe_key=f"br:dup:{key}", code="send.duplicate_blocked",
                                        severity="high", text=text, company_id=r.get("company_id"), send=send)
    return r


def airtable_emailed_at(rec_id: str, when: datetime) -> bool:
    """Stamp Emailed At on the Airtable lead - only ever called after a confirmed send."""
    key, base, table = (os.environ.get(k, "") for k in ("AIRTABLE_API_KEY", "AIRTABLE_BASE_ID", "AIRTABLE_TABLE_NAME"))
    if not (key and base and table):
        return False
    url = f"https://api.airtable.com/v0/{base}/{urllib.parse.quote(table)}/{rec_id}"
    with httpx.Client(timeout=30, headers={"Authorization": f"Bearer {key}"}) as c:
        cur = c.get(url)
        cur.raise_for_status()
        if cur.json().get("fields", {}).get("Emailed At"):
            return True           # the sender already stamped it; never overwrite the original time
        resp = c.patch(url, json={"fields": {"Emailed At": when.astimezone(timezone.utc).isoformat()}})
        resp.raise_for_status()
    return True


def confirm_send(conn, *, company_id: str, recipient: str, sender: str, subject: str, body: str, sent_at: datetime,
                 provider: str, provider_message_id: str | None, thread_id: str | None = None, kind: str = "cold",
                 airtable: AirtableWriter | None = airtable_emailed_at, **kw) -> dict:
    """Record a send ONLY if the provider confirmed it. Emailed At is written after the DB commit, so
    the send guard already blocks a duplicate even if the Airtable call fails."""
    if not provider_message_id:
        return {"recorded": False, "reason": "no provider message id - not a confirmed send", "emailed_at_written": False}
    mid = store.record_confirmed_send(conn, company_id=company_id, recipient=recipient, sender=sender, subject=subject,
                                      body=body, sent_at=sent_at, provider=provider,
                                      provider_message_id=provider_message_id.strip().strip("<>").lower(),
                                      thread_id=thread_id, kind=kind, **kw)
    conn.commit()
    written = False
    rec = store.airtable_record_of(conn, company_id)
    if airtable and rec and kind in ("cold", "followup"):
        try:
            written = bool(airtable(rec, sent_at))
        except Exception as exc:  # noqa: BLE001 - DB already holds the truth; flag it
            notify.notify_once(conn, dedupe_key=f"br:emailedat:{rec}", code="send.emailed_at_failed", severity="high",
                               company_id=company_id, text=f"**BRIGHTREACH - Emailed At not written**\n\nA confirmed send to "
                               f"{recipient} could not be stamped in Airtable ({type(exc).__name__}). The database already "
                               "blocks a second email to them. Action needed: none.")
    return {"recorded": mid is not None, "message_id": mid, "emailed_at_written": written, "airtable_record": rec}


def owner_set_status(conn, company_id: str, to: str, note: str) -> dict:
    row = store.set_status(conn, company_id, to, f"owner: {note}", actor="owner", owner_override=True)
    store.resolve_attention(conn, company_id, note)
    store.add_fact(conn, company_id, "other", f"Owner note: {note}", {}, "owner")
    conn.commit()
    return row


def mark_won(conn, company_id: str, setup_usd: float | None = None, monthly_usd: float | None = None,
             offer: str | None = None, send: notify.Sender | None = None) -> dict:
    row = store.set_status(conn, company_id, "won", "owner confirmed the deal", actor="owner", owner_override=True,
                           proposal_status="accepted")
    conn.execute("INSERT INTO deals (company_id, offer, setup_usd, monthly_usd, won_at) VALUES (%s,%s,%s,%s,now()) "
                 "ON CONFLICT (company_id) DO UPDATE SET offer = EXCLUDED.offer, setup_usd = EXCLUDED.setup_usd, "
                 "monthly_usd = EXCLUDED.monthly_usd, won_at = now(), lost_at = NULL, updated_at = now()",
                 (company_id, offer, setup_usd, monthly_usd))
    store.resolve_attention(conn, company_id, "deal won")
    conn.commit()
    c = store.company(conn, company_id)
    money = " + ".join(x for x in (f"${setup_usd:,.0f} setup" if setup_usd else "",
                                   f"${monthly_usd:,.0f}/month" if monthly_usd else "") if x) or "amount not recorded"
    notify.notify_once(conn, dedupe_key=f"br:won:{company_id}", code="deal.won", severity="high", company_id=company_id,
                       send=send, text=f"**BRIGHTREACH - DEAL WON**\n\n**Company:** {c['company_name']}\n**Deal:** {money}\n"
                                       "**Next step:** onboarding. Cold emails to them are permanently off.")
    return row


def mark_lost(conn, company_id: str, note: str) -> dict:
    row = store.set_status(conn, company_id, "lost", f"owner: {note}", actor="owner", owner_override=True)
    conn.execute("INSERT INTO deals (company_id, lost_at, note) VALUES (%s, now(), %s) ON CONFLICT (company_id) DO UPDATE "
                 "SET lost_at = now(), note = EXCLUDED.note, updated_at = now()", (company_id, note))
    store.resolve_attention(conn, company_id, note)
    conn.commit()
    return row
