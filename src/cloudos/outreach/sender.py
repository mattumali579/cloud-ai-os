"""The Hostinger outreach sender.

    plan()      Ready companies -> final dedupe + suppression (send guard) -> copy + QA -> queue step 0
    run()       claim one due row at a time -> guard again -> Message-ID saved -> SMTP -> confirmed record
                -> next follow-up scheduled. Paced, capped, only inside the send window.
    recover()   rows a dead worker left in 'claimed': proven sent (Sent folder) / never sent / ambiguous
    sweep()     cancel queued follow-ups for anyone who replied, unsubscribed, bounced or was closed

Guarantees (tests/test_outreach_sender.py proves each one):
  * one row per (company, step) in the database - a first touch or follow-up exists once, ever
  * a row is claimed by exactly one worker (FOR UPDATE SKIP LOCKED); two workers never send it twice
  * the send guard (outreach_send_check) runs immediately before every SMTP call, fail-closed
  * "sent" is written only after the SMTP server accepted the recipient
  * a worker that died mid-send never causes a resend: the Sent folder decides, else 'ambiguous'
"""
from __future__ import annotations

import json
import os
import random
import re
import socket
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

import yaml

from cloudos.conversations import guard, store
from cloudos.config import REPO_ROOT
from cloudos.conversations.text import business_domain
from cloudos.outreach import copy as copywriter
from cloudos.outreach import review_campaign
from cloudos.outreach.transport import AuthError, Mailbox, build, new_message_id

ROOT = REPO_ROOT
CONFIG = REPO_ROOT / "config" / "outreach_sender.yaml"
STALE_CLAIM = timedelta(minutes=15)
_SUPPRESSED = {"email_suppressed", "domain_suppressed", "company_suppressed", "do_not_contact", "bounced"}
_USABLE_CONTACT = "email_status IN ('validated','published')"     # the only contacts a first touch may go to
_AUDIT_LABEL = re.compile(r"[a-z_]+")
_WEEKDAYS = [0, 1, 2, 3, 4]
# One-shot marker: unsent trade first touches are rewritten onto v4 once, then left alone.
COPY_RERENDER_V4 = "copy_rerender:v4"


def load_config(path: Path = CONFIG) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def worker_id() -> str:
    return f"{os.environ.get('GITHUB_RUN_ID') or socket.gethostname()}-{os.getpid()}"


def sender_name(cfg: dict) -> str:
    s = cfg["sender"]
    return (os.environ.get(s.get("from_name_env", "SENDER_NAME")) or "").strip() or s.get("from_name_default", "Matt")


def postal_address() -> str:
    return (os.environ.get("SENDER_POSTAL_ADDRESS") or "").strip()


def industry_limit(cfg: dict) -> list[str] | None:
    """Industries new first touches are limited to, or None (= all) while the limit is disabled."""
    lim = (cfg.get("planning") or {}).get("industry_limit") or {}
    return list(lim.get("industries") or []) or None if lim.get("enabled") else None


def v4_industries(cfg: dict) -> list[str]:
    x = cfg.get("experiment") or {}
    return [str(i).strip().lower() for i in (x.get("v4_industries") or []) if str(i).strip()]


def copy_arm(row: dict, cfg: dict) -> str:
    """'v4', 'v3' or 'v2' for this company (config: experiment). Deterministic, so it never flips."""
    x = cfg.get("experiment") or {}
    return copywriter.arm(str(row["company_id"]), row.get("industry") or "",
                          share=float(x.get("v3_share") or 0), industries=x.get("v3_industries") or [],
                          v4_share=float(x.get("v4_share") or 0), v4_industries=x.get("v4_industries") or [])


def followup_days(cfg: dict, version: str) -> list:
    """Business days between touches for this copy arm. v4 has its own (4-touch) cadence."""
    x = cfg.get("experiment") or {}
    if version == copywriter.V4 and x.get("v4_followup_days"):
        return list(x["v4_followup_days"])
    return list(cfg["sequence"]["followup_days"])


# ------------------------------------------------------------------ pacing
def local_now(cfg: dict, now: datetime | None = None) -> datetime:
    return (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(cfg["pacing"]["timezone"]))


def in_window(cfg: dict, now: datetime | None = None) -> bool:
    t = local_now(cfg, now)
    p = cfg["pacing"]
    if t.weekday() not in p["send_days"]:
        return False
    hm = t.strftime("%H:%M")
    return p["window_start"] <= hm < p["window_end"]


def business_days(cfg: dict) -> list[int]:
    """Days follow-ups may land on. Independent of send_days, so a 7-day send window
    still schedules follow-ups on Monday-Friday."""
    raw = (cfg.get("pacing") or {}).get("business_days")
    days = [int(d) for d in raw] if raw else list(_WEEKDAYS)
    return days or list(_WEEKDAYS)


