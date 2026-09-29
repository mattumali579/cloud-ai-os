"""Status summary, human-attention queue, and performance feedback for the lead generator."""
from __future__ import annotations

import os
import re
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

TZ = ZoneInfo("America/Chicago")
POSITIVE = ("INTERESTED", "PRICE_QUESTION", "MORE_INFORMATION", "MEETING_REQUEST", "READY_TO_BUY")
ACTIVE_ORDER = ["proposal_needed", "negotiating", "meeting_requested", "needs_review", "proposal_sent", "interested",
                "qualified", "replied", "not_now"]
MIN_SENT, MIN_REPLIES = 50, 5          # below this a group is "insufficient_data" - no changes from tiny samples


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime(day.year, day.month, day.day, tzinfo=TZ)
    return start, start + timedelta(days=1)


def airtable_emailed_count(day: date) -> int | None:
    """First emails confirmed sent that day = Airtable rows whose Emailed At falls on it (Chicago)."""
    key, base, table = (os.environ.get(k, "") for k in ("AIRTABLE_API_KEY", "AIRTABLE_BASE_ID", "AIRTABLE_TABLE_NAME"))
    if not (key and base and table):
        return None
    formula = f"IS_SAME(SET_TIMEZONE({{Emailed At}}, 'America/Chicago'), '{day.isoformat()}', 'day')"
    url = f"https://api.airtable.com/v0/{base}/{urllib.parse.quote(table)}"
    n, offset = 0, ""
    with httpx.Client(timeout=30, headers={"Authorization": f"Bearer {key}"}) as c:
        while True:
            params = {"filterByFormula": formula, "pageSize": "100", "fields[]": "Emailed At"}
            if offset:
                params["offset"] = offset
            r = c.get(url, params=params)
            r.raise_for_status()
            data = r.json()
            n += len(data.get("records", []))
            offset = data.get("offset", "")
            if not offset:
                return n


def status_report(conn, day: date | None = None, use_airtable: bool = False) -> dict:
    day = day or datetime.now(TZ).date()
    a, b = _day_bounds(day)
    one = lambda q, *p: conn.execute(q, (a, b, *p)).fetchone()["n"]  # noqa: E731
    by_label = {r["classification"]: r["n"] for r in conn.execute(
        "SELECT ra.classification, count(DISTINCT ra.message_id) n FROM reply_analyses ra JOIN outreach_messages m USING (message_id) "
        "WHERE m.occurred_at >= %s AND m.occurred_at < %s GROUP BY 1", (a, b)).fetchall()}
    moved = {r["to_status"]: r["n"] for r in conn.execute(
        "SELECT to_status, count(DISTINCT company_id) n FROM company_status_transitions WHERE at >= %s AND at < %s GROUP BY 1",
        (a, b)).fetchall()}
    airtable_sent = airtable_emailed_count(day) if use_airtable else None
    today = {
        "emails_confirmed_sent": airtable_sent if airtable_sent is not None else one(
            "SELECT count(DISTINCT company_id) n FROM outreach_messages WHERE direction='outbound' AND kind='cold' "
            "AND occurred_at >= %s AND occurred_at < %s"),
        "emails_confirmed_sent_source": "Airtable Emailed At" if airtable_sent is not None else "database (Gmail Sent Mail)",
        "follow_ups_and_replies_sent": one("SELECT count(*) n FROM outreach_messages WHERE direction='outbound' AND kind <> 'cold' "
                                           "AND occurred_at >= %s AND occurred_at < %s"),
        "replies": one("SELECT count(*) n FROM outreach_messages WHERE direction='inbound' AND kind='inbound' "
                       "AND occurred_at >= %s AND occurred_at < %s"),
        "positive_replies": sum(by_label.get(l, 0) for l in POSITIVE),
        "price_questions": by_label.get("PRICE_QUESTION", 0),
        "meeting_requests": by_label.get("MEETING_REQUEST", 0),
        "interested": by_label.get("INTERESTED", 0),
        "proposals_needed": moved.get("proposal_needed", 0),
        "proposals_sent": moved.get("proposal_sent", 0),
        "won": moved.get("won", 0),
        "lost": moved.get("lost", 0),
        "needs_review": by_label.get("NEEDS_REVIEW", 0) + one(
            "SELECT count(*) n FROM outreach_messages WHERE direction='inbound' AND company_id IS NULL AND kind='inbound' "
            "AND occurred_at >= %s AND occurred_at < %s"),
        "unsubscribes": by_label.get("UNSUBSCRIBE", 0),
        "bounces": by_label.get("DELIVERY_FAILURE", 0),
    }
    totals = {r["current_status"]: r["n"] for r in conn.execute(
        "SELECT current_status, count(*) n FROM company_conversation_state GROUP BY 1").fetchall()}
    return {"day": day.isoformat(), "today": today, "all_time_by_status": totals, "top_prospects": top_prospects(conn),
            "attention": attention_queue(conn)}


def top_prospects(conn, limit: int = 10) -> list[dict]:
    rows = conn.execute(
        "SELECT s.company_id, s.company_name, s.current_status, s.last_reply_classification, s.last_meaningful_summary, "
        "s.next_action, s.next_action_due, s.high_value, s.last_reply_at, s.current_price FROM company_conversation_state s "
        "WHERE s.current_status = ANY(%s)", (ACTIVE_ORDER,)).fetchall()
    rows = sorted(rows, key=lambda r: (ACTIVE_ORDER.index(r["current_status"]), not r["high_value"],
                                       -(r["last_reply_at"].timestamp() if r["last_reply_at"] else 0)))
    return [{"company": r["company_name"], "status": r["current_status"], "last_classification": r["last_reply_classification"],
             "last_message": r["last_meaningful_summary"], "next_action": r["next_action"],
             "due": r["next_action_due"].isoformat() if r["next_action_due"] else None, "price": r["current_price"],
             "high_value": r["high_value"]} for r in rows[:limit]]


