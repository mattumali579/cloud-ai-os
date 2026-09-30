"""Mirror every Hostinger-sent lead's state into Airtable "B2B LeadGen / Leads".

The database is the ledger that decides who may be emailed (it cannot run out of
room). Airtable is where the owner looks, so each lead's row shows: the exact
subject and body, when it went out, from which mailbox, its Message-ID, how many
follow-ups, the latest reply and its classification, and any failure.

Free-plan limits (support.airtable.com "Managing API call limits"): 1,000 API
calls per month and 1,000 records per base; 5 requests/second. So:
  * only rows whose content changed are written (hash per company)
  * 10 records per call (the API maximum)
  * the monthly budget is spread evenly over the days left in the month
  * the most important changes go first: replies, then failures, then sends
"""
from __future__ import annotations

import calendar
import hashlib
import json
import os
import time
import urllib.parse
from datetime import datetime, timezone

import httpx

from cloudos.conversations import store

STATUS = {  # conversation status -> Airtable Status choice (existing choices reused where they exist)
    "replied": "Replied", "interested": "Interested", "qualified": "Interested", "meeting_requested": "Interested",
    "proposal_needed": "Interested", "proposal_sent": "Interested", "negotiating": "Interested", "won": "Won",
    "lost": "Not Interested", "not_now": "Not Now", "do_not_contact": "Unsubscribed", "needs_review": "Needs Review",
}
PRIORITY = {"Interested": 0, "Replied": 1, "Unsubscribed": 1, "Not Interested": 2, "Needs Review": 1, "Send Failed": 2,
            "Bounced": 2, "Emailed": 3, "Follow-up 1 Sent": 4, "Follow-up 2 Sent": 4, "Follow-ups Complete": 5}


def desired(conn, cfg: dict) -> list[dict]:
    rows = conn.execute(
        """
        SELECT c.company_id::text cid, c.company_name, c.domain, c.website, c.industry, c.phone, c.ready_at,
               q0.recipient, q0.subject, q0.body, q0.copy_variant, q0.sent_at, q0.message_id_header, q0.state q0_state,
               q0.stop_reason q0_reason,
               (SELECT count(*) FROM outreach_queue q WHERE q.company_id = c.company_id AND q.step > 0 AND q.state = 'sent') fu_sent,
               (SELECT max(sent_at) FROM outreach_queue q WHERE q.company_id = c.company_id AND q.step > 0 AND q.state = 'sent') fu_last,
               (SELECT count(*) FROM outreach_queue q WHERE q.company_id = c.company_id AND q.step > 0 AND q.state = 'queued') fu_waiting,
               s.current_status, s.last_reply_classification, s.bounced, s.do_not_contact,
               (SELECT left(m.body, 1500) FROM outreach_messages m WHERE m.company_id = c.company_id
                  AND m.direction = 'inbound' AND m.kind NOT IN ('bounce') ORDER BY m.occurred_at DESC LIMIT 1) last_reply,
               m.record_id mirror_rec, m.pushed_hash
        FROM outreach_queue q0 JOIN companies c USING (company_id)
        LEFT JOIN company_conversation_state s USING (company_id)
        LEFT JOIN airtable_mirror m USING (company_id)
        WHERE q0.step = 0 AND q0.state IN ('sent','failed','ambiguous')
        """).fetchall()
    out = []
    for r in rows:
        if r["q0_state"] == "failed":
            status = "Bounced" if r["bounced"] else "Send Failed"
        elif r["q0_state"] == "ambiguous":
            status = "Needs Review"
        elif r["current_status"] in STATUS:
            status = STATUS[r["current_status"]]
        elif r["bounced"]:
            status = "Bounced"
        elif r["fu_sent"] and not r["fu_waiting"] and r["fu_sent"] >= len(cfg["sequence"]["followup_days"]):
            status = "Follow-ups Complete"
        elif r["fu_sent"]:
            status = f"Follow-up {r['fu_sent']} Sent"
        else:
            status = "Emailed"
        f = {
            "Status": status, "Email": r["recipient"], "Subject": r["subject"], "Email Preview": r["body"],
            "Copy Variant": r["copy_variant"] or "", "Sent From": os.environ.get("HOSTINGER_EMAIL", ""),
            "Message ID": r["message_id_header"] or "", "Follow-ups Sent": int(r["fu_sent"] or 0),
            "Reply Status": (r["last_reply_classification"] or "").replace("_", " ").title(),
            "Latest Reply": r["last_reply"] or "", "Failure Reason": (r["q0_reason"] or "")[:250]
            if r["q0_state"] != "sent" else "",
        }
        if r["sent_at"]:
            f["Emailed At"] = r["sent_at"].astimezone(timezone.utc).isoformat()
        if r["fu_last"]:
            f["Last Follow-up At"] = r["fu_last"].astimezone(timezone.utc).isoformat()
        if r["ready_at"]:
            f["Ready At"] = r["ready_at"].astimezone(timezone.utc).isoformat()
        h = hashlib.sha1(json.dumps(f, sort_keys=True, default=str).encode()).hexdigest()
        if h == r["pushed_hash"]:
            continue
        rec = r["mirror_rec"] or store.airtable_record_of(conn, r["cid"])
        if not rec:   # a lead the sender took straight from the database: create its row
            f.update({"Company": r["company_name"], "Domain": r["domain"] or "", "Website": r["website"] or "",
                      "Phone": r["phone"] or "", "Category": r["industry"] or "",
                      "Signal": f"lead_engine_company_id={r['cid']}"})
        out.append({"cid": r["cid"], "rec": rec, "fields": f, "hash": h, "prio": PRIORITY.get(status, 3)})
    out.sort(key=lambda d: d["prio"])
    return out


