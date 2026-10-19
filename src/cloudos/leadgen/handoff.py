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
import logging
import os
import re
import urllib.parse
from datetime import datetime, timezone

import httpx

from cloudos.leadgen import store
from cloudos.leadgen.engine import outreach_ready_rows

TAG = "lead_engine_company_id="
SENT_STATUSES = {"contacted", "emailed", "follow-up 1 sent", "follow-up 2 sent", "follow-ups complete", "out of office"}
log = logging.getLogger("cloudos.leadgen")


class AirtableAPIError(Exception):
    """One Airtable call failed. Callers stop; they do not retry."""

    def __init__(self, status: int | None, detail: str, created: list | None = None):
        self.status = status
        self.detail = detail
        self.created = list(created or [])
        super().__init__(detail)


class Airtable:
    def __init__(self, api_key: str, base_id: str, table: str):
        self.api_key = api_key
        self.base_id = base_id
        self.url = f"https://api.airtable.com/v0/{base_id}/{urllib.parse.quote(table)}"
        self.client = httpx.Client(timeout=60, headers={"Authorization": f"Bearer {api_key}"})

    def _scrub(self, text: str) -> str:
        for secret in (self.api_key, self.base_id):
            if secret:
                text = text.replace(secret, "[redacted]")
        return text

    def _public_detail(self, resp: httpx.Response) -> str:
        detail = f"HTTP {resp.status_code}"
        try:
            body = resp.json()
        except Exception:
            return detail
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict):
            text = " ".join(str(err.get(k) or "") for k in ("type", "message")).strip()
            if text:
                detail = text[:200]
        elif isinstance(err, str) and err.strip():
            detail = err.strip()[:200]
        return self._scrub(detail)

    def _send(self, method: str, **kwargs) -> httpx.Response:
        """One attempt. A failure raises AirtableAPIError and does not retry."""
        try:
            resp = self.client.request(method, self.url, **kwargs)
        except httpx.HTTPError:
            raise AirtableAPIError(None, "request_failed") from None
        if resp.status_code >= 400:
            raise AirtableAPIError(resp.status_code, self._public_detail(resp)) from None
        return resp

    def all(self, fields: list[str]) -> list[dict]:
        records, offset = [], ""
        while True:
            params = [("pageSize", "100")] + [("fields[]", f) for f in fields] + ([("offset", offset)] if offset else [])
            resp = self._send("GET", params=params)
            data = resp.json()
            records += data.get("records", [])
            offset = data.get("offset", "")
            if not offset:
                return records

    def create(self, rows: list[dict]) -> list[dict]:
        out = []
        for i in range(0, len(rows), 10):
            try:
                resp = self._send("POST", json={"records": [{"fields": r} for r in rows[i:i + 10]], "typecast": True})
            except AirtableAPIError as exc:
                exc.created = out
                raise
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


_SUPPRESSED = {"email_suppressed", "domain_suppressed", "company_suppressed", "do_not_contact", "bounced"}


def _cleared_by_guard(conn, rows: list[dict], want: int, dry_run: bool) -> tuple[list[dict], dict]:
    """Only rows the reply layer's send guard clears as a first touch go to the sender's tray.

    A row for someone who unsubscribed, bounced, is suppressed, or was already emailed is held back and
    its lead-engine status corrected so it stops coming back. Fail closed: if the guard itself errors,
    NOTHING is handed off this run."""
    from cloudos.conversations.guard import check_fail_closed

    ok: list[dict] = []
    refused: dict = {}
    for row in rows:
        if len(ok) >= want:
            break
        verdict = check_fail_closed(conn, row["email"] or "", "cold", row["company_id"])
        if verdict.get("allowed") is True:
            ok.append(row)
            continue
        reasons = set(verdict.get("reasons") or ["guard_error"])
        if reasons & {"guard_error"}:
            refused["guard_error"] = refused.get("guard_error", 0) + 1
            return [], refused
        key = "suppressed" if reasons & _SUPPRESSED else ("already_contacted" if "already_contacted" in reasons else "other")
        refused[key] = refused.get(key, 0) + 1
        if dry_run:
            continue
        new_status = {"suppressed": "do_not_contact", "already_contacted": "contacted"}.get(key)
        if new_status:
            conn.execute("UPDATE companies SET outreach_status = %s, updated_at = now() WHERE company_id = %s "
                         "AND outreach_status = 'outreach_ready'", (new_status, row["company_id"]))
    if not dry_run:
        conn.commit()
    return ok, refused


def _mark_handed_off(conn, rows: list[dict], created: list[dict]) -> None:
    now = datetime.now(timezone.utc)
    for row, rec in zip(rows, created):
        conn.execute("UPDATE companies SET outreach_status = 'handed_off', handed_off_at = %s, handoff_ref = %s, "
                     "updated_at = now() WHERE company_id = %s", (now, f"airtable:{rec['id']}", row["company_id"]))
    conn.commit()


def _defer_handoff(result: dict, exc: AirtableAPIError, *, handed_off: int) -> dict:
    """Record a skipped Airtable handoff. Does not call Airtable again."""
    count = 1
    result.update(deferred=True, handed_off=handed_off, airtable_errors=count,
                  airtable_status=exc.status, airtable_error=exc.detail)
    status = f"HTTP {exc.status}" if exc.status else "no response"
    noun = "error" if count == 1 else "errors"
    message = (f"Airtable handoff deferred: {count} {noun} ({status}, {exc.detail}). "
               f"Handed off: {handed_off}. No retry.")
    log.warning("%s", message)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(f"Airtable handoff deferred: {count} {noun} ({status}). Handed off: {handed_off}. No retry.\n")
        except OSError:
            log.warning("could not write the Airtable deferral count to the step summary")
    return result


def sync_and_handoff(conn, at: Airtable, cfg: dict, dry_run: bool = False) -> dict:
    """Top up the sender's tray and read back sends.

    Any Airtable error defers the rest of the handoff and returns. The failed
    call is not retried, so a monthly billing-plan 429 cannot spend more quota.
    """
    try:
        records = at.all(["Status", "Emailed At", "Email", "Signal"])
    except AirtableAPIError as exc:
        return _defer_handoff({"synced_sent": 0, "synced_unsubscribed": 0, "handed_off": 0}, exc, handed_off=0)
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
    rows, refused = _cleared_by_guard(conn, outreach_ready_rows(conn, space * 3), space, dry_run)
    result["blocked_by_guard"] = refused
    if refused.get("guard_error"):
        result["guard_error"] = "send guard unavailable - nothing handed off"
        return result
    if dry_run:
        result["would_hand_off"] = len(rows)
        return result
    try:
        created = at.create([_fields(r, cfg) for r in rows])
    except AirtableAPIError as exc:
        created = list(exc.created)
        if created:
            _mark_handed_off(conn, rows, created)
        return _defer_handoff(result, exc, handed_off=len(created))
    _mark_handed_off(conn, rows, created)
    result["handed_off"] = len(created)
    return result
