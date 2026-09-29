"""Hand outreach-ready companies to the EXISTING sender, and read back what it sent.

The existing sender (AI-Second-Brain leadgen-pipeline, Instantly lane) consumes
Airtable "B2B LeadGen / Leads" rows that have Email + Website + Company +
Category and no contacted/suppressed status. This module keeps a small rolling
window of engine leads there (Airtable free plan: 1,000 records per base) and
marks a company contacted ONLY when the sender itself stamped "Emailed At" -
drafted, queued or skipped never counts as sent.
"""
from __future__ import annotations

import json
import re
import urllib.parse
from datetime import datetime, timezone

import httpx

from cloudos.leadgen import store
from cloudos.leadgen.engine import outreach_ready_rows

TAG = "lead_engine_company_id="
SENT_STATUSES = {"contacted", "emailed", "follow-up 1 sent", "follow-up 2 sent", "follow-ups complete", "out of office"}


class Airtable:
    def __init__(self, api_key: str, base_id: str, table: str):
        self.url = f"https://api.airtable.com/v0/{base_id}/{urllib.parse.quote(table)}"
        self.client = httpx.Client(timeout=60, headers={"Authorization": f"Bearer {api_key}"})

    def all(self, fields: list[str]) -> list[dict]:
        records, offset = [], ""
        while True:
            params = [("pageSize", "100")] + [("fields[]", f) for f in fields] + ([("offset", offset)] if offset else [])
            resp = self.client.get(self.url, params=params)
            resp.raise_for_status()
            data = resp.json()
            records += data.get("records", [])
            offset = data.get("offset", "")
            if not offset:
                return records

    def create(self, rows: list[dict]) -> list[dict]:
        out = []
        for i in range(0, len(rows), 10):
            resp = self.client.post(self.url, json={"records": [{"fields": r} for r in rows[i:i + 10]], "typecast": True})
            resp.raise_for_status()
            out += resp.json().get("records", [])
        return out


def _fields(row: dict, cfg: dict) -> dict:
    facts = row["personalization_facts"] or {}
    if isinstance(facts, str):
        facts = json.loads(facts)
    signal = "; ".join([
        f"{TAG}{row['company_id']}",
        f"qualification={row['qualification_level']}",
        f"reason={row['qualification_reason']}",
        f"source={row['source']}",
        f"email_status={row['email_status']}",
        f"email_source={row['email_source_url'] or ''}",
        "facts=" + json.dumps({k: v for k, v in facts.items() if k not in {'pages_read'}}, default=str)[:700],
    ])
    return {
        "Company": row["company_name"], "Domain": row["domain"] or "", "Website": row["website"] or "",
        "Email": row["email"], "Phone": row["phone"] or "", "Category": row["industry"] or "",
        "Offer": cfg["handoff"]["offer"], "Status": cfg["handoff"]["status"], "Signal": signal,
    }


def sync_and_handoff(conn, at: Airtable, cfg: dict, dry_run: bool = False) -> dict:
    records = at.all(["Status", "Emailed At", "Email", "Signal"])
    result = {"airtable_records": len(records), "synced_sent": 0, "synced_unsubscribed": 0, "handed_off": 0}

    # 1) read back provider-confirmed outcomes for engine leads
    waiting = 0
    for rec in records:
        f = rec.get("fields", {})
        m = re.search(TAG + r"([0-9a-f-]{36})", f.get("Signal") or "")
        if not m:
            continue
        company_id = m.group(1)
        status = str(f.get("Status") or "").lower()
        if f.get("Emailed At"):
            sent_at = datetime.fromisoformat(f["Emailed At"].replace("Z", "+00:00"))
            if store.record_outreach(conn, company_id, f.get("Email", ""), "airtable-handoff", sent_at, "sent",
                                     "leadgen-pipeline", f"airtable:{rec['id']}:sent"):
                result["synced_sent"] += 1
            conn.execute("UPDATE companies SET outreach_status = CASE WHEN outreach_status IN ('handed_off','outreach_ready') "
                         "THEN 'contacted' ELSE outreach_status END, first_contacted_at = coalesce(first_contacted_at, %s), "
                         "last_contacted_at = GREATEST(last_contacted_at, %s), updated_at = now() WHERE company_id = %s",
                         (sent_at, sent_at, company_id))
        elif status == "unsubscribed":
            conn.execute("UPDATE companies SET outreach_status = 'unsubscribed', updated_at = now() WHERE company_id = %s",
                         (company_id,))
            result["synced_unsubscribed"] += 1
        elif status == cfg["handoff"]["status"].lower():
            waiting += 1
    conn.commit()

    # 2) top the window back up
    space = min(int(cfg["handoff"]["airtable_ready_window"]) - waiting,
                int(cfg["handoff"]["airtable_record_ceiling"]) - len(records))
    result.update(waiting_in_airtable=waiting, space=max(space, 0))
    if space <= 0:
        return result
    rows = outreach_ready_rows(conn, space)
    if dry_run:
        result["would_hand_off"] = len(rows)
        return result
    created = at.create([_fields(r, cfg) for r in rows])
    now = datetime.now(timezone.utc)
    for row, rec in zip(rows, created):
        conn.execute("UPDATE companies SET outreach_status = 'handed_off', handed_off_at = %s, handoff_ref = %s, "
                     "updated_at = now() WHERE company_id = %s", (now, f"airtable:{rec['id']}", row["company_id"]))
    conn.commit()
    result["handed_off"] = len(created)
    return result
