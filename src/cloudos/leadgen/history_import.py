"""Import every reachable historical lead/outreach record BEFORE discovery runs.

Sources (each optional; a missing one is reported, never fatal):
  airtable     B2B LeadGen / Leads - the durable ledger of the old pipeline
  csv          old enriched-lead CSV exports
  outbox       cloud-ai-os email_outbox/sent wave files (recipient lists)
  gmail_sent   Gmail "Sent Mail" headers; only messages that are provably
               outreach (machine header, or recipient/subject matching the ledger)
"""
from __future__ import annotations

import csv
import email
import email.utils
import imaplib
import json
import os
import re
import urllib.parse
from collections import Counter
from datetime import datetime, timezone
from email.header import decode_header, make_header
from pathlib import Path

import httpx

from cloudos.leadgen import store
from cloudos.leadgen.normalize import FREEMAIL, email_domain, normalize_domain, normalize_email
from cloudos.leadgen.store import Candidate

CONTACTED_AIRTABLE = {"contacted", "emailed", "follow-up 1 sent", "follow-up 2 sent", "follow-ups complete",
                      "out of office", "replied", "interested", "booked"}
STATUS_MAP = {
    "unsubscribed": ("unsubscribed", "REJECT", "historical: unsubscribed - never contact again"),
    "rejected": ("rejected", "REJECT", "historical: rejected by old qualifier"),
    "dead site": ("rejected", "REJECT", "historical: dead site"),
    "send failed": ("rejected", "REJECT", "historical: earlier send failed - address unreliable"),
    "no email found": ("not_ready", "LOW", "historical: never contacted, no email found at the time"),
    "scrape failed": ("not_ready", "LOW", "historical: never contacted, scrape failed at the time"),
    "preparing": ("not_ready", "LOW", "historical: prepared but never sent"),
}


def _parse_dt(value: str):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        try:
            return email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None


