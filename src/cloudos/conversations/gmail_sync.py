"""Read the outreach Gmail mailbox (All Mail, read-only) and feed the memory.

- Outbound mail we sent (X-Leadgen-Outreach header, or a reply to a company we
  contacted) is stored as a confirmed send with its exact body, Message-ID and
  Gmail thread id. Anything in Sent Mail was accepted by Gmail: that is the
  provider confirmation.
- Inbound mail is only looked at when it belongs to a conversation we started
  (thread, In-Reply-To/References, or the sender is an address/domain we
  emailed). Everything else in the mailbox - personal mail - is never stored.
- Prepared drafts are placed in Gmail Drafts (threaded) for the owner to review
  and send. This module itself never sends email.

Incremental by IMAP UID with UIDVALIDITY, so each message is handled once.
"""
from __future__ import annotations

import email
import email.policy
import imaplib
import os
import re
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid, parsedate_to_datetime

from cloudos.conversations import guard, notify, store
from cloudos.conversations.classify import PRICE_RE
from cloudos.conversations.pipeline import process_inbound
from cloudos.conversations.text import addr, addrs, business_domain, msgid, msgids, strip_quoted

ALL_MAIL = '"[Gmail]/All Mail"'
DRAFTS = '"[Gmail]/Drafts"'
HEADER_FIELDS = ("FROM TO CC SUBJECT DATE MESSAGE-ID IN-REPLY-TO REFERENCES X-LEADGEN-OUTREACH X-LEADGEN-RECORD-ID "
                 "X-LEADGEN-MACHINE AUTO-SUBMITTED X-AUTOREPLY X-AUTORESPOND PRECEDENCE CONTENT-TYPE")
OUTREACH_KIND = {"office-agent-first": "cold", "revenue-recovery-first": "cold", "office-agent-followup": "followup",
                 "email-close-auto": "pricing"}
# Gmail labels the lead pipelines put on outreach they draft for the owner to send by hand.
# Those hand-sent emails carry no X-Leadgen header, so the label is the only marker.
OUTREACH_LABELS = tuple(l.strip() for l in os.environ.get("OUTREACH_GMAIL_LABELS", "Leads,LG/Processed").split(",") if l.strip())
BATCH = 100


def _has_outreach_label(labels: str) -> bool:
    found = re.findall(r'"([^"]*)"|(\S+)', labels or "")
    names = {(a or b).replace("\\\\", "\\") for a, b in found}
    return any(l in names for l in OUTREACH_LABELS)
BODY_LIMIT = 20000


def credentials() -> tuple[str, str]:
    user = os.environ.get("EMAIL_ADDRESS") or os.environ.get("SMTP_USER") or ""
    pw = os.environ.get("EMAIL_APP_PASSWORD") or os.environ.get("SMTP_PASS") or ""
    return user.strip(), pw.strip().replace(" ", "")


def our_addresses() -> set[str]:
    vals = [os.environ.get(k, "") for k in ("EMAIL_ADDRESS", "SMTP_USER", "SMTP_FROM_EMAIL")]
    vals += (os.environ.get("EMAIL_OWNER_ALIASES", "") or "").split(",")
    return {addr(v) or v.strip().lower() for v in vals if v and v.strip()}


def _text(msg) -> str:
    plain, html = "", ""
    for part in (msg.walk() if msg.is_multipart() else [msg]):
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html") or part.get_content_disposition() == "attachment":
            continue
        try:
            chunk = part.get_content()
        except Exception:
            chunk = (part.get_payload(decode=True) or b"").decode("utf-8", "replace")
        if ctype == "text/plain" and not plain:
            plain = chunk
        elif ctype == "text/html" and not html:
            html = chunk
    if not plain and html:
        html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
        html = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", html)
        html = re.sub(r"(?i)<blockquote.*", "", html, flags=re.S)       # quoted history in HTML mail
        plain = re.sub(r"<[^>]+>", " ", html)
        plain = re.sub(r"&nbsp;?", " ", plain).replace("&amp;", "&").replace("&#39;", "'").replace("&quot;", '"')
    return re.sub(r"[ \t]+", " ", plain).strip()[:BODY_LIMIT]


def _date(msg) -> datetime:
    try:
        d = parsedate_to_datetime(str(msg.get("Date")))
        return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


