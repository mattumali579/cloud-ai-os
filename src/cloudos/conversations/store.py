"""Postgres memory for conversations. All writes for one event share one transaction
(the caller commits), so a crash never leaves a half-recorded reply."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterable

from cloudos.conversations import status as sm
from cloudos.conversations.text import addr, clean

OUTREACH_TO_STATUS = {
    "new": "discovered", "not_ready": "discovered", "rejected": "discovered", "outreach_ready": "ready",
    "handed_off": "ready", "contacted": "emailed", "replied": "replied", "bounced": "emailed",
    "unsubscribed": "do_not_contact", "do_not_contact": "do_not_contact",
}
STATUS_TO_OUTREACH = {  # keep the lead engine's own column consistent (it excludes these from new outreach)
    "emailed": "contacted", "do_not_contact": "do_not_contact",
}


def _j(v: Any) -> str:
    return json.dumps(v, default=str)


def now() -> datetime:
    return datetime.now(timezone.utc)


# ----------------------------------------------------------------- companies
def company(conn, company_id: str) -> dict | None:
    return conn.execute("SELECT * FROM companies WHERE company_id = %s", (company_id,)).fetchone()


def company_by_airtable_record(conn, rec_id: str) -> str | None:
    row = conn.execute("SELECT company_id FROM companies WHERE handoff_ref = %s OR historical_ids @> ARRAY[%s] LIMIT 1",
                       (f"airtable:{rec_id}", f"airtable:{rec_id}")).fetchone()
    return str(row["company_id"]) if row else None


def airtable_record_of(conn, company_id: str) -> str | None:
    row = conn.execute("SELECT handoff_ref, historical_ids FROM companies WHERE company_id = %s", (company_id,)).fetchone()
    if not row:
        return None
    for ref in [row["handoff_ref"] or "", *(row["historical_ids"] or [])]:
        if ref.startswith("airtable:rec"):
            return ref.split(":", 1)[1]
    st = conn.execute("SELECT airtable_record_id FROM company_conversation_state WHERE company_id = %s", (company_id,)).fetchone()
    return st["airtable_record_id"] if st else None


# --------------------------------------------------------------------- state
def ensure_state(conn, company_id: str, lock: bool = True) -> dict:
    """The company's conversation state row, created from the lead-engine status on first touch."""
    q = "SELECT * FROM company_conversation_state WHERE company_id = %s" + (" FOR UPDATE" if lock else "")
    row = conn.execute(q, (company_id,)).fetchone()
    if row:
        return row
    c = company(conn, company_id)
    if not c:
        raise KeyError(f"unknown company {company_id}")
    st = OUTREACH_TO_STATUS.get(c["outreach_status"], "discovered")
    contact = conn.execute("SELECT email FROM contacts WHERE company_id = %s ORDER BY discovered_at LIMIT 1",
                           (company_id,)).fetchone()
    dnc = st == "do_not_contact"
    conn.execute(
        "INSERT INTO company_conversation_state (company_id, company_name, contact_email, current_status, do_not_contact, "
        "bounced, cold_sequence_active, high_value, airtable_record_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (company_id) DO NOTHING",
        (company_id, c["company_name"], contact["email"] if contact else None, st, dnc,
         c["outreach_status"] == "bounced", st == "emailed" and c["outreach_status"] != "bounced",
         c["qualification_status"] == "HIGH", None))
    conn.execute("INSERT INTO company_status_transitions (company_id, from_status, to_status, reason) VALUES (%s,NULL,%s,%s)",
                 (company_id, st, f"initialised from lead engine outreach_status={c['outreach_status']}"))
    rec = airtable_record_of(conn, company_id)
    if rec:
        conn.execute("UPDATE company_conversation_state SET airtable_record_id = %s WHERE company_id = %s", (rec, company_id))
    return conn.execute(q, (company_id,)).fetchone()