class Importer:
    def __init__(self, conn):
        self.conn = conn
        self.stats: dict[str, Counter] = {}

    def _count(self, source: str, key: str, n: int = 1) -> None:
        self.stats.setdefault(source, Counter())[key] += n

    def upsert(self, source: str, hist_id: str, cand: Candidate, *, emails: list[str], contacted_at=None,
               outreach_status: str, qualification: str, reason: str) -> str:
        self._count(source, "raw_records")
        cand.historical_ids = [hist_id]
        cand.source = f"history:{source}"
        existing = store.find_existing(self.conn, cand, emails)
        if existing is None:
            company_id = store.insert_company(
                self.conn, cand, qualification=qualification, reason=reason, outreach_status=outreach_status,
                personalization={}, is_historical=True, first_contacted_at=contacted_at, last_contacted_at=contacted_at)
            self._count(source, "new_companies")
        else:
            company_id = existing.company_id
            self._count(source, "merged_into_existing" if hist_id not in self._ids(company_id) else "already_imported")
            contacted_rank = ["not_ready", "rejected", "contacted", "unsubscribed"]
            self.conn.execute(
                """
                UPDATE companies SET
                  historical_ids = (SELECT array_agg(DISTINCT x) FROM unnest(historical_ids || %s::text[]) x),
                  is_historical = true,
                  first_contacted_at = CASE WHEN %s::timestamptz IS NULL THEN first_contacted_at
                                            ELSE LEAST(coalesce(first_contacted_at, %s::timestamptz), %s::timestamptz) END,
                  last_contacted_at  = GREATEST(last_contacted_at, %s::timestamptz),
                  outreach_status = CASE WHEN array_position(%s::text[], %s) > coalesce(array_position(%s::text[], outreach_status), 0)
                                         THEN %s ELSE outreach_status END,
                  website = coalesce(website, %s), phone = coalesce(phone, %s), industry = coalesce(industry, %s),
                  updated_at = now()
                WHERE company_id = %s
                """,
                ([hist_id], contacted_at, contacted_at, contacted_at, contacted_at,
                 contacted_rank, outreach_status, contacted_rank, outreach_status,
                 cand.website or None, cand.phone or None, cand.industry or None, company_id),
            )
        for addr in emails:
            if store.add_contact(self.conn, company_id, addr, "published", role="historical", source=f"history:{source}"):
                self._count(source, "contacts")
        if contacted_at:
            for addr in emails or [""]:
                store.record_outreach(self.conn, company_id, addr, cand.extra.get("campaign", "legacy"), contacted_at,
                                      "sent", f"history:{source}", f"{source}:{hist_id}:{addr}")
        return company_id

    def _ids(self, company_id: str) -> list[str]:
        row = self.conn.execute("SELECT historical_ids FROM companies WHERE company_id = %s", (company_id,)).fetchone()
        return list(row["historical_ids"] or []) if row else []

    # ---------------------------------------------------------------- airtable
    def import_airtable(self, records: list[dict]) -> None:
        for rec in records:
            f = rec.get("fields") or {}
            if "lead_engine_company_id=" in str(f.get("Signal") or ""):
                self._count("airtable", "skipped_engine_handoff_rows")  # our own handoffs, synced by handoff.py
                continue
            status = str(f.get("Status") or "").strip().lower()
            emailed_at = _parse_dt(f.get("Emailed At"))
            if status == "unsubscribed":
                ostatus, level, reason = STATUS_MAP["unsubscribed"]
                contacted_at = emailed_at or _parse_dt(rec.get("createdTime"))
            elif emailed_at or status in CONTACTED_AIRTABLE:
                ostatus, level, reason = "contacted", "REJECT", f"historical: already contacted ({f.get('Status')})"
                contacted_at = emailed_at or _parse_dt(rec.get("createdTime"))
            else:
                ostatus, level, reason = STATUS_MAP.get(status, ("not_ready", "LOW", f"historical: status {f.get('Status')!r}"))
                contacted_at = None
            website = f.get("Website") or (f"http://{f['Domain']}" if f.get("Domain") and "." in str(f.get("Domain")) else "")
            cand = Candidate(name=str(f.get("Company") or f.get("Domain") or "unknown").strip(), website=website,
                             industry=str(f.get("Category") or "").strip().lower(), phone=str(f.get("Phone") or ""),
                             extra={"campaign": f.get("Copy Variant") or "airtable-ledger"})
            emails = [e for e in [normalize_email(f.get("Email"))] if e]
            self.upsert("airtable", f"airtable:{rec['id']}", cand, emails=emails, contacted_at=contacted_at,
                        outreach_status=ostatus, qualification=level, reason=reason)

    # --------------------------------------------------------------------- csv
    def import_csv(self, path: Path) -> None:
        with path.open(encoding="utf-8-sig", errors="replace") as fh:
            for i, row in enumerate(csv.DictReader(fh)):
                low = {k.lower().strip(): (v or "").strip() for k, v in row.items() if k}
                name = low.get("company") or low.get("name") or low.get("title") or low.get("business_name") or ""
                website = low.get("website") or low.get("url") or low.get("domain") or ""
                emails = [e for e in (normalize_email(x) for x in re.split(r"[;, ]", low.get("email", "") + " " + low.get("emails", ""))) if e]
                if not name and not website:
                    continue
                cand = Candidate(name=name or website, website=website, industry=low.get("category", "").lower(),
                                 phone=low.get("phone", ""))
                self.upsert("csv", f"csv:{path.name}:{i}", cand, emails=emails, outreach_status="not_ready",
                            qualification="LOW", reason="historical: old CSV export, contact status unknown")

    # ------------------------------------------------------------------ outbox
    def import_outbox(self, folder: Path) -> None:
        for path in sorted(folder.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                continue
            if data.get("status") != "sent":
                continue
            when = _parse_dt(data.get("sent_at") or "") or datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            for addr in data.get("sent") or []:
                addr = normalize_email(addr)
                if not addr:
                    continue
                dom = email_domain(addr)
                cand = Candidate(name=dom if dom not in FREEMAIL else addr, website="" if dom in FREEMAIL else f"http://{dom}",
                                 extra={"campaign": data.get("draft_id") or path.stem})
                self.upsert("outbox", f"outbox:{path.stem}:{addr}", cand, emails=[addr], contacted_at=when,
                            outreach_status="contacted", qualification="REJECT",
                            reason="historical: already contacted (cloud-ai-os outbox)")

    # -------------------------------------------------------------- gmail sent
    def import_gmail_sent(self, user: str, password: str, since: str = "01-Jan-2026") -> None:
        known_emails = {r["email"] for r in self.conn.execute("SELECT lower(email) AS email FROM contacts").fetchall()}
        known_domains = {r["d"] for r in self.conn.execute(
            "SELECT normalized_domain AS d FROM companies WHERE normalized_domain IS NOT NULL").fetchall()}
        imap = imaplib.IMAP4_SSL("imap.gmail.com")
        imap.login(user, password)
        try:
            imap.select('"[Gmail]/Sent Mail"', readonly=True)
            _, data = imap.search(None, "SINCE", since)
            ids = data[0].split()
            self._count("gmail_sent", "messages_scanned", len(ids))
            for start in range(0, len(ids), 200):
                chunk = b",".join(ids[start:start + 200])
                _, fetched = imap.fetch(chunk, "(BODY.PEEK[HEADER.FIELDS (TO CC SUBJECT DATE X-LEADGEN-MACHINE X-LEADGEN-RECORD-ID)])")
                for part in fetched:
                    if not isinstance(part, tuple):
                        continue
                    msg = email.message_from_bytes(part[1])
                    machine = msg.get("X-Leadgen-Machine")
                    subject = str(make_header(decode_header(msg.get("Subject") or "")))
                    when = _parse_dt(msg.get("Date") or "")
                    recips = [normalize_email(a) for _, a in email.utils.getaddresses(msg.get_all("To", []) + msg.get_all("Cc", []))]
                    for addr in filter(None, recips):
                        if addr == normalize_email(user):
                            continue
                        dom = email_domain(addr)
                        is_outreach = bool(machine) or addr in known_emails or (dom not in FREEMAIL and dom in known_domains)
                        if not is_outreach:
                            self._count("gmail_sent", "non_outreach_recipients_skipped")
                            continue
                        cand = Candidate(name=dom if dom not in FREEMAIL else addr,
                                         website="" if dom in FREEMAIL else f"http://{dom}",
                                         extra={"campaign": machine or "gmail-sent"})
                        hist = f"gmail:{msg.get('X-Leadgen-Record-ID') or ''}:{addr}:{when.isoformat() if when else subject[:40]}"
                        self.upsert("gmail_sent", hist, cand, emails=[addr], contacted_at=when,
                                    outreach_status="contacted", qualification="REJECT",
                                    reason="historical: already emailed from Gmail")
        finally:
            imap.logout()


def fetch_airtable(api_key: str, base_id: str, table: str) -> list[dict]:
    records, offset = [], ""
    with httpx.Client(timeout=60) as client:
        while True:
            url = f"https://api.airtable.com/v0/{base_id}/{urllib.parse.quote(table)}?pageSize=100" + (f"&offset={offset}" if offset else "")
            resp = client.get(url, headers={"Authorization": f"Bearer {api_key}"})
            resp.raise_for_status()
            data = resp.json()
            records += data.get("records", [])
            offset = data.get("offset", "")
            if not offset:
                return records


def airtable_env() -> dict:
    """Airtable creds: process env first, then the leadgen-pipeline .env (never printed)."""
    keys = ("AIRTABLE_API_KEY", "AIRTABLE_BASE_ID", "AIRTABLE_TABLE_NAME")
    env = {k: os.environ.get(k, "") for k in keys}
    if not all(env.values()):
        for candidate in (Path.home() / "AI-Second-Brain" / "leadgen-pipeline" / ".env",):
            if candidate.exists():
                for line in candidate.read_text(encoding="utf-8").splitlines():
                    if "=" in line and not line.lstrip().startswith("#"):
                        k, v = line.split("=", 1)
                        if k.strip() in keys and not env.get(k.strip()):
                            env[k.strip()] = v.strip().strip('"')
    return env