class Known:
    """What we already know, so relevance can be decided from headers alone."""

    def __init__(self, conn):
        rows = conn.execute("SELECT provider_message_id, thread_id, lower(recipient) r FROM outreach_messages "
                            "WHERE direction = 'outbound'").fetchall()
        self.msgids = {r["provider_message_id"] for r in rows if r["provider_message_id"]}
        self.threads = {r["thread_id"] for r in rows if r["thread_id"]}
        contacted = conn.execute(
            "SELECT DISTINCT lower(email) e FROM outreach_history WHERE status = 'sent' AND email IS NOT NULL "
            "UNION SELECT lower(ct.email) FROM contacts ct JOIN companies c USING (company_id) "
            "WHERE c.first_contacted_at IS NOT NULL OR c.outreach_status IN ('contacted','replied','bounced','unsubscribed','do_not_contact')"
        ).fetchall()
        self.addresses = {r["e"] for r in contacted} | {r["r"] for r in rows if r["r"]}
        self.domains = {business_domain(a) for a in self.addresses} - {""}

    def add_outbound(self, pmid: str, thread: str | None, rcpt: str) -> None:
        self.msgids.add(pmid)
        if thread:
            self.threads.add(thread)
        self.addresses.add(rcpt)
        if business_domain(rcpt):
            self.domains.add(business_domain(rcpt))


def _parse_fetch(data) -> list[dict]:
    out = []
    for part in data:
        if not isinstance(part, tuple):
            continue
        meta = part[0].decode("ascii", "replace")
        uid = re.search(r"UID (\d+)", meta)
        thr = re.search(r"X-GM-THRID (\d+)", meta)
        labels = re.search(r"X-GM-LABELS \((.*?)\)", meta)
        out.append({"uid": int(uid.group(1)) if uid else None, "thread": thr.group(1) if thr else None,
                    "labels": labels.group(1) if labels else "", "raw": part[1]})
    return out


