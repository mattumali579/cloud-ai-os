"""Tie an inbound email to exactly one company, strongest evidence first.

    1. provider thread id (Gmail X-GM-THRID) of a message we sent
    2. In-Reply-To / References pointing at a Message-ID we sent
    3. the sender is exactly an address we emailed
    4. the sender is a known contact of one company
    5. business domain of the sender matches one company        (medium -> review)

A match is only 'high' when one company is identified and no stronger or
equal signal points somewhere else. Anything else is flagged for review and
never auto-acted on. No fuzzy name matching.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from cloudos.conversations.text import addr, business_domain, msgid, msgids


@dataclass
class MatchResult:
    company_id: str | None
    method: str
    confidence: str                   # high | medium | low | none
    reason: str = ""
    candidates: list[str] = field(default_factory=list)
    parent_message_id: str | None = None

    @property
    def safe(self) -> bool:
        return self.confidence == "high" and self.company_id is not None


def _distinct(rows) -> list[str]:
    seen: list[str] = []
    for r in rows:
        cid = str(r["company_id"]) if r["company_id"] else None
        if cid and cid not in seen:
            seen.append(cid)
    return seen


def match_inbound(conn, *, sender: str, thread_id: str | None, in_reply_to: str | None,
                  references: str | list[str] | None, our_addresses: set[str]) -> MatchResult:
    s = addr(sender)
    refs = references if isinstance(references, list) else msgids(references or "")
    ids = [i for i in [msgid(in_reply_to)] + refs if i]

    by_thread: list[str] = []
    if thread_id:
        by_thread = _distinct(conn.execute(
            "SELECT DISTINCT company_id FROM outreach_messages WHERE thread_id = %s AND direction = 'outbound' "
            "AND company_id IS NOT NULL", (thread_id,)).fetchall())
    parent = None
    by_ref: list[str] = []
    if ids:
        rows = conn.execute("SELECT message_id, company_id, provider_message_id FROM outreach_messages "
                            "WHERE provider_message_id = ANY(%s) AND company_id IS NOT NULL", (ids,)).fetchall()
        by_ref = _distinct(rows)
        if rows:
            order = {i: n for n, i in enumerate(ids)}
            parent = str(min(rows, key=lambda r: order.get(r["provider_message_id"], 99))["message_id"])
    by_recipient = _distinct(conn.execute(
        "SELECT DISTINCT company_id FROM outreach_messages WHERE direction = 'outbound' AND lower(recipient) = %s "
        "AND company_id IS NOT NULL", (s,)).fetchall()) if s else []
    by_contact = _distinct(conn.execute(
        "SELECT company_id FROM contacts WHERE lower(email) = %s", (s,)).fetchall()) if s else []
    dom = business_domain(s)
    by_domain = _distinct(conn.execute(
        "SELECT company_id FROM companies WHERE normalized_domain = %s", (dom,)).fetchall()) if dom else []

    if s in our_addresses:
        return MatchResult(None, "own_address", "none", "message is from our own mailbox")

    for method, found in (("thread_id", by_thread), ("in_reply_to", by_ref)):
        if len(found) > 1:
            return MatchResult(None, method, "low", f"{method} points at {len(found)} different companies", found, parent)
        if len(found) == 1:
            cid = found[0]
            # a different company claiming this exact sender is a conflict, not a detail
            other = [c for c in by_recipient + by_contact if c != cid]
            if other and cid not in by_recipient + by_contact:
                return MatchResult(None, method, "low",
                                   "thread belongs to one company but the sender is on record for another",
                                   [cid, *other], parent)
            return MatchResult(cid, method, "high", "reply in a thread we started", [cid], parent)

    for method, found in (("exact_recipient", by_recipient), ("contact_email", by_contact)):
        if len(found) > 1:
            return MatchResult(None, method, "low", f"sender address is on record for {len(found)} companies", found)
        if len(found) == 1:
            return MatchResult(found[0], method, "high", "sender is exactly an address on record", found)

    if len(by_domain) == 1:
        return MatchResult(by_domain[0], "domain", "medium",
                           "only the sender's website domain matches; could be a different person there", by_domain)
    if len(by_domain) > 1:
        return MatchResult(None, "domain", "low", "domain matches several companies", by_domain)
    return MatchResult(None, "none", "none", "no identifier matches any company we contacted")