def add_business_days(start: datetime, days: int, cfg: dict) -> datetime:
    """start + N business days, landing at 9:00-11:00 local. Weekends count only when
    pacing.business_days says so — not merely because send_days includes them."""
    tz = ZoneInfo(cfg["pacing"]["timezone"])
    allowed = set(business_days(cfg))
    d = start.astimezone(tz).date()
    added = 0
    while added < days:
        d += timedelta(days=1)
        if d.weekday() in allowed:
            added += 1
    minute = random.randint(0, 119)
    return datetime(d.year, d.month, d.day, 9 + minute // 60, minute % 60, tzinfo=tz).astimezone(timezone.utc)


def add_sequence_days(start: datetime, days: int, cfg: dict, campaign: str = "legacy") -> datetime:
    """Schedule active reviews-campaign touches by calendar day; retain the
    legacy business-day behavior for every existing campaign."""
    if campaign == review_campaign.CAMPAIGN and (cfg.get("sequence") or {}).get("day_mode") == "calendar":
        tz = ZoneInfo(cfg["pacing"]["timezone"])
        d = (start.astimezone(tz) + timedelta(days=days)).date()
        minute = random.randint(0, 119)
        return datetime(d.year, d.month, d.day, 9 + minute // 60, minute % 60, tzinfo=tz).astimezone(timezone.utc)
    return add_business_days(start, days, cfg)


def sent_last_24h(conn) -> int:
    return conn.execute("SELECT count(*) n FROM outreach_queue WHERE state = 'sent' AND sent_at > now() - interval '24 hours'"
                        ).fetchone()["n"]


def daily_cap(conn, cfg: dict) -> int:
    """Return the owner-authorized campaign ceiling.

    The mailbox transport remains authoritative for real provider throttling.
    A stale HOSTINGER_DAILY_LIMIT secret must not silently re-enable a retired
    campaign hold.
    """
    return max(0, int(cfg["pacing"]["daily_limit"]))


# ----------------------------------------------------------------- planning
def _candidates(conn, limit: int, industries: list[str] | None = None) -> list[dict]:
    return conn.execute(
        f"""
        SELECT c.company_id::text, c.company_name, c.domain, c.normalized_domain, c.industry, c.city, c.state,
               c.qualification_status, c.personalization, c.outreach_status, ct.email
        FROM companies c
        JOIN LATERAL (SELECT email FROM contacts WHERE company_id = c.company_id
                      AND {_USABLE_CONTACT} ORDER BY (email_status = 'validated') DESC,
                      (role = 'generic') DESC, discovered_at LIMIT 1) ct ON true
        WHERE c.outreach_status IN ('outreach_ready', 'handed_off') AND c.first_contacted_at IS NULL AND c.active
          AND c.qualification_status IN ('HIGH', 'MEDIUM')
          AND NOT EXISTS (SELECT 1 FROM outreach_queue q WHERE q.company_id = c.company_id)
          AND (%s::text[] IS NULL OR c.industry = ANY(%s::text[]))
        ORDER BY (c.qualification_status = 'HIGH') DESC, c.ready_at NULLS LAST, c.discovered_at
        LIMIT %s
        """, (industries or None, industries or None, limit)).fetchall()


def _duplicate_in_queue(conn, email: str, company_id: str, exclude_queue_id: int | None = None) -> bool:
    """The same business under another record: this address, or another address at the same business
    email domain, is already queued/sent here, was emailed by any earlier sender, or belongs to a company
    that was already contacted. Free-mail domains (gmail.com...) are never treated as a business."""
    dom = business_domain(email)
    row = conn.execute(
        "SELECT 1 FROM outreach_queue WHERE state NOT IN ('cancelled','failed') AND queue_id IS DISTINCT FROM %s AND "
        "(outreach_norm_email(recipient) = outreach_norm_email(%s) OR (%s <> '' AND split_part(lower(recipient),'@',2) = %s)) "
        "AND company_id <> %s::uuid LIMIT 1", (exclude_queue_id, email, dom, dom, company_id)).fetchone()
    if row or not dom:
        return row is not None
    row = conn.execute(
        """
        SELECT 1 WHERE EXISTS (SELECT 1 FROM outreach_history WHERE status = 'sent' AND split_part(lower(email),'@',2) = %(d)s)
           OR EXISTS (SELECT 1 FROM outreach_messages WHERE direction = 'outbound' AND split_part(lower(recipient),'@',2) = %(d)s)
           OR EXISTS (SELECT 1 FROM contacts ct JOIN companies c USING (company_id)
                      WHERE split_part(lower(ct.email),'@',2) = %(d)s AND c.company_id <> %(c)s::uuid
                        AND (c.first_contacted_at IS NOT NULL OR c.outreach_status IN
                             ('contacted','replied','bounced','unsubscribed','do_not_contact')))
           OR EXISTS (SELECT 1 FROM email_suppressions WHERE domain = %(d)s)
        """, {"d": dom, "c": company_id}).fetchone()
    return row is not None


def rerender_queued_v4(conn, cfg: dict) -> dict:
    """Rewrite unsent, never-attempted first touches in v4 industries onto v4 copy. Once.

    Runs on the next plan cycle, then records `copy_rerender:v4` so it does not run again.
    Sent, claimed, attempted, follow-up, and already-contacted rows are never updated.
    A missing postal address defers the pass (the flag stays unset) so the next cycle retries.
    QA is the same gate as planning: a failure cancels that unsent row instead of sending it.
    """
    out = {"skipped": False, "rewritten": 0, "qa_failed": 0}
    x = cfg.get("experiment") or {}
    industries = [str(i).strip().lower() for i in (x.get("v4_industries") or []) if str(i).strip()]
    if float(x.get("v4_share") or 0) <= 0 or not industries:
        return {**out, "skipped": True, "reason": "v4_off"}
    if conn.execute("SELECT 1 FROM airtable_state WHERE key = %s", (COPY_RERENDER_V4,)).fetchone():
        return {**out, "skipped": True, "reason": "already_done"}
    addr = postal_address()
    if not addr:
        return {**out, "deferred": "no_postal_address"}
    name = sender_name(cfg)
    rows = conn.execute(
        """
        SELECT q.queue_id, q.company_id::text, q.copy_variant, c.company_name, c.industry, c.city, c.personalization
        FROM outreach_queue q JOIN companies c USING (company_id)
        WHERE q.step = 0 AND q.state = 'queued' AND q.attempts = 0 AND q.message_id_header IS NULL
          AND q.campaign = 'legacy' AND q.copy_source = 'python-generated'
          AND c.first_contacted_at IS NULL
          AND lower(btrim(c.industry)) = ANY(%s::text[])
          AND coalesce(q.copy_variant, '') NOT LIKE 'v4-%%'
          AND NOT EXISTS (SELECT 1 FROM outreach_queue o WHERE o.company_id = q.company_id
                          AND o.state IN ('sent', 'ambiguous', 'claimed'))
          AND NOT EXISTS (SELECT 1 FROM outreach_messages m WHERE m.company_id = q.company_id
                          AND m.direction = 'outbound')
        """, (industries,)).fetchall()
    for r in rows:
        if copy_arm(r, cfg) != copywriter.V4:
            continue
        e = copywriter.first_touch(dict(r), sender_name=name, postal_address=addr, version=copywriter.V4)
        problems = copywriter.qa(e, postal_address=addr, company_name=r["company_name"])
        state, stop = ("queued", None) if not problems else ("cancelled", "qa: " + "; ".join(problems))
        cur = conn.execute(
            "UPDATE outreach_queue SET subject = %s, body = %s, copy_variant = %s, state = %s, stop_reason = %s, "
            "updated_at = now() WHERE queue_id = %s AND state = 'queued' AND step = 0 AND attempts = 0 "
            "AND message_id_header IS NULL",
            (e.subject, e.body, e.variant, state, stop, r["queue_id"]))
        if cur.rowcount:
            out["qa_failed" if problems else "rewritten"] += 1
    conn.execute(
        "INSERT INTO airtable_state (key, value) VALUES (%s, %s::jsonb) ON CONFLICT (key) DO NOTHING",
        (COPY_RERENDER_V4, json.dumps({"version": copywriter.V4, "rewritten": out["rewritten"],
                                       "qa_failed": out["qa_failed"]})))
    conn.commit()
    return out


def refresh_queued(conn, cfg: dict) -> dict:
    """Rewrite first touches that were prepared with older wording (or for the other copy arm) and
    never touched by a send attempt (no claim, no Message-ID). Anything attempted keeps its exact text."""
    out = {"rewritten": 0, "qa_failed": 0}
    addr = postal_address()
    if not addr:
        return out
    name = sender_name(cfg)
    rows = conn.execute(
        """
        SELECT q.queue_id, q.company_id::text, q.copy_variant, c.company_name, c.industry, c.city, c.personalization
        FROM outreach_queue q JOIN companies c USING (company_id)
        WHERE q.step = 0 AND q.state = 'queued' AND q.attempts = 0 AND q.message_id_header IS NULL
          AND q.campaign = 'legacy' AND q.copy_source = 'python-generated'
        """).fetchall()
    for r in rows:
        version = copy_arm(r, cfg)
        if (r["copy_variant"] or "").startswith(version + "-"):
            continue                                   # already written with the wording this company gets
        e = copywriter.first_touch(dict(r), sender_name=name, postal_address=addr, version=version)
        problems = copywriter.qa(e, postal_address=addr, company_name=r["company_name"])
        state, stop = ("queued", None) if not problems else ("cancelled", "qa: " + "; ".join(problems))
        cur = conn.execute(
            "UPDATE outreach_queue SET subject = %s, body = %s, copy_variant = %s, state = %s, stop_reason = %s, "
            "updated_at = now() WHERE queue_id = %s AND state = 'queued' AND attempts = 0 "
            "AND message_id_header IS NULL",
            (e.subject, e.body, e.variant, state, stop, r["queue_id"]))
        if cur.rowcount:
            out["qa_failed" if problems else "rewritten"] += 1
    conn.commit()
    return out


def queue_first_touch_counts(conn) -> tuple[int, int]:
    """(all queued first touches, those already on v4 copy)."""
    row = conn.execute(
        "SELECT count(*) n, count(*) FILTER (WHERE copy_variant LIKE %s) v4 "
        "FROM outreach_queue WHERE state = 'queued' AND step = 0", ("v4-%",)).fetchone()
    return int(row["n"]), int(row["v4"])


def plan_slots(cfg: dict, have: int, have_v4: int, limit: int | None) -> tuple[int, int]:
    """(other_slots, v4_slots) this call may prepare.

    v4_queue_ahead is its own budget: a full general queue does not block new v4 first
    touches, and v4 rows do not consume the general budget. An explicit limit keeps the
    single shared budget (manual plan and tests)."""
    if limit is not None:
        return max(int(limit) - have, 0), 0
    planning = cfg.get("planning") or {}
    other = max(int(planning.get("queue_ahead") or 0) - (have - have_v4), 0)
    v4_budget = int(planning.get("v4_queue_ahead") or 0)
    v4 = max(v4_budget - have_v4, 0) if v4_budget else 0
    return other, v4


def trade_ready_counts(conn, cfg: dict) -> dict:
    """Counts only. Ready, usable-email, not suppressed, never-contacted companies in the v4 industries."""
    industries = v4_industries(cfg)
    by = {ind: {"ready": 0, "unqueued": 0} for ind in industries}
    queued_v4 = queue_first_touch_counts(conn)[1] if industries else 0
    if not industries:
        return {"ready": 0, "unqueued": 0, "queued_v4": 0, "by_industry": by}
    rows = conn.execute(
        f"""
        SELECT lower(btrim(c.industry)) AS industry, count(*) AS ready,
               count(*) FILTER (WHERE NOT EXISTS (
                   SELECT 1 FROM outreach_queue q WHERE q.company_id = c.company_id)) AS unqueued
        FROM companies c
        JOIN LATERAL (SELECT email FROM contacts WHERE company_id = c.company_id AND {_USABLE_CONTACT}
                      ORDER BY (email_status = 'validated') DESC, (role = 'generic') DESC, discovered_at
                      LIMIT 1) ct ON true
        WHERE c.outreach_status IN ('outreach_ready', 'handed_off') AND c.first_contacted_at IS NULL AND c.active
          AND c.qualification_status IN ('HIGH', 'MEDIUM')
          AND lower(btrim(c.industry)) = ANY(%s::text[])
          AND NOT EXISTS (
              SELECT 1 FROM email_suppressions s
              WHERE (s.email IS NOT NULL AND outreach_norm_email(s.email) = outreach_norm_email(ct.email))
                 OR (coalesce(s.domain, '') <> '' AND s.domain = split_part(lower(trim(ct.email)), '@', 2))
                 OR s.company_id = c.company_id)
        GROUP BY 1
        """, (industries,)).fetchall()
    for r in rows:
        by[r["industry"]] = {"ready": int(r["ready"]), "unqueued": int(r["unqueued"])}
    return {"ready": sum(v["ready"] for v in by.values()),
            "unqueued": sum(v["unqueued"] for v in by.values()),
            "queued_v4": queued_v4, "by_industry": by}


def plan(conn, cfg: dict, *, limit: int | None = None) -> dict:
    """Prepare first touches for Ready companies. Nothing is sent here."""
    active = (cfg.get("campaign") or {}).get("id")
    if active == review_campaign.CAMPAIGN:
        return review_campaign.queue_approved(conn, postal_address=postal_address(), limit=limit)
    migrated = rerender_queued_v4(conn, cfg)
    refreshed = refresh_queued(conn, cfg)
    have, have_v4 = queue_first_touch_counts(conn)
    other_slots, v4_slots = plan_slots(cfg, have, have_v4, limit)
    v4_budget_on = limit is None and int((cfg.get("planning") or {}).get("v4_queue_ahead") or 0) > 0
    out = {"queued": 0, "blocked": {}, "qa_failed": 0, "already_waiting": have, "v4_waiting": have_v4,
           "trades": trade_ready_counts(conn, cfg)}
    if not migrated.get("skipped"):
        out["v4_rerender"] = migrated
    if refreshed["rewritten"] or refreshed["qa_failed"]:
        out["refreshed"] = refreshed
    if other_slots == 0 and v4_slots == 0:
        return out
    addr = postal_address()
    name = sender_name(cfg)
    if not addr:
        # a missing setting is not the lead's fault: prepare nothing, cancel nothing
        out["stopped"] = "SENDER_POSTAL_ADDRESS is not set - nothing prepared"
        return out
    added_other = added_v4 = 0
    for row in _candidates(conn, (other_slots + v4_slots) * 2, industry_limit(cfg)):
        if added_v4 >= v4_slots and added_other >= other_slots:
            break
        version = copy_arm(row, cfg)
        use_v4 = v4_budget_on and version == copywriter.V4
        if use_v4:
            if added_v4 >= v4_slots:
                continue                     # v4 does not consume the general budget
        elif added_other >= other_slots:
            continue
        email = (row["email"] or "").strip().lower()
        verdict = guard.check_fail_closed(conn, email, "cold", row["company_id"])
        if verdict.get("allowed") is not True:
            reasons = set(verdict.get("reasons") or ["guard_error"])
            if "guard_error" in reasons:
                out["blocked"]["guard_error"] = out["blocked"].get("guard_error", 0) + 1
                conn.rollback()
                return out            # fail closed: a broken guard stops planning entirely
            key = "suppressed" if reasons & _SUPPRESSED else ("already_contacted" if "already_contacted" in reasons else
                                                              sorted(reasons)[0])
            out["blocked"][key] = out["blocked"].get(key, 0) + 1
            new = {"suppressed": "do_not_contact", "already_contacted": "contacted"}.get(key)
            if new:
                conn.execute("UPDATE companies SET outreach_status = %s, updated_at = now() WHERE company_id = %s "
                             "AND outreach_status IN ('outreach_ready','handed_off')", (new, row["company_id"]))
            conn.commit()
            continue
        if _duplicate_in_queue(conn, email, row["company_id"]):
            out["blocked"]["duplicate_business"] = out["blocked"].get("duplicate_business", 0) + 1
            conn.execute("UPDATE companies SET outreach_status = 'rejected', qualification_reason = "
                         "'same business as a company already emailed or queued', updated_at = now() WHERE company_id = %s",
                         (row["company_id"],))
            conn.commit()
            continue
        e = copywriter.first_touch(row, sender_name=name, postal_address=addr, version=version)
        problems = copywriter.qa(e, postal_address=addr, company_name=row["company_name"])
        state, stop = ("queued", None) if not problems else ("cancelled", "qa: " + "; ".join(problems))
        conn.execute("INSERT INTO outreach_queue (company_id, step, recipient, subject, body, copy_variant, state, stop_reason) "
                     "VALUES (%s,0,%s,%s,%s,%s,%s,%s) ON CONFLICT (company_id, step) DO NOTHING",
                     (row["company_id"], email, e.subject, e.body, e.variant, state, stop))
        conn.commit()
        if problems:
            out["qa_failed"] += 1
        else:
            out["queued"] += 1
            if use_v4:
                added_v4 += 1
            else:
                added_other += 1
    return out


# ------------------------------------------------------------------ sending
def claim(conn, who: str, *, followups: bool = True) -> dict | None:
    row = conn.execute(
        """
        UPDATE outreach_queue SET state = 'claimed', claimed_by = %s, claimed_at = now(), attempts = attempts + 1,
               updated_at = now()
        WHERE queue_id = (SELECT queue_id FROM outreach_queue WHERE state = 'queued' AND due_at <= now()
                          AND (%s OR step = 0)
                          ORDER BY (step = 0 AND copy_variant LIKE 'v4-%%') DESC,
                                   step DESC, due_at, queue_id
                          LIMIT 1 FOR UPDATE SKIP LOCKED)
        RETURNING *
        """, (who, followups)).fetchone()
    conn.commit()
    return row


def _release(conn, item: dict, *, delay: timedelta | None = None, undo_attempt: bool = False) -> None:
    conn.execute("UPDATE outreach_queue SET state = 'queued', claimed_by = NULL, claimed_at = NULL, "
                 "due_at = greatest(due_at, now() + %s), attempts = attempts - %s, updated_at = now() "
                 "WHERE queue_id = %s AND state = 'claimed'",
                 (delay or timedelta(0), 1 if undo_attempt else 0, item["queue_id"]))
    conn.commit()


def _finish(conn, item: dict, state: str, reason: str) -> None:
    conn.execute("UPDATE outreach_queue SET state = %s, stop_reason = %s, updated_at = now() WHERE queue_id = %s",
                 (state, reason[:300], item["queue_id"]))
    if state == "ambiguous":
        # it may have been delivered: every sender's guard must now treat this company as contacted
        when = item.get("claimed_at") or datetime.now(timezone.utc)
        conn.execute("UPDATE companies SET first_contacted_at = coalesce(first_contacted_at, %s), "
                     "outreach_status = CASE WHEN outreach_status IN ('outreach_ready','handed_off') THEN 'contacted' "
                     "ELSE outreach_status END, updated_at = now() WHERE company_id = %s", (when, item["company_id"]))
        conn.execute("INSERT INTO outreach_history (company_id, email, campaign, sent_at, status, provider, dedupe_key) "
                     "VALUES (%s,%s,'ambiguous',%s,'sent','hostinger',%s) ON CONFLICT (dedupe_key) DO NOTHING",
                     (item["company_id"], item["recipient"], when, f"ambiguous:{item['queue_id']}"))
    conn.commit()


def record_sent(conn, item: dict, cfg: dict, *, from_email: str, sent_at: datetime, note: str) -> str | None:
    """The provider accepted the mail: store it, mark the row, schedule the next touch. One transaction."""
    kind = "cold" if item["step"] == 0 else "followup"
    root = item["thread_root"] or item["message_id_header"]
    mid = store.record_confirmed_send(
        conn, company_id=str(item["company_id"]), recipient=item["recipient"], sender=from_email,
        subject=item["subject"], body=item["body"], sent_at=sent_at, provider="hostinger",
        provider_message_id=item["message_id_header"], thread_id=root, kind=kind,
        copy_variant=item["copy_variant"], source_ref=f"queue:{item['queue_id']}",
        meta={"step": item["step"], "note": note})
    if mid is None:   # this exact Message-ID was already recorded (a second recovery): keep the existing record
        mid = str(store.message_by_provider_id(conn, item["message_id_header"])["message_id"])
    conn.execute("UPDATE outreach_queue SET state = 'sent', sent_at = %s, provider_response = %s, outreach_message_id = %s, "
                 "updated_at = now() WHERE queue_id = %s", (sent_at, note[:200], mid, item["queue_id"]))
    conn.execute("UPDATE companies SET outreach_status = 'contacted', updated_at = now() WHERE company_id = %s "
                 "AND outreach_status IN ('outreach_ready','handed_off')", (item["company_id"],))
    first = item if item["step"] == 0 else (conn.execute(
        "SELECT subject, copy_variant, campaign, copy_source FROM outreach_queue WHERE company_id = %s AND step = 0",
        (item["company_id"],)).fetchone() or item)
    campaign = first.get("campaign") or "legacy"
    if campaign == review_campaign.CAMPAIGN:
        next_step = int(item["step"]) + 1
        approved = conn.execute(
            "SELECT subject, body, copy_version, evidence FROM outreach_campaign_copy "
            "WHERE company_id=%s AND campaign=%s AND step=%s AND qa_passed_at IS NOT NULL",
            (item["company_id"], campaign, next_step),
        ).fetchone()
        days = list(cfg["sequence"]["followup_days"])
        if approved and item["step"] < len(days):
            body = review_campaign.add_footer(approved["body"], postal_address())
            problems = copywriter.qa(copywriter.Email(approved["subject"] or first["subject"], body,
                                                       approved["copy_version"]),
                                     postal_address=postal_address(), company_name=(store.company(
                                         conn, str(item["company_id"])) or {}).get("company_name", ""))
            conn.execute(
                "INSERT INTO outreach_queue (company_id, step, recipient, subject, body, copy_variant, thread_root, "
                "due_at, state, stop_reason, campaign, copy_source, evidence) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (company_id, step) DO NOTHING",
                (item["company_id"], next_step, item["recipient"], approved["subject"] or first["subject"], body,
                 approved["copy_version"], root, add_sequence_days(sent_at, int(days[item["step"]]), cfg, campaign),
                 "cancelled" if problems else "queued", ("qa: " + "; ".join(problems)) if problems else None,
                 campaign, approved["copy_version"], json.dumps(approved["evidence"] or {})),
            )
        conn.commit()
        return mid
    version = (first["copy_variant"] or "").split("-")[0]          # follow-ups stay in the first touch's arm
    version = version if version in (copywriter.V3, copywriter.V4) else copywriter.COPY_VERSION
    days = followup_days(cfg, version)
    if item["step"] < len(days):
        company = store.company(conn, str(item["company_id"]))
        e = copywriter.followup(dict(company), item["step"] + 1, first["subject"],
                                sender_name=sender_name(cfg), postal_address=postal_address(), version=version)
        problems = copywriter.qa(e, postal_address=postal_address(), company_name=company["company_name"])
        conn.execute("INSERT INTO outreach_queue (company_id, step, recipient, subject, body, copy_variant, thread_root, "
                     "due_at, state, stop_reason) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                     "ON CONFLICT (company_id, step) DO NOTHING",
                     (item["company_id"], item["step"] + 1, item["recipient"], e.subject, e.body, e.variant, root,
                      add_sequence_days(sent_at, int(days[item["step"]]), cfg, campaign),
                      "cancelled" if problems else "queued", ("qa: " + "; ".join(problems)) if problems else None))
    conn.commit()
    return mid


def _refusal(conn, item: dict) -> tuple[str, list[str]] | None:
    """Why this row must not be sent right now, or None when it may go. Reads only - the caller acts.
    (guard_error | guard | duplicate_business | qa, the reasons). send_item() and audit_queue() share it."""
    kind = "cold" if item["step"] == 0 else "followup"
    verdict = guard.check_fail_closed(conn, item["recipient"], kind, str(item["company_id"]))
    if verdict.get("allowed") is not True:
        reasons = list(verdict.get("reasons") or ["guard_error"])
        return ("guard_error" if "guard_error" in reasons else "guard"), reasons
    if item["step"] == 0 and _duplicate_in_queue(conn, item["recipient"], str(item["company_id"]), item["queue_id"]):
        return "duplicate_business", []
    problems = copywriter.qa(copywriter.Email(item["subject"], item["body"], item["copy_variant"] or ""),
                             postal_address=postal_address(),
                             company_name=(store.company(conn, str(item["company_id"])) or {}).get("company_name", ""))
    return ("qa", problems) if problems else None


def audit_queue(conn) -> dict:
    """How many prepared first touches would pass every send-time check right now. Reads only: nothing is
    claimed, cancelled, rewritten or sent, and nothing is committed. Counts and failure categories only."""
    out = {"audited": 0, "passing": 0, "failing": 0, "failures": {}, "guard_reasons": {}, "qa_problems": {},
           "postal_address_set": bool(postal_address())}

    def bump(group: str, key: str) -> None:
        out[group][key] = out[group].get(key, 0) + 1

    rows = conn.execute("SELECT * FROM outreach_queue WHERE state = 'queued' AND step = 0 ORDER BY queue_id").fetchall()
    for item in rows:
        out["audited"] += 1
        refusal = _refusal(conn, item)
        if refusal is None or refusal[0] in ("duplicate_business", "qa"):
            # the address must still be one the planner may pick; checked right after the guard
            usable = conn.execute("SELECT 1 FROM contacts WHERE company_id = %s AND lower(trim(email)) = %s AND "
                                  + _USABLE_CONTACT + " LIMIT 1", (item["company_id"], item["recipient"])).fetchone()
            if usable is None:
                refusal = ("contact_not_eligible", [])
        if refusal is None:
            out["passing"] += 1
            continue
        out["failing"] += 1
        bump("failures", refusal[0])
        for reason in refusal[1]:
            if refusal[0] == "guard":
                bump("guard_reasons", reason if _AUDIT_LABEL.fullmatch(str(reason)) else "other")
            elif refusal[0] == "qa":
                bump("qa_problems", reason.split(" (")[0])      # never the wording that tripped it
    conn.rollback()                                              # nothing was written; end the read
    return out


def send_item(conn, item: dict, mailbox: Mailbox, cfg: dict, *, from_email: str) -> str:
    """Returns: sent | cancelled | failed | retry | ambiguous | auth."""
    refusal = _refusal(conn, item)
    if refusal:
        why, reasons = refusal
        if why == "guard_error":
            _release(conn, item, delay=timedelta(minutes=10), undo_attempt=True)
            return "retry"
        _finish(conn, item, "cancelled", {"guard": "guard: " + ", ".join(reasons), "qa": "qa: " + "; ".join(reasons),
                                          "duplicate_business": "same business as a company already emailed or queued"}[why])
        return "cancelled"
    msgid = item["message_id_header"]
    if not msgid:
        msgid = new_message_id(from_email)
        conn.execute("UPDATE outreach_queue SET message_id_header = %s WHERE queue_id = %s", (msgid, item["queue_id"]))
        conn.commit()                 # saved BEFORE the SMTP call - the crash-proof marker
        item = {**item, "message_id_header": msgid}
    msg = build(from_email=from_email, from_name=sender_name(cfg), to=item["recipient"], subject=item["subject"],
                body=item["body"], message_id=msgid, in_reply_to=item["thread_root"],
                company_id=str(item["company_id"]), step=item["step"])
    res = mailbox.send(msg)
    if res.ok:
        sent_at = datetime.now(timezone.utc)
        _file_copy(mailbox, msg, msgid)
        record_sent(conn, item, cfg, from_email=from_email, sent_at=sent_at, note=res.detail)
        return "sent"
    if res.kind == "unknown":
        # the connection broke while the message was being handed over: it may have been delivered
        if mailbox.in_sent(msgid):
            record_sent(conn, item, cfg, from_email=from_email, sent_at=datetime.now(timezone.utc),
                        note="found in Sent after an interrupted hand-over")
            return "sent"
        _finish(conn, item, "ambiguous", f"interrupted mid-send ({res.detail}) - not resent")
        return "ambiguous"
    if res.kind == "auth":
        _release(conn, item, undo_attempt=True)
        return "auth"
    if res.kind == "throttled":
        _release(conn, item, delay=timedelta(minutes=60), undo_attempt=True)
        return "throttled"
    if res.kind == "permanent":
        _finish(conn, item, "failed", f"rejected: {res.detail}")
        store.suppress(conn, reason="hard_bounce", email=item["recipient"])
        cid = str(item["company_id"])
        store.update_state(conn, cid, bounced=True, cold_sequence_active=False, sequence_stop_reason="address rejected")
        conn.execute("UPDATE companies SET outreach_status = 'bounced', updated_at = now() WHERE company_id = %s", (cid,))
        conn.commit()
        return "failed"
    if item["attempts"] >= int(cfg["pacing"]["max_attempts"]):
        _finish(conn, item, "failed", f"gave up after {item['attempts']} tries: {res.detail}")
        return "failed"
    # nothing was handed over: try again later with the same Message-ID
    conn.execute("UPDATE outreach_queue SET provider_response = %s WHERE queue_id = %s", (res.detail[:200], item["queue_id"]))
    _release(conn, item, delay=timedelta(minutes=20 * item["attempts"]))
    return "retry"


def _file_copy(mailbox: Mailbox, msg, msgid: str) -> None:
    """Keep a copy in Sent. Some mailboxes file SMTP mail there themselves: checked once per run."""
    if getattr(mailbox, "auto_files_sent", None) is None:
        mailbox.auto_files_sent = bool(mailbox.in_sent(msgid))
    if not mailbox.auto_files_sent:
        mailbox.file_in_sent(msg)


def recover(conn, mailbox: Mailbox | None, cfg: dict, *, from_email: str) -> dict:
    """Rows a dead worker left 'claimed'. Never resends anything that might have gone out."""
    out = {"released": 0, "proven_sent": 0, "ambiguous": 0, "unknown": 0}
    rows = conn.execute("SELECT * FROM outreach_queue WHERE state = 'claimed' AND claimed_at < now() - %s",
                        (STALE_CLAIM,)).fetchall()
    for r in rows:
        if not r["message_id_header"]:
            _release(conn, r)                    # never reached the SMTP step
            out["released"] += 1
            continue
        if mailbox is None:
            out["unknown"] += 1
            continue
        found = mailbox.in_sent(r["message_id_header"])
        if found is True:
            record_sent(conn, r, cfg, from_email=from_email, sent_at=r["claimed_at"], note="recovered from Sent folder")
            out["proven_sent"] += 1
        elif found is False:
            _finish(conn, r, "ambiguous", "worker stopped mid-send and Sent has no copy - not resent")
            out["ambiguous"] += 1
        else:
            out["unknown"] += 1
    return out


def sweep(conn, cfg: dict) -> dict:
    """Cancel waiting follow-ups the moment a sequence should stop (the guard would refuse them anyway)."""
    rows = conn.execute(
        """
        UPDATE outreach_queue q SET state = 'cancelled', updated_at = now(),
               stop_reason = 'sequence stopped: ' || coalesce(s.current_status, '') ||
                             CASE WHEN s.do_not_contact THEN ' (do not contact)' WHEN s.bounced THEN ' (bounced)' ELSE '' END
        FROM company_conversation_state s
        WHERE q.company_id = s.company_id AND q.state = 'queued' AND q.step > 0
          AND (s.do_not_contact OR s.bounced OR s.current_status = ANY(%s)
               OR EXISTS (SELECT 1 FROM outreach_messages m WHERE m.company_id = q.company_id AND m.direction = 'inbound'
                          AND m.kind NOT IN ('auto_reply','bounce')))
        RETURNING q.company_id, q.stop_reason
        """, (list(cfg["sequence"]["stop_statuses"]),)).fetchall()
    conn.commit()
    return {"cancelled": len(rows), "rows": [dict(r) for r in rows]}


def run(conn, mailbox: Mailbox, cfg: dict, *, from_email: str, minutes: float, followups: bool = True,
        sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> dict:
    who = worker_id()
    bad_run = 0
    deadline = clock() + minutes * 60
    stats = {"sent": 0, "cancelled": 0, "failed": 0, "retry": 0, "ambiguous": 0, "throttled": 0, "auth_failed": False, "cap": daily_cap(conn, cfg),
             "sent_24h_before": sent_last_24h(conn), "in_window": in_window(cfg, now())}
    if not stats["in_window"]:
        return stats
    while clock() < deadline:
        if sent_last_24h(conn) >= stats["cap"]:
            stats["stopped"] = "daily cap reached"
            break
        if not in_window(cfg, now()):
            stats["stopped"] = "send window closed"
            break
        item = claim(conn, who, followups=followups)
        if not item:
            stats["stopped"] = "nothing due"
            break
        outcome = send_item(conn, item, mailbox, cfg, from_email=from_email)
        if outcome == "auth":
            stats["auth_failed"] = True
            break
        stats[outcome] += 1
        if outcome == "throttled":
            stats["stopped"] = "mail server says a sending limit was reached"
            break
        bad_run = bad_run + 1 if outcome in ("failed", "retry") else 0
        if bad_run >= 3:
            # three failures in a row is the server, not the addresses: stop before it hurts more leads
            stats["stopped"] = "3 failures in a row - paused until the next run"
            break
        if outcome == "sent":
            gap = random.uniform(float(cfg["pacing"]["min_gap_seconds"]), float(cfg["pacing"]["max_gap_seconds"]))
            left = deadline - clock()
            if left <= gap:
                break
            sleep(gap)
    return stats