def _budget_today(conn, cfg: dict, now: datetime) -> int:
    month = now.strftime("%Y-%m")
    used = (conn.execute("SELECT calls FROM airtable_api_usage WHERE month = %s", (month,)).fetchone() or {"calls": 0})["calls"]
    left = max(int(cfg["airtable"]["monthly_call_budget"]) - used, 0)
    days_left = calendar.monthrange(now.year, now.month)[1] - now.day + 1
    spent_today = (conn.execute("SELECT value FROM airtable_state WHERE key = %s", (f"calls:{now.date()}",)).fetchone()
                   or {"value": 0})["value"]
    per_day = -(-left // days_left) if days_left else left
    return max(min(per_day - int(spent_today), left), 0)


def _count_call(conn, now: datetime, n: int = 1) -> None:
    conn.execute("INSERT INTO airtable_api_usage (month, calls) VALUES (%s,%s) ON CONFLICT (month) DO UPDATE "
                 "SET calls = airtable_api_usage.calls + EXCLUDED.calls", (now.strftime("%Y-%m"), n))
    conn.execute("INSERT INTO airtable_state (key, value) VALUES (%s, to_jsonb(%s::int)) ON CONFLICT (key) DO UPDATE "
                 "SET value = to_jsonb((airtable_state.value)::text::int + %s), updated_at = now()",
                 (f"calls:{now.date()}", n, n))


def sync(conn, cfg: dict, *, client: httpx.Client | None = None, now: datetime | None = None) -> dict:
    key, base, table = (os.environ.get(k, "") for k in ("AIRTABLE_API_KEY", "AIRTABLE_BASE_ID", "AIRTABLE_TABLE_NAME"))
    if not (key and base and table):
        return {"skipped": "Airtable settings missing"}
    now = now or datetime.now(timezone.utc)
    todo = desired(conn, cfg)
    budget = _budget_today(conn, cfg, now)
    res = {"changed": len(todo), "budget_today": budget, "updated": 0, "created": 0, "calls": 0, "deferred": 0}
    if not todo:
        return res
    url = f"https://api.airtable.com/v0/{base}/{urllib.parse.quote(table)}"
    c = client or httpx.Client(timeout=60, headers={"Authorization": f"Bearer {key}"})
    count = (conn.execute("SELECT value FROM airtable_state WHERE key = 'record_count'").fetchone() or {}).get("value")
    ceiling = int(cfg["airtable"]["record_ceiling"])
    updates = [d for d in todo if d["rec"]]
    creates = [d for d in todo if not d["rec"]]
    batches = [("PATCH", updates[i:i + 10]) for i in range(0, len(updates), 10)]
    batches += [("POST", creates[i:i + 10]) for i in range(0, len(creates), 10)]
    batches.sort(key=lambda b: min(d["prio"] for d in b[1]))
    for method, batch in batches:
        if res["calls"] >= budget:
            res["deferred"] += len(batch)
            continue
        if method == "POST":
            if count is None or int(count) + len(batch) > ceiling:
                res["deferred"] += len(batch)
                res["ceiling"] = "Airtable base is at its record limit - new rows wait; the database has them"
                continue
            body = {"records": [{"fields": d["fields"]} for d in batch], "typecast": True}
        else:
            body = {"records": [{"id": d["rec"], "fields": d["fields"]} for d in batch], "typecast": True}
        r = c.request(method, url, json=body)
        res["calls"] += 1
        _count_call(conn, now)
        if r.status_code == 429:
            conn.commit()
            time.sleep(30)
            res["deferred"] += len(batch)
            continue
        if r.status_code >= 400:
            conn.commit()
            res.setdefault("errors", []).append(f"HTTP {r.status_code}")
            continue
        recs = r.json().get("records", [])
        for d, rec in zip(batch, recs):
            conn.execute("INSERT INTO airtable_mirror (company_id, record_id, pushed_hash, pushed_at) VALUES (%s,%s,%s,now()) "
                         "ON CONFLICT (company_id) DO UPDATE SET record_id = EXCLUDED.record_id, "
                         "pushed_hash = EXCLUDED.pushed_hash, pushed_at = now()", (d["cid"], rec["id"], d["hash"]))
            if method == "POST":
                conn.execute("UPDATE companies SET handoff_ref = coalesce(handoff_ref, %s) WHERE company_id = %s",
                             (f"airtable:{rec['id']}", d["cid"]))
        if method == "POST":
            count = int(count) + len(recs)
            conn.execute("INSERT INTO airtable_state (key, value) VALUES ('record_count', to_jsonb(%s::int)) "
                         "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()", (count,))
        res["created" if method == "POST" else "updated"] += len(recs)
        conn.commit()
        time.sleep(0.25)   # stay under 5 requests/second
    return res


def refresh_record_count(conn, *, client: httpx.Client | None = None) -> int | None:
    """Count Airtable rows (1 call per 100 rows). Run rarely - it spends the monthly budget."""
    key, base, table = (os.environ.get(k, "") for k in ("AIRTABLE_API_KEY", "AIRTABLE_BASE_ID", "AIRTABLE_TABLE_NAME"))
    if not (key and base and table):
        return None
    url = f"https://api.airtable.com/v0/{base}/{urllib.parse.quote(table)}"
    c = client or httpx.Client(timeout=60, headers={"Authorization": f"Bearer {key}"})
    n, offset, calls = 0, "", 0
    while True:
        params = [("pageSize", "100"), ("fields[]", "Company")] + ([("offset", offset)] if offset else [])
        r = c.get(url, params=params)
        calls += 1
        _count_call(conn, datetime.now(timezone.utc))
        conn.commit()
        r.raise_for_status()
        data = r.json()
        n += len(data.get("records", []))
        offset = data.get("offset", "")
        if not offset:
            break
    conn.execute("INSERT INTO airtable_state (key, value) VALUES ('record_count', to_jsonb(%s::int)) "
                 "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()", (n,))
    conn.commit()
    return n