def set_status(conn, company_id: str, to: str, reason: str, message_id: str | None = None,
               actor: str = "system", owner_override: bool = False, at: datetime | None = None, **fields) -> dict:
    """The only way status changes. Validates the transition and logs it."""
    st = ensure_state(conn, company_id)
    frm = st["current_status"]
    sm.check(frm, to, owner_override=owner_override)
    cold = fields.pop("cold_sequence_active", st["cold_sequence_active"])
    if not sm.cold_sequence_allowed(to):
        cold = False
    dnc = to == "do_not_contact" or (st["do_not_contact"] and not owner_override)
    if fields.get("bounced") or st["bounced"]:
        cold = False
    sets = {"current_status": to, "cold_sequence_active": cold, "do_not_contact": dnc, **fields}
    if frm != to:
        sets["previous_status"] = frm
    if not cold and st["cold_sequence_active"] and "sequence_stop_reason" not in sets:
        sets["sequence_stop_reason"] = reason
    cols = ", ".join(f"{k} = %s" for k in sets)
    conn.execute(f"UPDATE company_conversation_state SET {cols}, version = version + 1, updated_at = now() "
                 f"WHERE company_id = %s", (*sets.values(), company_id))
    if frm != to:
        conn.execute("INSERT INTO company_status_transitions (company_id, from_status, to_status, reason, message_id, actor, at) "
                     "VALUES (%s,%s,%s,%s,%s,%s,coalesce(%s, now()))", (company_id, frm, to, reason, message_id, actor, at))
    oc = STATUS_TO_OUTREACH.get(to) or ("replied" if to not in sm.PRE_REPLY else None)
    if oc:
        conn.execute("UPDATE companies SET outreach_status = %s, updated_at = now() WHERE company_id = %s "
                     "AND outreach_status NOT IN ('do_not_contact','unsubscribed')", (oc, company_id))
    return conn.execute("SELECT * FROM company_conversation_state WHERE company_id = %s", (company_id,)).fetchone()


def update_state(conn, company_id: str, **fields) -> None:
    if not fields:
        return
    ensure_state(conn, company_id)
    cols = ", ".join(f"{k} = %s" for k in fields)
    conn.execute(f"UPDATE company_conversation_state SET {cols}, version = version + 1, updated_at = now() "
                 f"WHERE company_id = %s", (*fields.values(), company_id))


# ------------------------------------------------------------------ messages
def insert_message(conn, m: dict) -> str | None:
    """Append a message. Returns its id, or None when this provider message id is already stored."""
    m = clean(dict(m))           # a NUL byte in one email must never make the database refuse it
    row = conn.execute(
        "INSERT INTO outreach_messages (company_id, direction, kind, sender, recipient, subject, body, occurred_at, provider, "
        "provider_message_id, thread_id, in_reply_to, reference_ids, offer, copy_variant, cta, price_quoted, match_method, "
        "match_confidence, source_ref, meta) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (provider_message_id) WHERE provider_message_id IS NOT NULL DO NOTHING RETURNING message_id",
        (m.get("company_id"), m["direction"], m.get("kind", "other"), m.get("sender", ""), m.get("recipient", ""),
         m.get("subject", ""), m.get("body", ""), m["occurred_at"], m.get("provider", ""), m.get("provider_message_id") or None,
         m.get("thread_id"), m.get("in_reply_to"), list(m.get("reference_ids") or []), m.get("offer"), m.get("copy_variant"),
         m.get("cta"), m.get("price_quoted"), m.get("match_method"), m.get("match_confidence"), m.get("source_ref"),
         _j(m.get("meta") or {}))).fetchone()
    return str(row["message_id"]) if row else None


def message_by_provider_id(conn, provider_message_id: str) -> dict | None:
    return conn.execute("SELECT * FROM outreach_messages WHERE provider_message_id = %s", (provider_message_id,)).fetchone()


def thread(conn, company_id: str, before: datetime | None = None) -> list[dict]:
    q = "SELECT * FROM outreach_messages WHERE company_id = %s"
    args: list = [company_id]
    if before is not None:
        q += " AND occurred_at < %s"
        args.append(before)
    return list(conn.execute(q + " ORDER BY occurred_at, recorded_at", args).fetchall())