def sync(conn, *, since: str = "01-Sep-2026", send: notify.Sender | None = None, push_drafts: bool = True,
         repair_emailed_at: bool = True, imap_factory=imaplib.IMAP4_SSL, host: str = "imap.gmail.com",
         max_messages: int = 3000, rescan: bool = False) -> dict:
    user, pw = credentials()
    if not (user and pw):
        raise RuntimeError("mailbox credentials missing (EMAIL_ADDRESS/EMAIL_APP_PASSWORD or SMTP_USER/SMTP_PASS)")
    ours = our_addresses() | {user.lower()}
    key = f"{user.lower()}:{ALL_MAIL}"
    started = datetime.now(timezone.utc)
    res = {"scanned": 0, "relevant": 0, "outbound_recorded": 0, "outbound_unresolved": 0, "inbound": {},
           "notifications": 0, "drafts_pushed": 0, "emailed_at_repaired": 0, "first_run": False}
    imap = imap_factory(host, 993)
    try:
        imap.login(user, pw)
        typ, _ = imap.select(ALL_MAIL, readonly=True)
        if typ != "OK":
            raise RuntimeError("cannot open All Mail")
        uidvalidity = int(re.search(rb"UIDVALIDITY (\d+)", imap.status(ALL_MAIL, "(UIDVALIDITY)")[1][0]).group(1))
        st = conn.execute("SELECT * FROM reply_poll_state WHERE mailbox = %s", (key,)).fetchone()
        if st and st["uidvalidity"] == uidvalidity and not rescan:
            typ, data = imap.uid("SEARCH", None, f"UID {st['last_uid'] + 1}:*")
        else:
            res["first_run"] = True
            typ, data = imap.uid("SEARCH", None, "SINCE", since)
        floor = st["last_uid"] if (st and st["uidvalidity"] == uidvalidity and not rescan) else 0
        uids = sorted(int(u) for u in (data[0] or b"").split() if int(u) > floor)
        uids = uids[:max_messages]
        res["scanned"] = len(uids)
        known = Known(conn)
        # replies older than 36h found on a first run / re-read go into one digest, not one ping each
        quiet_before = started - timedelta(hours=36) if (res["first_run"] or rescan) else None
        digest: list[str] = []
        repair: list[tuple[str, datetime]] = []
        last_uid = st["last_uid"] if st and st["uidvalidity"] == uidvalidity else 0
        for i in range(0, len(uids), BATCH):
            chunk = uids[i:i + BATCH]
            typ, data = imap.uid("FETCH", ",".join(map(str, chunk)),
                                 f"(UID X-GM-THRID X-GM-LABELS BODY.PEEK[HEADER.FIELDS ({HEADER_FIELDS})])")
            heads = _parse_fetch(data)
            wanted = []
            for h in heads:
                m = email.message_from_bytes(h["raw"], policy=email.policy.default)
                if "\\Draft" in h["labels"]:
                    continue
                frm = addr(str(m.get("From", "")))
                to = addrs(", ".join(str(m.get(k, "")) for k in ("To", "Cc")))
                refs = [msgid(m.get("In-Reply-To"))] + msgids(str(m.get("References", "")))
                if frm in ours:
                    rel = bool(m.get("X-Leadgen-Outreach")) or _has_outreach_label(h["labels"]) or any(
                        t in known.addresses or business_domain(t) in known.domains for t in to if t not in ours)
                    if str(m.get("X-Leadgen-Outreach", "")).strip().lower() == "ai-reply-draft":
                        rel = False
                    if rel:   # replies later in this same batch must already see it
                        for t in [t for t in to if t not in ours][:1]:
                            known.add_outbound(msgid(m.get("Message-ID")), h["thread"], t)
                else:
                    rel = (h["thread"] in known.threads or any(r and r in known.msgids for r in refs)
                           or frm in known.addresses or business_domain(frm) in known.domains
                           or bool(re.match(r"(mailer-daemon|postmaster)@", frm)) and h["thread"] in known.threads)
                if rel:
                    wanted.append(h)
            if wanted:
                typ, data = imap.uid("FETCH", ",".join(str(h["uid"]) for h in wanted), "(UID X-GM-THRID X-GM-LABELS BODY.PEEK[])")
                for full in _parse_fetch(data):
                    res["relevant"] += 1
                    m = email.message_from_bytes(full["raw"], policy=email.policy.default)
                    frm = addr(str(m.get("From", "")))
                    if frm in ours:
                        r = _outbound(conn, m, full, ours, known)
                        if r is None:
                            res["outbound_unresolved"] += 1
                        elif r:
                            res["outbound_recorded"] += 1
                            if r[1] and repair_emailed_at:
                                repair.append(r[1])
                    else:
                        when = _date(m)
                        body = _text(m)
                        quiet = bool(quiet_before and when < quiet_before)
                        out = process_inbound(conn, {
                            "provider_message_id": msgid(m.get("Message-ID")) or f"gmail-uid-{full['uid']}@{user}",
                            "thread_id": full["thread"], "in_reply_to": str(m.get("In-Reply-To") or ""),
                            "references": msgids(str(m.get("References", ""))), "sender": str(m.get("From", "")),
                            "recipient": str(m.get("To", "")), "subject": str(m.get("Subject", "")), "body": body,
                            "occurred_at": when, "provider": "gmail",
                            "headers": {k: str(m.get(k)) for k in ("Auto-Submitted", "X-Autoreply", "X-Autorespond", "Precedence") if m.get(k)},
                            "source_ref": f"imap:{ALL_MAIL}:{full['uid']}"}, our_addresses=ours, send=send,
                            notify_owner=not quiet)
                        label = out.get("classification") or out["status"]
                        res["inbound"][label] = res["inbound"].get(label, 0) + 1
                        if (out.get("notification") or {}).get("created"):
                            res["notifications"] += 1
                        if quiet and out.get("status") in ("processed", "needs_review_unmatched") and label not in ("AUTO_REPLY", "DELIVERY_FAILURE"):
                            digest.append(label)
            last_uid = max([last_uid, *chunk])
            conn.execute("INSERT INTO reply_poll_state (mailbox, last_uid, uidvalidity, last_run_at, last_result) "
                         "VALUES (%s,%s,%s,now(),%s) ON CONFLICT (mailbox) DO UPDATE SET last_uid = EXCLUDED.last_uid, "
                         "uidvalidity = EXCLUDED.uidvalidity, last_run_at = now(), last_result = EXCLUDED.last_result",
                         (key, last_uid, uidvalidity, store._j(res)))
            conn.commit()
        if digest:
            _digest(conn, digest, send, res)
    finally:
        try:
            imap.logout()
        except Exception:
            pass

    if repair:
        res["emailed_at_repaired"] = _repair_emailed_at(conn, repair, send)
    if push_drafts:
        res["drafts_pushed"] = push_pending_drafts(conn, imap_factory=imap_factory, host=host)
    res["retried_notifications"] = notify.flush(conn, send=send)
    conn.execute("UPDATE reply_poll_state SET last_run_at = now(), last_result = %s WHERE mailbox = %s", (store._j(res), key))
    conn.commit()
    return res


