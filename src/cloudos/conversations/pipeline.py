"""process_inbound(): one reply, end to end, in one transaction.

    already stored? -> stop (idempotent)
    match company (strong ids first) -> unsure? store, queue for review, notify, stop
    load full thread + state + remembered facts
    classify in context
    store message + analysis (history is append-only)
    move status through the state machine
    remember facts; suppress on unsubscribe; flag bounces
    prepare drafts (never sent) + attention-queue item when a human is needed
    commit, then notify the owner once
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

from cloudos.conversations import actions, notify, store
from cloudos.conversations import status as sm
from cloudos.conversations.classify import VERSION, Classification, classify
from cloudos.conversations.match import MatchResult, match_inbound
from cloudos.conversations.text import EMAIL_RE, addr, clean, one_line, strip_quoted

MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december"]
HUMAN_URGENCY = {"READY_TO_BUY": "urgent", "MEETING_REQUEST": "high", "PRICE_QUESTION": "high",
                 "NEEDS_REVIEW": "high", "INTERESTED": "normal", "MORE_INFORMATION": "normal", "OBJECTION": "normal",
                 "REFERRAL": "normal", "OTHER": "normal"}
POSITIVE_LABELS = {"INTERESTED", "PRICE_QUESTION", "MORE_INFORMATION", "MEETING_REQUEST", "READY_TO_BUY"}
REVIEW_WHY = {
    "affirmation_ambiguous": "They said yes, but it's not clear to what",
    "affirmation_without_context": "They said yes, but we have no email of ours on record to tie it to",
    "no_recognised_intent": "The reply doesn't clearly say what they want",
    "conflict_negative_and_positive": "The reply says no and sounds interested at the same time",
    "conflict_unsubscribe_and_positive": "They asked to be removed but also sounded interested",
    "legal_or_angry": "The reply is angry or mentions legal/privacy",
    "referral_unclear": "They seem to point to someone else, but it's unclear who",
    "empty_or_unreadable": "The reply has no readable text",
    "delivery_notice_unclear": "A mail-server notice that doesn't clearly say whether the email bounced",
}
BOUNCE_RCPT = [re.compile(p, re.I) for p in (
    r"Final-Recipient:\s*rfc822;\s*([^\s;<>]+@[^\s;<>]+)", r"wasn'?t delivered to\s+<?([^\s<>]+@[^\s<>]+)",
    r"delivery to the following recipient[s]? failed[^\n]*\n\s*<?([^\s<>]+@[^\s<>]+)>?",
    r"Original-Recipient:\s*rfc822;\s*([^\s;<>]+@[^\s;<>]+)", r"<([^\s<>]+@[^\s<>]+)>:\s*host")]


def bounce_recipient(body: str, our: set[str]) -> str | None:
    for p in BOUNCE_RCPT:
        m = p.search(body or "")
        rcpt = m.group(1).lower().strip(".,;:>") if m else ""
        if rcpt and "@" in rcpt and rcpt not in our:
            return rcpt
    return None


def _follow_up_date(text: str, today: date) -> date | None:
    low = text.lower()
    for i, name in enumerate(MONTHS, start=1):
        if re.search(rf"\b{name}\b", low):
            year = today.year + (1 if i <= today.month else 0)
            return date(year, i, 1)
    if re.search(r"next year", low):
        return date(today.year + 1, 1, 15)
    if re.search(r"next quarter|few months|couple (of )?months", low):
        return today + timedelta(days=90)
    if re.search(r"next month", low):
        return today + timedelta(days=30)
    return None


def process_inbound(conn, msg: dict, *, our_addresses: set[str], send: notify.Sender | None = None,
                    notify_owner: bool = True, today: date | None = None) -> dict:
    """msg: provider_message_id, thread_id, in_reply_to, references, sender, recipient, subject, body,
    occurred_at, headers, provider, source_ref"""
    msg = clean(dict(msg))       # NUL / control characters in one email must never make the database refuse it
    pmid = msg.get("provider_message_id")
    if pmid and store.message_by_provider_id(conn, pmid):
        return {"status": "duplicate", "provider_message_id": pmid}
    today = today or datetime.now(timezone.utc).date()
    our = {a.lower() for a in our_addresses}

    # a bounce comes from the mail server; match it on the address that failed
    match_sender = msg.get("sender", "")
    pre = classify({"subject": msg.get("subject"), "body": msg.get("body"), "sender": msg.get("sender"),
                    "headers": msg.get("headers") or {}}, [], None, [], our)
    failed_rcpt = None
    if pre.label == "DELIVERY_FAILURE":
        failed_rcpt = bounce_recipient(msg.get("body", ""), our)
        if failed_rcpt:
            match_sender = failed_rcpt

    m = match_inbound(conn, sender=match_sender, thread_id=msg.get("thread_id"), in_reply_to=msg.get("in_reply_to"),
                      references=msg.get("references"), our_addresses=our)
    if m.confidence == "none" and m.method == "own_address":
        return {"status": "ignored_own_message"}
    if not m.safe:
        return _unmatched(conn, msg, m, pre, send, notify_owner, our=our, failed_rcpt=failed_rcpt)

    cid = m.company_id
    st = store.ensure_state(conn, cid)
    company = store.company(conn, cid)
    history = store.thread(conn, cid)
    remembered = store.facts(conn, cid)
    c = classify({"subject": msg.get("subject"), "body": msg.get("body"), "sender": msg.get("sender"),
                  "headers": msg.get("headers") or {}}, history, st, remembered, our)
    kind = {"AUTO_REPLY": "auto_reply", "DELIVERY_FAILURE": "bounce"}.get(c.label, "inbound")
    mid = store.insert_message(conn, dict(
        company_id=cid, direction="inbound", kind=kind, sender=addr(msg.get("sender", "")),
        recipient=addr(msg.get("recipient", "")), subject=msg.get("subject", ""), body=msg.get("body", ""),
        occurred_at=msg["occurred_at"], provider=msg.get("provider", "gmail"), provider_message_id=pmid,
        thread_id=msg.get("thread_id"), in_reply_to=msg.get("in_reply_to"),
        reference_ids=msg.get("references") if isinstance(msg.get("references"), list) else [],
        match_method=m.method, match_confidence=m.confidence, source_ref=msg.get("source_ref"),
        meta={"failed_recipient": failed_rcpt} if failed_rcpt else {}))
    if mid is None:
        return {"status": "duplicate", "provider_message_id": pmid}
    conn.execute("INSERT INTO reply_analyses (message_id, company_id, classification, confidence, evidence, "
                 "conversation_summary, interpretation, recommended_action, needs_review_reason, classifier_version) "
                 "VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s)",
                 (mid, cid, c.label, c.confidence, store._j(c.evidence), c.summary, c.interpretation,
                  c.recommended_action, c.needs_review_reason, VERSION))

    prev = st["current_status"]
    human = c.label not in ("AUTO_REPLY", "DELIVERY_FAILURE")
    fields: dict = {"last_reply_classification": c.label}
    if human:
        fields.update(last_reply_at=msg["occurred_at"], last_meaningful_message_id=mid,
                      last_meaningful_at=msg["occurred_at"], last_meaningful_summary=one_line(c.new_text, 300))
        qs = [f.fact_text for f in c.facts if f.fact_type == "question"]
        fields["outstanding_question"] = qs[-1] if qs else None
    code, label_text = actions.next_action(c.label)
    fields["next_action"] = label_text
    fields["next_action_due"] = msg["occurred_at"] + timedelta(hours=4 if c.label in HUMAN_URGENCY else 72) \
        if isinstance(msg["occurred_at"], datetime) and c.label in HUMAN_URGENCY else None
    if c.label == "NOT_NOW":
        fu = _follow_up_date(c.new_text, today)
        fields["follow_up_on"] = fu
        fields["next_action_due"] = datetime(fu.year, fu.month, fu.day, 14, tzinfo=timezone.utc) if fu else None
    if c.label == "READY_TO_BUY":
        fields["proposal_status"] = "accepted" if st["proposal_status"] == "sent" else "needed"
    if c.label == "DELIVERY_FAILURE":
        fields["bounced"] = True

    new = sm.target_for(c.label, prev, st["proposal_status"])
    if new == prev and c.label == "DELIVERY_FAILURE" and st["cold_sequence_active"]:
        fields["cold_sequence_active"] = False
        fields["sequence_stop_reason"] = "delivery failure"
    if human and prev in sm.PRE_REPLY and new in sm.PRE_REPLY:
        new = "replied"            # any human reply ends the generic sequence
    store.set_status(conn, cid, new, f"reply classified {c.label}", message_id=mid, at=msg["occurred_at"], **fields)
    if c.label == "DELIVERY_FAILURE":
        conn.execute("UPDATE companies SET outreach_status = 'bounced', updated_at = now() WHERE company_id = %s "
                     "AND outreach_status IN ('contacted','handed_off','outreach_ready')", (cid,))

    for f in c.facts:
        store.add_fact(conn, cid, f.fact_type, f.fact_text, f.value, "prospect", mid)
    if c.referral:
        for e in c.referral.get("emails") or []:
            conn.execute("INSERT INTO contacts (company_id, email, email_status, role, source) VALUES (%s,%s,'unknown',"
                         "'referred','reply_referral') ON CONFLICT DO NOTHING", (cid, e))

    if c.label == "UNSUBSCRIBE":
        store.suppress(conn, reason="unsubscribe" if not c.legal_or_angry else "legal", email=addr(msg.get("sender", "")),
                       company_id=cid, source_message_id=mid)
        conn.execute("UPDATE outreach_drafts SET state = 'discarded', decided_at = now() WHERE company_id = %s "
                     "AND state IN ('awaiting_approval','approved')", (cid,))
    if c.label == "DELIVERY_FAILURE" and failed_rcpt:
        store.suppress(conn, reason="hard_bounce", email=failed_rcpt, company_id=None, source_message_id=mid)

    drafts = _drafts(conn, c, company, cid, mid, msg)
    link = notify.record_link(store.airtable_record_of(conn, cid))
    cur = store.ensure_state(conn, cid, lock=False)
    attention = _attention(conn, c, cur, company, cid, mid, prev, link)
    conn.commit()

    sent = None
    if notify_owner and _should_notify(c, cur, prev):
        sent = notify.notify_once(
            conn, dedupe_key=f"br:reply:{mid}", code=f"reply.{c.label.lower()}",
            severity="urgent" if c.label == "READY_TO_BUY" or c.legal_or_angry else "high",
            company_id=cid, meta={"message_id": mid, "classification": c.label}, send=send,
            text=_card(c, cur, company, msg, prev, link, drafts))
    return {"status": "processed", "company_id": cid, "message_id": mid, "classification": c.label,
            "confidence": c.confidence, "from_status": prev, "to_status": cur["current_status"], "drafts": drafts,
            "attention": attention, "notification": sent, "match": m.method}


def _drafts(conn, c: Classification, company: dict, cid: str, mid: str, msg: dict) -> list[str]:
    st = store.ensure_state(conn, cid, lock=False)
    if st["do_not_contact"]:
        return []
    facts = store.facts(conn, cid)
    to = addr(msg.get("sender", ""))
    made: list[tuple[str, tuple]] = []
    latest_outbound = conn.execute(
        "SELECT body FROM outreach_messages WHERE company_id=%s AND direction='outbound' ORDER BY occurred_at DESC LIMIT 1",
        (cid,),
    ).fetchone()
    video_request = bool(c.label in ("MORE_INFORMATION", "INTERESTED") and latest_outbound
                         and re.search(r"\b(video|breakdown|walkthrough)\b", latest_outbound["body"] or "", re.I))
    if video_request:
        made.append(("reply", actions.video_request_reply(company)))
    elif c.label == "PRICE_QUESTION":
        made.append(("pricing", actions.pricing_reply(company, st, facts, c.new_text)))
    elif c.label == "INTERESTED":
        made.append(("reply", actions.personal_reply(company, st, facts, c.label, c.new_text)))
        made.append(("audit", actions.audit(company, st, facts)))
    elif c.label in ("MORE_INFORMATION", "OBJECTION"):
        made.append(("reply" if c.label == "MORE_INFORMATION" else "objection",
                     actions.personal_reply(company, st, facts, c.label, c.new_text)))
    elif c.label == "READY_TO_BUY":
        made.append(("proposal", actions.proposal(company, st, facts)))
    elif c.label == "REFERRAL" and c.referral and c.referral.get("emails"):
        made.append(("referral_intro", actions.referral_intro(company, c.referral, to)))
    ids = []
    for kind, (subject, body, content) in made:
        target = (c.referral["emails"][0] if kind == "referral_intro" else to)
        d = store.add_draft(conn, company_id=cid, kind=kind, body=body, subject=subject, to_email=target,
                            based_on_message_id=mid, content={**content, "in_reply_to": msg.get("provider_message_id"),
                                                              "thread_id": msg.get("thread_id")})
        if d:
            ids.append(d)
    if c.label == "READY_TO_BUY" and ids:
        store.update_state(conn, cid, proposal_status="drafted" if st["proposal_status"] != "accepted" else "accepted")
    return ids


def _attention(conn, c, st, company, cid, mid, prev, link) -> bool:
    high_value = bool(st["high_value"])
    human_needed = c.label in HUMAN_URGENCY or c.legal_or_angry or (high_value and c.label not in ("AUTO_REPLY",))
    if not human_needed or c.label in ("NOT_NOW", "NOT_INTERESTED", "WRONG_PERSON") and not high_value and not c.legal_or_angry:
        return False
    if c.label == "UNSUBSCRIBE" and not c.legal_or_angry:
        return False
    if prev == "do_not_contact" and c.label not in POSITIVE_LABELS and not c.legal_or_angry:
        return False     # already blocked and not asking to reopen - nothing for the owner to do
    reason = {
        "NEEDS_REVIEW": f"{REVIEW_WHY.get(c.needs_review_reason, 'The meaning is unclear')} - nothing will be sent automatically.",
        "MEETING_REQUEST": "They asked for a call/meeting - only you can set the time.",
        "READY_TO_BUY": "They want to buy - approve the proposal and collect payment.",
        "PRICE_QUESTION": "Pricing reply is prepared and needs your approval before it goes out.",
        "INTERESTED": "Personal reply + audit prepared; needs your approval.",
        "MORE_INFORMATION": "Answer prepared; check it answers their exact question.",
        "OBJECTION": "They raised a concern - negotiation needs you.",
        "REFERRAL": "They referred you to someone else - approve the intro.",
    }.get(c.label, "High-value prospect replied.")
    if c.legal_or_angry:
        reason = "Angry or legal/privacy wording - handle personally. Sending is blocked."
    code = "legal_or_angry" if c.legal_or_angry else (c.needs_review_reason or c.label.lower())
    recommended = c.recommended_action
    if c.label in ("MORE_INFORMATION", "INTERESTED"):
        outbound = conn.execute(
            "SELECT subject,body FROM outreach_messages WHERE company_id=%s AND direction='outbound' "
            "ORDER BY occurred_at DESC LIMIT 1", (cid,),
        ).fetchone()
        if outbound and re.search(r"\b(video|breakdown|walkthrough)\b", outbound["body"] or "", re.I):
            p = company.get("personalization") or {}
            if isinstance(p, str):
                try:
                    import json
                    p = json.loads(p)
                except ValueError:
                    p = {}
            campaign = p.get("review_campaign") or {}
            code = "video_request"
            reason = "They asked for the personalized video - create it now; do not claim it exists until it is produced."
            recommended = "\n".join(filter(None, [
                "Create a 3-5 minute personalized video:",
                f"Company: {company.get('company_name', '')}",
                f"Contact: {company.get('contact_email') or ''}",
                f"Google profile: {campaign.get('source') or p.get('google_profile_url') or ''}",
                f"Original evidence: {campaign.get('exact_evidence') or ''}",
                f"Competitor evidence: {campaign.get('competitor_context') or ''}",
                f"Original email: {one_line(outbound['body'], 500)}",
                f"Their reply: {one_line(c.new_text, 400)}",
                "Talking points: show the exact review/Maps issue (0:00-0:30); explain the competitive/customer "
                "impact without promising rankings (0:30-1:30); show the review-request, monitoring/response, and "
                "local visibility changes (1:30-3:00); end with $299/month and one simple next step.",
            ]))
    return store.queue_attention(
        conn, company_id=cid, message_id=mid, reason_code=code, reason=reason,
        urgency="urgent" if c.legal_or_angry or c.label == "READY_TO_BUY" else HUMAN_URGENCY.get(c.label, "high" if high_value else "normal"),
        status_snapshot=st["current_status"], last_reply=one_line(c.new_text, 400), summary=c.summary,
        recommended_action=recommended, record_link=link)


def _should_notify(c: Classification, st: dict, prev: str) -> bool:
    if c.legal_or_angry:
        return True
    if c.label in notify.NOTIFY_LABELS:
        return True
    if st["high_value"] and c.label not in ("AUTO_REPLY",):
        return True    # high-value prospect replied (even a no), or its email bounced
    return False


def _card(c: Classification, st: dict, company: dict, msg: dict, prev: str, link: str | None, drafts: list[str]) -> str:
    offer = st.get("current_offer") or actions.offer()["name"]
    price = st.get("current_price")
    if c.label == "PRICE_QUESTION" and not price:
        price = f"{actions.offer()['price_text']} (from the offer sheet; not yet sent)"
    title = {"NEEDS_REVIEW": "BRIGHTREACH - NEEDS YOU", "READY_TO_BUY": "BRIGHTREACH - READY TO BUY",
             "DELIVERY_FAILURE": "BRIGHTREACH - EMAIL BOUNCED"}.get(c.label, "BRIGHTREACH REPLY")
    if st.get("proposal_status") == "accepted":
        title = "BRIGHTREACH - PROPOSAL ACCEPTED"
    needed = actions.ACTION_NEEDED.get(c.label, "Review")
    if c.legal_or_angry:
        needed = "Reply manually (sending to them is blocked)"
    if st.get("high_value") and needed == "None":
        needed = "None (high-value prospect - FYI)"
    if drafts:
        needed += f" - {len(drafts)} draft(s) ready"
    return notify.reply_card(
        company=company["company_name"], contact=addr(msg.get("sender", "")), prev_status=prev,
        new_status=st["current_status"], label=c.label, said=c.new_text or msg.get("subject", ""), context=c.summary,
        interpretation=c.interpretation + (f" Evidence: {'; '.join(c.evidence[:2])}" if c.evidence else ""),
        next_step=c.recommended_action, offer=offer, price=price, action_needed=needed, link=link, title=title)


def _unmatched(conn, msg: dict, m: MatchResult, pre: Classification, send, notify_owner: bool,
               our: set[str] | None = None, failed_rcpt: str | None = None) -> dict:
    """Couldn't tie this to one company with confidence: keep it, flag it, never act on its sales meaning.

    Opt-outs and dead addresses are the exception: those are acted on even without a company, because
    the cost of emailing someone who said stop is far higher than the cost of blocking an address."""
    mid = store.insert_message(conn, dict(
        company_id=None, direction="inbound", kind={"AUTO_REPLY": "auto_reply", "DELIVERY_FAILURE": "bounce"}.get(pre.label, "inbound"),
        sender=addr(msg.get("sender", "")), recipient=addr(msg.get("recipient", "")), subject=msg.get("subject", ""),
        body=msg.get("body", ""), occurred_at=msg["occurred_at"], provider=msg.get("provider", "gmail"),
        provider_message_id=msg.get("provider_message_id"), thread_id=msg.get("thread_id"),
        in_reply_to=msg.get("in_reply_to"), match_method=m.method, match_confidence=m.confidence,
        source_ref=msg.get("source_ref"), meta={"candidates": m.candidates, "match_reason": m.reason}))
    if mid is None:
        return {"status": "duplicate"}
    if pre.label in ("AUTO_REPLY",):
        conn.commit()
        return {"status": "unmatched_auto_reply", "message_id": mid}
    suppressed = _suppress_unmatched(conn, msg, m, pre, mid, our or set(), failed_rcpt)
    names = [r["company_name"] for r in conn.execute("SELECT company_name FROM companies WHERE company_id = ANY(%s::uuid[])",
                                                     (m.candidates,)).fetchall()] if m.candidates else []
    blocked = {"address": " Their address is blocked from all email.",
               "company": " Their address and the company whose website matches it are blocked from all email.",
               "bounce": " The address that bounced is blocked."}.get(suppressed, "")
    store.queue_attention(conn, company_id=None, message_id=mid, reason_code="match_uncertain",
                          reason=f"Can't tell which company this is from: {m.reason}.{blocked}", urgency="high",
                          last_reply=one_line(strip_quoted(msg.get("body", "")), 400),
                          summary="Possible companies: " + (", ".join(names) or "none"),
                          recommended_action="Tell me which company it belongs to; nothing was updated or sent.")
    conn.commit()
    out = {"status": "needs_review_unmatched", "message_id": mid, "match": m.method, "confidence": m.confidence,
           "suppressed": suppressed}
    if notify_owner:
        text = notify.reply_card(
            company="UNKNOWN - " + (", ".join(names) if names else "no match"), contact=addr(msg.get("sender", "")),
            prev_status="?", new_status="needs_review", label=pre.label, said=strip_quoted(msg.get("body", "")) or msg.get("subject", ""),
            context=f"Subject: {msg.get('subject', '')}", interpretation=f"Not matched safely ({m.reason}).",
            next_step="Decide which company this belongs to. No status was changed and nothing was sent." + blocked,
            offer=None, price=None, action_needed="Review", link=None, title="BRIGHTREACH - UNMATCHED REPLY")
        out["notification"] = notify.notify_once(conn, dedupe_key=f"br:unmatched:{mid}", code="reply.unmatched",
                                                 severity="high", text=text, meta={"message_id": mid}, send=send)
    return out


def _suppress_unmatched(conn, msg: dict, m: MatchResult, pre: Classification, mid: str, our: set[str],
                        failed_rcpt: str | None) -> str | None:
    """Opt-out from a sender we couldn't tie to one company for certain: block the address itself, and when
    only their website domain matched exactly one company (someone else at that business), block the company
    too and drop its pending drafts. A permanent bounce blocks the dead address."""
    if pre.label == "DELIVERY_FAILURE" and failed_rcpt:
        store.suppress(conn, reason="hard_bounce", email=failed_rcpt, source_message_id=mid)
        return "bounce"
    if pre.label != "UNSUBSCRIBE":
        return None
    sender = addr(msg.get("sender", ""))
    if not sender or sender in our:
        return None
    reason = "legal" if pre.legal_or_angry else "unsubscribe"
    store.suppress(conn, reason=reason, email=sender, source_message_id=mid)
    if m.method == "domain" and m.confidence == "medium" and len(m.candidates) == 1:
        cid = m.candidates[0]
        store.suppress(conn, reason=reason, company_id=cid, source_message_id=mid)
        conn.execute("UPDATE outreach_drafts SET state = 'discarded', decided_at = now() WHERE company_id = %s "
                     "AND state IN ('awaiting_approval','approved')", (cid,))
        return "company"
    return "address"