def record_confirmed_send(conn, *, company_id: str, recipient: str, sender: str, subject: str, body: str,
                          sent_at: datetime, provider: str, provider_message_id: str, thread_id: str | None = None,
                          kind: str = "cold", offer: str | None = None, copy_variant: str | None = None,
                          cta: str | None = None, price_quoted: str | None = None, source_ref: str | None = None,
                          draft_id: str | None = None, meta: dict | None = None) -> str | None:
    """Store a send the provider CONFIRMED (SMTP accepted / API returned an id).

    Queued, drafted and attempted sends must never reach this function: a
    provider_message_id is required. Returns the new message id, or None if
    this exact message was already recorded (safe to call twice).
    """
    if not provider_message_id:
        raise ValueError("a confirmed send needs the provider's message id")
    if kind not in ("cold", "followup", "reply", "pricing", "audit", "proposal", "manual", "other"):
        raise ValueError(f"unknown send kind {kind}")
    mid = insert_message(conn, dict(
        company_id=company_id, direction="outbound", kind=kind, sender=addr(sender) or sender, recipient=addr(recipient),
        subject=subject, body=body, occurred_at=sent_at, provider=provider, provider_message_id=provider_message_id,
        thread_id=thread_id, offer=offer, copy_variant=copy_variant, cta=cta, price_quoted=price_quoted,
        source_ref=source_ref, meta=meta or {}))
    if mid is None:
        return None
    conn.execute("INSERT INTO outreach_history (company_id, email, campaign, sent_at, status, provider, dedupe_key) "
                 "VALUES (%s,%s,%s,%s,'sent',%s,%s) ON CONFLICT (dedupe_key) DO NOTHING",
                 (company_id, addr(recipient), copy_variant or kind, sent_at, provider, f"msg:{provider_message_id}"))
    conn.execute("UPDATE companies SET first_contacted_at = coalesce(first_contacted_at, %s), "
                 "last_contacted_at = GREATEST(coalesce(last_contacted_at, %s), %s), updated_at = now() WHERE company_id = %s",
                 (sent_at, sent_at, sent_at, company_id))
    st = ensure_state(conn, company_id)
    if st["current_status"] in ("discovered", "ready"):
        set_status(conn, company_id, "emailed", f"confirmed {kind} send", message_id=mid, at=sent_at,
                   cold_sequence_active=kind in ("cold", "followup"))
    extra: dict = {"contact_email": st["contact_email"] or addr(recipient)}
    if offer:
        extra["current_offer"] = offer
    if price_quoted:
        extra["current_price"] = price_quoted
        add_fact(conn, company_id, "price_discussed", f"We quoted {price_quoted}", {"amount_text": price_quoted}, "us", mid)
    if kind in ("reply", "pricing", "audit", "proposal", "manual"):
        extra.update(last_meaningful_message_id=mid, last_meaningful_at=sent_at,
                     last_meaningful_summary=f"We sent a {kind}: {subject}"[:300])
    if kind == "proposal":
        extra["proposal_status"] = "sent"
        cur = ensure_state(conn, company_id)["current_status"]
        if cur in ("proposal_needed", "negotiating", "interested", "qualified", "meeting_requested"):
            set_status(conn, company_id, "proposal_sent", "proposal sent", message_id=mid, at=sent_at)
    update_state(conn, company_id, **extra)
    if draft_id:
        conn.execute("UPDATE outreach_drafts SET state = 'sent', sent_message_id = %s, decided_at = now() "
                     "WHERE draft_id = %s", (mid, draft_id))
    if kind in ("reply", "pricing", "audit", "proposal"):
        # whatever we just sent answers any older pending draft of the same kind
        conn.execute("UPDATE outreach_drafts SET state = 'superseded', decided_at = now() WHERE company_id = %s "
                     "AND kind = %s AND state IN ('awaiting_approval','approved') AND draft_id IS DISTINCT FROM %s",
                     (company_id, kind, draft_id))
    return mid