def _resolve_company(conn, m, rcpt: str) -> str | None:
    rec = str(m.get("X-Leadgen-Record-ID") or "").strip()
    if rec.startswith("rec"):
        cid = store.company_by_airtable_record(conn, rec)
        if cid:
            return cid
    row = conn.execute("SELECT company_id FROM contacts WHERE lower(email) = %s", (rcpt,)).fetchall()
    if len(row) == 1:
        return str(row[0]["company_id"])
    row = conn.execute("SELECT DISTINCT company_id FROM outreach_messages WHERE direction='outbound' AND lower(recipient) = %s "
                       "AND company_id IS NOT NULL", (rcpt,)).fetchall()
    if len(row) == 1:
        return str(row[0]["company_id"])
    row = conn.execute("SELECT DISTINCT company_id FROM outreach_history WHERE lower(email) = %s AND company_id IS NOT NULL",
                       (rcpt,)).fetchall()
    if len(row) == 1:
        return str(row[0]["company_id"])
    dom = business_domain(rcpt)
    if dom:
        row = conn.execute("SELECT company_id FROM companies WHERE normalized_domain = %s", (dom,)).fetchall()
        if len(row) == 1:
            return str(row[0]["company_id"])
    return None


def _create_company_from_outreach(conn, m, rcpt: str) -> str:
    """Outreach went to a company the database never heard of (hand-sent pipeline draft).
    Register it, so its replies are matched and it can never be cold-emailed twice."""
    from cloudos.leadgen.normalize import normalize_name
    subject = str(m.get("Subject") or "")
    sm_ = re.search(r"\b(?:at|about|for)\s+(.+?)\s*$", re.sub(r"^(re|fwd?):\s*", "", subject, flags=re.I))
    display = email.utils.parseaddr(str(m.get("To") or ""))[0]
    dom = business_domain(rcpt) or None
    name = (sm_.group(1) if sm_ else "") or display or dom or rcpt
    row = conn.execute(
        "INSERT INTO companies (company_name, normalized_name, domain, normalized_domain, discovery_source, "
        "qualification_status, qualification_reason, outreach_status) VALUES (%s,%s,%s,%s,'gmail_outreach','PENDING',"
        "'registered from a sent outreach email not in the lead database','contacted') "
        "ON CONFLICT (normalized_domain) WHERE normalized_domain IS NOT NULL DO UPDATE SET updated_at = now() "
        "RETURNING company_id", (name[:200], normalize_name(name) or name.lower(), dom, dom)).fetchone()
    cid = str(row["company_id"])
    conn.execute("INSERT INTO contacts (company_id, email, email_status, source) VALUES (%s,%s,'unknown','gmail_outreach') "
                 "ON CONFLICT DO NOTHING", (cid, rcpt))
    return cid


def _outbound(conn, m, full: dict, ours: set[str], known: Known):
    """Record one of our sent messages. Returns (message_id, repair_item) / False (already known) / None (no company)."""
    to = [t for t in addrs(", ".join(str(m.get(k, "")) for k in ("To", "Cc"))) if t not in ours]
    if not to:
        return False
    rcpt = to[0]
    pmid = msgid(m.get("Message-ID"))
    if not pmid:
        return False
    if store.message_by_provider_id(conn, pmid):
        known.add_outbound(pmid, full["thread"], rcpt)
        return False
    cid = _resolve_company(conn, m, rcpt)
    if not cid and (m.get("X-Leadgen-Outreach") or _has_outreach_label(full.get("labels", ""))):
        cid = _create_company_from_outreach(conn, m, rcpt)
    if not cid:
        return None
    body = _text(m)
    header = str(m.get("X-Leadgen-Outreach") or "").strip().lower()
    kind = OUTREACH_KIND.get(header)
    draft_id = None
    if not kind:
        d = conn.execute("SELECT draft_id, kind FROM outreach_drafts WHERE company_id = %s AND lower(to_email) = %s "
                         "AND state IN ('awaiting_approval','approved') ORDER BY created_at DESC LIMIT 1", (cid, rcpt)).fetchone()
        if d:
            draft_id, kind = str(d["draft_id"]), {"objection": "reply", "referral_intro": "reply"}.get(d["kind"], d["kind"])
        else:
            kind = "manual"
    mine = strip_quoted(body)
    prices = PRICE_RE.findall(mine)
    questions = re.findall(r"[^.?!\n]*\?", mine)
    sent_at = _date(m)
    mid = store.record_confirmed_send(
        conn, company_id=cid, recipient=rcpt, sender=str(m.get("From", "")), subject=str(m.get("Subject", "")), body=body,
        sent_at=sent_at, provider="gmail", provider_message_id=pmid, thread_id=full["thread"], kind=kind,
        copy_variant=str(m.get("X-Leadgen-Machine") or "") or None, cta=questions[-1].strip() if questions else None,
        price_quoted=", ".join(prices) if prices and kind in ("pricing", "proposal", "manual", "reply") else None,
        source_ref=f"imap:{ALL_MAIL}:{full['uid']}", draft_id=draft_id,
        meta={"x_leadgen_outreach": header or None, "x_leadgen_record_id": str(m.get("X-Leadgen-Record-ID") or "") or None})
    conn.commit()
    known.add_outbound(pmid, full["thread"], rcpt)
    repair = None
    if kind in ("cold", "followup") and sent_at < datetime.now(timezone.utc) - timedelta(minutes=30):
        rec = store.airtable_record_of(conn, cid)
        if rec:
            repair = (rec, sent_at)
    return (mid, repair) if mid else False