def attention_queue(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT q.*, coalesce(c.company_name, 'UNKNOWN (unmatched)') company, s.current_status FROM human_attention_queue q "
        "LEFT JOIN companies c USING (company_id) LEFT JOIN company_conversation_state s USING (company_id) "
        "WHERE q.state = 'open' ORDER BY CASE q.urgency WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 ELSE 2 END, q.created_at").fetchall()
    return [{"item_id": r["item_id"], "company": r["company"], "company_id": str(r["company_id"]) if r["company_id"] else None,
             "status": r["current_status"] or r["status_snapshot"], "last_reply": r["last_reply"], "summary": r["summary"],
             "recommended_action": r["recommended_action"], "urgency": r["urgency"], "reason_code": r["reason_code"],
             "reason_human_needed": r["reason_human_needed"], "record_link": r["record_link"],
             "since": r["created_at"].isoformat()} for r in rows]


def _subject_template(subject: str, company: str) -> str:
    s = subject or ""
    if company:
        s = re.sub(re.escape(company), "{company}", s, flags=re.I)
    return re.sub(r"^(re|fwd?):\s*", "", s, flags=re.I).strip()[:120]


def feedback(conn) -> dict:
    """Machine-readable performance by industry / source / subject / copy / offer / CTA.

    Only groups with >= MIN_SENT companies emailed AND >= MIN_REPLIES replies get a
    recommendation. No names or emails in the output (safe for the public repo).
    """
    rows = conn.execute("""
        WITH first AS (
            SELECT DISTINCT ON (m.company_id) m.company_id, m.subject, m.copy_variant, m.offer, m.cta
            FROM outreach_messages m WHERE m.direction = 'outbound' AND m.kind = 'cold' AND m.company_id IS NOT NULL
            ORDER BY m.company_id, m.occurred_at)
        SELECT f.*, c.company_name, coalesce(c.industry, 'unknown') industry, c.discovery_source source,
               EXISTS (SELECT 1 FROM outreach_messages i WHERE i.company_id = f.company_id AND i.direction='inbound' AND i.kind='inbound') replied,
               EXISTS (SELECT 1 FROM reply_analyses ra WHERE ra.company_id = f.company_id AND ra.classification = ANY(%s)) positive,
               EXISTS (SELECT 1 FROM company_status_transitions t WHERE t.company_id = f.company_id AND t.to_status='meeting_requested') meeting,
               EXISTS (SELECT 1 FROM company_status_transitions t WHERE t.company_id = f.company_id AND t.to_status IN ('proposal_needed','proposal_sent')) proposal,
               EXISTS (SELECT 1 FROM company_status_transitions t WHERE t.company_id = f.company_id AND t.to_status='won') won,
               coalesce(d.setup_usd, 0) + 12 * coalesce(d.monthly_usd, 0) revenue_first_year
        FROM first f JOIN companies c USING (company_id) LEFT JOIN deals d ON d.company_id = f.company_id AND d.won_at IS NOT NULL
    """, (list(POSITIVE),)).fetchall()
    total = len(rows)
    overall_pos = sum(r["positive"] for r in rows) / total if total else 0.0
    overall_rep = sum(r["replied"] for r in rows) / total if total else 0.0

    def group(key) -> dict:
        out: dict = {}
        for r in rows:
            k = key(r) or "unknown"
            g = out.setdefault(k, {"sent": 0, "replies": 0, "positive_replies": 0, "meetings": 0, "proposals": 0,
                                   "wins": 0, "revenue_first_year_usd": 0.0})
            g["sent"] += 1
            g["replies"] += int(r["replied"])
            g["positive_replies"] += int(r["positive"])
            g["meetings"] += int(r["meeting"])
            g["proposals"] += int(r["proposal"])
            g["wins"] += int(r["won"])
            g["revenue_first_year_usd"] += float(r["revenue_first_year"])
        for g in out.values():
            g["reply_rate"] = round(g["replies"] / g["sent"], 3)
            g["positive_rate"] = round(g["positive_replies"] / g["sent"], 3)
            g["recommendation"] = _recommend(g, overall_pos, overall_rep)
        return out

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(), "companies_emailed": total,
        "overall": {"reply_rate": round(overall_rep, 3), "positive_rate": round(overall_pos, 3)},
        "rules": {"min_sent": MIN_SENT, "min_replies": MIN_REPLIES},
        "by_industry": group(lambda r: r["industry"]), "by_source": group(lambda r: r["source"]),
        "by_subject": group(lambda r: _subject_template(r["subject"], r["company_name"])),
        "by_copy_variant": group(lambda r: r["copy_variant"]), "by_offer": group(lambda r: r["offer"]),
        "by_cta": group(lambda r: (r["cta"] or "")[:120]),
        "lead_engine_priority": {k: v["recommendation"] for k, v in group(lambda r: r["industry"]).items()},
    }


def _recommend(g: dict, overall_pos: float, overall_rep: float) -> str:
    if g["sent"] < MIN_SENT or g["replies"] < MIN_REPLIES:
        return "insufficient_data"
    if g["wins"] or (g["positive_rate"] >= 1.5 * overall_pos and g["positive_replies"] >= 3):
        return "increase_priority"
    if g["reply_rate"] >= overall_rep and g["positive_replies"] / max(g["replies"], 1) < 0.2:
        return "hold_many_replies_low_intent"
    if g["positive_replies"] == 0 and g["sent"] >= 2 * MIN_SENT:
        return "decrease_priority"
    return "hold"