# ---------------------------------------------------------- facts & queue
def add_fact(conn, company_id: str, fact_type: str, fact_text: str, value: dict | None = None,
             said_by: str = "prospect", source_message_id: str | None = None) -> None:
    fact_text, value = clean(fact_text), clean(value)
    conn.execute("INSERT INTO sales_facts (company_id, fact_type, fact_text, value, said_by, source_message_id) "
                 "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                 (company_id, fact_type, fact_text[:500], _j(value or {}), said_by, source_message_id))


def facts(conn, company_id: str) -> list[dict]:
    return list(conn.execute("SELECT * FROM sales_facts WHERE company_id = %s AND active ORDER BY created_at, fact_id",
                             (company_id,)).fetchall())


def suppress(conn, *, reason: str, email: str | None = None, company_id: str | None = None,
             source_message_id: str | None = None) -> None:
    if email:
        conn.execute("INSERT INTO email_suppressions (email, company_id, reason, source_message_id) VALUES (%s,%s,%s,%s) "
                     "ON CONFLICT (lower(email)) WHERE email IS NOT NULL DO NOTHING",
                     (email.lower(), company_id, reason, source_message_id))
    if company_id:
        conn.execute("INSERT INTO email_suppressions (company_id, reason, source_message_id) VALUES (%s,%s,%s) "
                     "ON CONFLICT (company_id) WHERE email IS NULL AND domain IS NULL DO NOTHING",
                     (company_id, reason, source_message_id))


def queue_attention(conn, *, company_id: str | None, message_id: str | None, reason_code: str, reason: str,
                    urgency: str, status_snapshot: str | None = None, last_reply: str | None = None, summary: str = "",
                    recommended_action: str = "", record_link: str | None = None) -> bool:
    reason, last_reply, summary, recommended_action = clean([reason, last_reply, summary, recommended_action])
    row = conn.execute(
        "INSERT INTO human_attention_queue (company_id, message_id, reason_code, reason_human_needed, urgency, "
        "status_snapshot, last_reply, summary, recommended_action, record_link) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT DO NOTHING RETURNING item_id",
        (company_id, message_id, reason_code, reason, urgency, status_snapshot, last_reply, summary,
         recommended_action, record_link)).fetchone()
    return row is not None


def resolve_attention(conn, company_id: str, resolution: str, reason_codes: Iterable[str] | None = None) -> int:
    q = "UPDATE human_attention_queue SET state = 'resolved', resolution = %s, resolved_at = now() WHERE company_id = %s AND state = 'open'"
    args: list = [resolution, company_id]
    if reason_codes:
        q += " AND reason_code = ANY(%s)"
        args.append(list(reason_codes))
    return conn.execute(q, args).rowcount


def add_draft(conn, *, company_id: str, kind: str, body: str, subject: str = "", to_email: str | None = None,
              based_on_message_id: str | None = None, content: dict | None = None) -> str | None:
    body, subject, to_email, content = clean([body, subject, to_email, content])
    # a newer draft of the same kind replaces older pending ones for this company
    conn.execute("UPDATE outreach_drafts SET state = 'superseded', decided_at = now() WHERE company_id = %s AND kind = %s "
                 "AND state = 'awaiting_approval' AND based_on_message_id IS DISTINCT FROM %s",
                 (company_id, kind, based_on_message_id))
    row = conn.execute(
        "INSERT INTO outreach_drafts (company_id, kind, based_on_message_id, to_email, subject, body, content) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING draft_id",
        (company_id, kind, based_on_message_id, to_email, subject, body, _j(content or {}))).fetchone()
    return str(row["draft_id"]) if row else None


def send_check(conn, email: str, kind: str, company_id: str | None = None) -> dict:
    row = conn.execute("SELECT outreach_send_check(%s, %s, %s) AS r", (email, kind, company_id)).fetchone()
    r = row["r"]
    return r if isinstance(r, dict) else json.loads(r)