def _repair_emailed_at(conn, items: list[tuple[str, datetime]], send) -> int:
    """A send Gmail confirmed but Airtable doesn't show would let someone email them twice. Stamp it (never overwrite)."""
    fixed = 0
    first = {}
    for rec, when in items:
        first[rec] = min(first.get(rec, when), when)
    for rec, when in first.items():
        try:
            if guard.airtable_emailed_at(rec, when):
                fixed += 1
        except Exception:
            continue
    return fixed


def _digest(conn, labels: list[str], send, res: dict) -> None:
    counts: dict[str, int] = {}
    for l in labels:
        counts[l] = counts.get(l, 0) + 1
    lines = ["**BRIGHTREACH - EARLIER REPLIES LOADED**", "",
             "I read the replies that arrived before this system existed and saved them to memory.",
             "", *[f"- {k.replace('_', ' ').lower()}: {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])], "",
             "Everything that needs you is in the attention list (python outreach_status.py --queue)."]
    notify.notify_once(conn, dedupe_key=f"br:backfill:{datetime.now(timezone.utc).date()}", code="reply.backfill",
                       severity="normal", text="\n".join(lines), send=send)


def push_pending_drafts(conn, *, imap_factory=imaplib.IMAP4_SSL, host: str = "imap.gmail.com") -> int:
    """Put prepared drafts in Gmail Drafts, threaded under the prospect's email, for the owner to send."""
    rows = conn.execute(
        "SELECT d.*, c.company_name FROM outreach_drafts d JOIN companies c USING (company_id) "
        "WHERE d.state = 'awaiting_approval' AND NOT (d.content ? 'gmail_draft_at') ORDER BY d.created_at LIMIT 25").fetchall()
    rows = [r for r in rows if store.send_check(conn, r["to_email"] or "", "reply", str(r["company_id"]))["allowed"]]
    if not rows:
        return 0
    user, pw = credentials()
    imap = imap_factory(host, 993)
    n = 0
    try:
        imap.login(user, pw)
        for r in rows:
            msg = EmailMessage()
            msg["From"] = os.environ.get("SMTP_FROM_EMAIL") or user
            msg["To"] = r["to_email"]
            subject = r["subject"] or f"Re: {r['company_name']}"
            msg["Subject"] = subject
            msg["Date"] = format_datetime(datetime.now(timezone.utc))
            msg["Message-ID"] = make_msgid(domain=(user.split("@")[-1] or "localhost"))
            parent = (r["content"] or {}).get("in_reply_to")
            if parent and r["kind"] != "referral_intro":
                msg["In-Reply-To"] = f"<{parent}>"
                msg["References"] = f"<{parent}>"
            msg["X-BrightReach-Draft"] = str(r["draft_id"])
            msg.set_content(r["body"])
            typ, _ = imap.append(DRAFTS, "(\\Draft)", imaplib.Time2Internaldate(time.time()), msg.as_bytes())
            if typ == "OK":
                conn.execute("UPDATE outreach_drafts SET content = content || jsonb_build_object('gmail_draft_at', now()::text) "
                             "WHERE draft_id = %s", (r["draft_id"],))
                conn.commit()
                n += 1
    finally:
        try:
            imap.logout()
        except Exception:
            pass
    return n
