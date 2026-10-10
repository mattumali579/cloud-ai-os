"""Lead discovery and contact collection pipeline.

Prioritizes the backlog of unsent and incomplete leads in Postgres, finds new
small businesses via no-key permissible sources (OpenStreetMap / sitemaps / directories),
extracts public contact emails, deterministically qualifies them for the single
current offer ("Google Reviews + Google Maps visibility — $299/month"), persists
leads and contacts, manages checkpoints for crash recovery, prevents overlapping
runs, and stages outreach drafts for approval without unauthorized sending.
"""
from __future__ import annotations

import json
import logging
import os
import re
import socket
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin, urlparse

import httpx

from cloudos.config import REPO_ROOT
from cloudos.leadgen import store
from cloudos.leadgen.engine import load_config
from cloudos.leadgen.enrich import Enrichment, Fetcher, extract_facts, extract_emails, is_shared_host, mx_ok, rank_emails, _visible_text, _CONTACT_LINK, _SKIP_LINK, _PARKED
from cloudos.leadgen.normalize import normalize_email, normalize_state, registrable_domain
from cloudos.leadgen.qualify import qualify, Verdict
from cloudos.leadgen.sources import OSMSource, Candidate, SourceError
from cloudos.leadgen.store import find_existing, insert_company, add_contact, log_discovery

log = logging.getLogger("cloudos.leadgen.pipeline")

CHECKPOINT_PRIMARY = "primary_lead_pipeline"
LOCK_TIMEOUT_MINUTES = 15
DEFAULT_BATCH_SIZE = 20
OFFER_CAMPAIGN_ID = "google_reviews_maps_299"
OFFER_NAME = "Google Reviews + Google Maps visibility — $299/month"


@dataclass
class BatchReport:
    batch_id: str
    status: str
    started_at: str
    finished_at: str = ""
    duration_seconds: float = 0.0
    source_type: str = "backlog"
    checkpoint_before: str | None = None
    checkpoint_after: str | None = None
    businesses_found: int = 0
    sites_accessed: int = 0
    sites_failed: int = 0
    public_emails_found: int = 0
    unique_validated_emails: int = 0
    leads_stored: int = 0
    outreach_ready_added: int = 0
    drafts_prepared: int = 0
    duplicates_rejected: int = 0
    errors: list[str] = field(default_factory=list)
    samples: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _ensure_checkpoint_row(conn, checkpoint_id: str = CHECKPOINT_PRIMARY) -> dict:
    conn.execute(
        """
        INSERT INTO lead_engine_checkpoint (checkpoint_id, batch_type, is_running, items_processed, stats)
        VALUES (%s, 'backlog_and_discovery', false, 0, '{}'::jsonb)
        ON CONFLICT (checkpoint_id) DO NOTHING
        """,
        (checkpoint_id,),
    )
    row = conn.execute(
        "SELECT * FROM lead_engine_checkpoint WHERE checkpoint_id = %s", (checkpoint_id,)
    ).fetchone()
    return dict(row) if row else {}


def acquire_batch_lock(conn, checkpoint_id: str = CHECKPOINT_PRIMARY, timeout_minutes: int = LOCK_TIMEOUT_MINUTES) -> tuple[bool, str, dict]:
    """Acquire exclusive lock for lead engine batch run.

    Prevents overlapping runs. If a prior lock expired (> timeout_minutes), it is broken.
    """
    _ensure_checkpoint_row(conn, checkpoint_id)
    cur = conn.execute(
        """
        SELECT checkpoint_id, last_processed_id, is_running, locked_at, lock_token, items_processed, stats
        FROM lead_engine_checkpoint
        WHERE checkpoint_id = %s
        FOR UPDATE
        """,
        (checkpoint_id,),
    ).fetchone()
    if not cur:
        return False, "Checkpoint not found", {}

    row = dict(cur)
    is_running = row.get("is_running", False)
    locked_at = row.get("locked_at")

    now = datetime.now(timezone.utc)
    if is_running and locked_at:
        if now - locked_at < timedelta(minutes=timeout_minutes):
            return False, f"Job already in progress (locked at {locked_at.isoformat()})", row
        log.warning("Breaking stale lock on checkpoint %s from %s", checkpoint_id, locked_at)

    token = uuid.uuid4().hex
    conn.execute(
        """
        UPDATE lead_engine_checkpoint
        SET is_running = true, locked_at = now(), lock_token = %s, updated_at = now()
        WHERE checkpoint_id = %s
        """,
        (token, checkpoint_id),
    )
    conn.commit()
    return True, token, row


def release_batch_lock(
    conn,
    checkpoint_id: str = CHECKPOINT_PRIMARY,
    lock_token: str | None = None,
    last_processed_id: str | None = None,
    processed_count: int = 0,
    stats: dict | None = None,
) -> None:
    """Release batch lock and update checkpoint bookmark."""
    set_clauses = ["is_running = false", "locked_at = NULL", "lock_token = NULL", "updated_at = now()"]
    set_params: list[Any] = []

    if last_processed_id:
        set_clauses.append("last_processed_id = %s")
        set_params.append(last_processed_id)
    if processed_count > 0:
        set_clauses.append("items_processed = items_processed + %s")
        set_params.append(int(processed_count))
    if stats is not None:
        set_clauses.append("stats = %s::jsonb")
        set_params.append(json.dumps(stats, default=str))

    sql = f"UPDATE lead_engine_checkpoint SET {', '.join(set_clauses)} WHERE checkpoint_id = %s"
    where_params: list[Any] = [checkpoint_id]
    if lock_token:
        sql += " AND (lock_token = %s OR lock_token IS NULL)"
        where_params.append(lock_token)

    conn.execute(sql, tuple(set_params + where_params))
    conn.commit()


def get_checkpoint_status(conn, checkpoint_id: str = CHECKPOINT_PRIMARY) -> dict:
    _ensure_checkpoint_row(conn, checkpoint_id)
    row = conn.execute(
        "SELECT * FROM lead_engine_checkpoint WHERE checkpoint_id = %s", (checkpoint_id,)
    ).fetchone()
    return dict(row) if row else {}


def _discover_site_contact_pages(fetcher: Fetcher, final_url: str, html: str) -> list[str]:
    """Find contact, about, team, locations, and reach-us pages politely."""
    base_host = urlparse(final_url).hostname or ""
    links: list[str] = []
    
    # 1. Inspect hrefs in HTML
    for href in re.findall(r"href=[\"']([^\"'#]+)", html, re.I):
        absolute = urljoin(final_url, href)
        host = urlparse(absolute).hostname or ""
        if registrable_domain(host) == registrable_domain(base_host) and _CONTACT_LINK.search(absolute) and not _SKIP_LINK.search(absolute):
            if absolute not in links and absolute != final_url:
                links.append(absolute)

    # 2. Inspect anchor text e.g. <a ...>Contact Us</a>
    for m in re.finditer(r"<a[^>]+href=[\"']([^\"'#]+)[\"'][^>]*>(.*?)</a>", html, re.I | re.S):
        href, anchor_text = m.group(1), re.sub(r"<[^>]+>", " ", m.group(2)).strip()
        if re.search(r"contact|get in touch|about us|our team|locations?", anchor_text, re.I):
            absolute = urljoin(final_url, href)
            host = urlparse(absolute).hostname or ""
            if registrable_domain(host) == registrable_domain(base_host) and not _SKIP_LINK.search(absolute):
                if absolute not in links and absolute != final_url:
                    links.append(absolute)

    links.sort(key=lambda u: (0 if "contact" in u.lower() else 1, len(u)))
    return links[: max(0, fetcher.max_pages - 1)]


def enrich_with_sitemaps(website: str, fetcher: Fetcher, extra_emails: list[tuple[str, str]] | None = None) -> Enrichment:
    """Enhanced enrichment that checks homepage, discovered contact links, and sitemaps."""
    url = website if "://" in website else "http://" + website
    result = Enrichment(ok=False, website=website)

    try:
        home = fetcher.get(url)
    except httpx.HTTPError as exc:
        result.dead, result.error = True, f"unreachable: {type(exc).__name__}"
        return result

    if home is None:
        status = fetcher.last_status.get(url)
        if status in (401, 403, 429, 503) or status == "robots":
            result.blocked, result.error = True, f"site refuses automated reading ({status})"
            for email, src in extra_emails or []:
                norm = normalize_email(email)
                if norm:
                    st = "validated" if mx_ok(norm.split("@")[1]) else "invalid"
                    result.emails.append(store.Candidate.listing_emails if hasattr(store, "x") else None)
            return result
        result.dead, result.error = True, f"homepage not readable (status {status})"
        return result

    result.final_url = str(home.url)
    pages = [(str(home.url), home.text)]
    text = _visible_text(home.text)
    if _PARKED.search(text[:4000]) or len(text) < 80:
        result.parked, result.error = True, "parked or empty site"
        return result

    if not is_shared_host(result.final_url):
        contact_links = _discover_site_contact_pages(fetcher, result.final_url, home.text)
        
        # Sitemaps fallback if no contact links found in HTML
        if not contact_links:
            sitemap_url = urljoin(result.final_url, "/sitemap.xml")
            try:
                sitemap_resp = fetcher.get(sitemap_url)
                if sitemap_resp and sitemap_resp.status_code == 200:
                    for loc in re.findall(r"<loc>(https?://[^<]+)</loc>", sitemap_resp.text, re.I):
                        if _CONTACT_LINK.search(loc) and not _SKIP_LINK.search(loc) and loc not in contact_links:
                            contact_links.append(loc)
            except Exception:
                pass

        contact_links.sort(key=lambda u: (0 if "contact" in u.lower() else 1, len(u)))
        for link in contact_links[: max(0, fetcher.max_pages - 1)]:
            try:
                resp = fetcher.get(link)
                if resp is not None:
                    pages.append((str(resp.url), resp.text))
                    time.sleep(0.3)
            except httpx.HTTPError:
                continue

    result.pages_read = len(pages)
    site_domain = urlparse(result.final_url).hostname or ""
    seen: dict[str, str] = {}
    for page_url, page_html in pages:
        for email in extract_emails(page_html):
            seen.setdefault(email, page_url)
    for email, src in extra_emails or []:
        norm = normalize_email(email)
        if norm:
            seen.setdefault(norm, src)

    from cloudos.leadgen.enrich import EmailFinding
    for email, role in rank_emails(list(seen), site_domain):
        status = "validated" if mx_ok(email.split("@")[1]) else "invalid"
        result.emails.append(EmailFinding(email=email, status=status, source_url=seen[email], role=role))
    result.emails.sort(key=lambda f: 0 if f.status == "validated" else 1)

    all_text = " ".join(_visible_text(h) for _, h in pages)
    result.facts = extract_facts(home.text, all_text)
    result.facts["pages_read"] = len(pages)
    result.ok = True
    return result


def _stage_outreach_draft_for_approval(conn, company_row: dict, best_email: str, postal_addr: str) -> bool:
    """Deterministic draft staging for the single current offer.

    Prepares outreach draft with CAN-SPAM footer, no technology jargon,
    opt-out text, and stages in outreach_queue as 'queued' awaiting approval.
    Does NOT send.
    """
    if not best_email:
        return False
    cid = str(company_row["company_id"])
    company_name = company_row.get("company_name", "")
    industry = company_row.get("industry") or "local"
    facts = company_row.get("personalization") or {}
    if isinstance(facts, str):
        try:
            facts = json.loads(facts)
        except ValueError:
            facts = {}

    reviews = facts.get("google_reviews")
    rating = facts.get("google_rating")

    # Short personalized copy grounded in public Google profile evidence
    if reviews and rating:
        hook = f"I noticed {company_name} has {reviews} Google reviews at {rating:g} stars."
    elif reviews:
        hook = f"I noticed {company_name} has {reviews} Google reviews."
    else:
        hook = f"I was looking at {industry} businesses in {company_row.get('city') or 'your area'} and came across {company_name}."

    subject = f"Google Reviews for {company_name}"[:50]
    body = (
        f"Hi {company_name} team,\n\n"
        f"{hook} We help local service businesses get more Google reviews and increase their Google Maps visibility "
        f"so more nearby homeowners call you first.\n\n"
        f"Would you be open to me sharing a 2-minute video breakdown of your local visibility?\n\n"
        f"Matt\n\n"
        f"--\n"
        f"{postal_addr}\n"
        f"If you would rather not hear from me, reply \"stop\" and I will not email you again."
    )

    cur = conn.execute(
        """
        INSERT INTO outreach_queue
            (company_id, step, recipient, subject, body, copy_variant, state, campaign, copy_source, evidence)
        VALUES (%s, 0, %s, %s, %s, 'reviews_v1', 'queued', %s, 'deterministic_engine', %s::jsonb)
        ON CONFLICT (company_id, step) DO NOTHING
        """,
        (
            cid,
            best_email,
            subject,
            body,
            OFFER_CAMPAIGN_ID,
            json.dumps({"company": company_name, "reviews": reviews, "rating": rating}, default=str),
        ),
    )
    return cur.rowcount > 0


class LeadBatchPipeline:
    def __init__(self, conn_factory: Callable, cfg: dict | None = None):
        self.conn_factory = conn_factory
        self.cfg = cfg or load_config()

    def run_batch(self, batch_size: int = DEFAULT_BATCH_SIZE, force: bool = False) -> dict:
        """Executes one bounded batch: backlog priority, fallback to OSM discovery.

        Prevents overlapping jobs, records checkpoints, validates formats,
        stages drafts safely without sending.
        """
        batch_id = f"batch_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        started_at = datetime.now(timezone.utc).isoformat()
        t0 = time.time()

        with self.conn_factory() as conn:
            acquired, token, chk = acquire_batch_lock(conn)
            if not acquired:
                return {
                    "batch_id": batch_id,
                    "status": "skipped",
                    "reason": token,
                    "checkpoint": chk,
                    "started_at": started_at,
                    "duration_seconds": 0.0,
                }

        report = BatchReport(
            batch_id=batch_id,
            status="running",
            started_at=started_at,
            checkpoint_before=str(chk.get("last_processed_id") or ""),
        )

        last_pid = chk.get("last_processed_id")
        processed_candidates: list[dict] = []
        source_type = "backlog"

        fetcher = Fetcher(timeout=float(self.cfg.get("run", {}).get("http_timeout", 12)),
                          max_pages=int(self.cfg.get("run", {}).get("max_pages_per_site", 4)))

        sender_postal = os.environ.get("SENDER_POSTAL_ADDRESS", "Matt Umali, Baton Rouge, LA 70801").strip()

        try:
            with self.conn_factory() as conn:
                # Step 1: Query backlog of uncontacted, incomplete leads in companies table
                query_sql = """
                    SELECT company_id, company_name, domain, website, industry, city, state, phone,
                           personalization, outreach_status, qualification_status
                    FROM companies
                    WHERE active AND first_contacted_at IS NULL AND website IS NOT NULL
                      AND qualification_status <> 'REJECT'
                      AND (
                          outreach_status = 'not_ready'
                          OR enriched_at IS NULL
                          OR NOT EXISTS (SELECT 1 FROM contacts ct WHERE ct.company_id = companies.company_id)
                      )
                """
                params = []
                if last_pid:
                    query_sql_with_bookmark = query_sql + " AND company_id > %s ORDER BY company_id ASC LIMIT %s"
                    rows = conn.execute(query_sql_with_bookmark, (last_pid, batch_size)).fetchall()
                else:
                    rows = []

                if len(rows) < batch_size:
                    # Wrap around or start from beginning
                    needed = batch_size - len(rows)
                    if last_pid:
                        wrap_sql = query_sql + " AND company_id <= %s ORDER BY company_id ASC LIMIT %s"
                        wrap_rows = conn.execute(wrap_sql, (last_pid, needed)).fetchall()
                    else:
                        wrap_sql = query_sql + " ORDER BY company_id ASC LIMIT %s"
                        wrap_rows = conn.execute(wrap_sql, (batch_size,)).fetchall()
                    rows = list(rows) + list(wrap_rows)

                backlog_candidates = [dict(r) for r in rows]

            # Step 2: If backlog is empty, fall back to public OSM discovery
            if not backlog_candidates:
                source_type = "osm_discovery"
                osm = OSMSource()
                queries = osm.queries(self.cfg)
                with self.conn_factory() as conn:
                    batch_queries = store.next_queries(conn, "osm", queries, limit=2)
                for q in batch_queries:
                    try:
                        cands = osm.search(q, self.cfg)
                        for c in cands:
                            if c.website:
                                backlog_candidates.append({
                                    "company_name": c.name,
                                    "website": c.website,
                                    "industry": c.industry,
                                    "city": c.city,
                                    "state": c.state,
                                    "phone": c.phone,
                                    "listing_emails": c.listing_emails,
                                    "personalization": {},
                                    "is_new_candidate": True,
                                })
                        with self.conn_factory() as conn:
                            store.source_success(conn, "osm")
                            conn.commit()
                    except SourceError as e:
                        report.errors.append(f"OSM query failed: {e}")
                        with self.conn_factory() as conn:
                            store.source_failure(conn, "osm", str(e), self.cfg.get("failure_policy", {}))
                            conn.commit()
                    if len(backlog_candidates) >= batch_size:
                        break

            report.source_type = source_type
            report.businesses_found = len(backlog_candidates)

            # Step 3: Process and collect from each site
            latest_id = last_pid
            with self.conn_factory() as conn:
                for item in backlog_candidates[:batch_size]:
                    cid = item.get("company_id")
                    if cid:
                        latest_id = str(cid)
                    website = item.get("website", "").strip()
                    if not website:
                        continue

                    # Visit company site + contact pages
                    enr = enrich_with_sitemaps(website, fetcher, [(e, "public listing") for e in item.get("listing_emails", [])])
                    if enr.ok:
                        report.sites_accessed += 1
                    else:
                        report.sites_failed += 1
                        if enr.error:
                            report.errors.append(f"{website}: {enr.error}")

                    emails_found = len(enr.emails)
                    report.public_emails_found += emails_found
                    valid_emails = [f for f in enr.emails if f.status == "validated"]
                    report.unique_validated_emails += len(valid_emails)

                    # Deterministic qualification
                    verdict = qualify(
                        name=item.get("company_name", ""),
                        website=website,
                        industry=item.get("industry") or "local",
                        enrichment=enr,
                        chains=self.cfg.get("chains", []),
                        review_count=item.get("personalization", {}).get("google_reviews") if isinstance(item.get("personalization"), dict) else None,
                    )

                    is_new = item.get("is_new_candidate", False)
                    best_email = enr.best_email.email if enr.best_email else ""

                    if is_new:
                        cand = Candidate(
                            name=item.get("company_name", ""),
                            website=website,
                            industry=item.get("industry", ""),
                            city=item.get("city", ""),
                            state=item.get("state", ""),
                            phone=item.get("phone", ""),
                            source="osm",
                            query="osm discovery",
                        )
                        match = find_existing(conn, cand, [best_email] if best_email else [])
                        if match:
                            report.duplicates_rejected += 1
                            continue
                        ostatus = "outreach_ready" if verdict.outreach_ready else ("rejected" if verdict.level == "REJECT" else "not_ready")
                        new_id = insert_company(
                            conn, cand,
                            qualification=verdict.level,
                            reason=verdict.reason,
                            outreach_status=ostatus,
                            personalization=enr.facts,
                            enriched=True,
                        )
                        cid = new_id
                        latest_id = new_id
                        report.leads_stored += 1
                    else:
                        ostatus = "outreach_ready" if verdict.outreach_ready else ("rejected" if verdict.level == "REJECT" else "not_ready")
                        conn.execute(
                            """
                            UPDATE companies
                            SET qualification_status = %s,
                                qualification_reason = %s,
                                outreach_status = %s,
                                enriched_at = now(),
                                personalization = personalization || %s::jsonb,
                                updated_at = now()
                            WHERE company_id = %s
                            """,
                            (verdict.level, verdict.reason, ostatus, json.dumps(enr.facts, default=str), cid),
                        )
                        report.leads_stored += 1

                    # Save contacts
                    for f in enr.emails:
                        add_contact(conn, str(cid), f.email, f.status, role=f.role, source="engine_collector", source_url=f.source_url)

                    if ostatus == "outreach_ready":
                        report.outreach_ready_added += 1
                        # Stage draft for approval
                        if best_email and sender_postal:
                            staged = _stage_outreach_draft_for_approval(conn, item, best_email, sender_postal)
                            if staged:
                                report.drafts_prepared += 1

                    conn.commit()

                    report.samples.append({
                        "company_id": str(cid),
                        "name": item.get("company_name"),
                        "website": website,
                        "status": ostatus,
                        "qualification": verdict.level,
                        "best_email": best_email,
                        "emails_count": len(enr.emails),
                    })

            report.checkpoint_after = str(latest_id or "")
            report.status = "completed"

        except Exception as exc:
            report.status = "error"
            report.errors.append(f"Pipeline error: {type(exc).__name__}: {exc}")
            log.exception("Pipeline error during batch execution")
        finally:
            fetcher.close()
            report.duration_seconds = round(time.time() - t0, 2)
            report.finished_at = datetime.now(timezone.utc).isoformat()
            with self.conn_factory() as conn:
                release_batch_lock(
                    conn,
                    checkpoint_id=CHECKPOINT_PRIMARY,
                    lock_token=token,
                    last_processed_id=report.checkpoint_after or latest_id,
                    processed_count=report.leads_stored,
                    stats=report.to_dict(),
                )
                # Log in lead_runs
                conn.execute(
                    "INSERT INTO lead_runs (mode, finished_at, stats) VALUES ('pipeline_batch', now(), %s::jsonb)",
                    (json.dumps(report.to_dict(), default=str),),
                )
                conn.commit()

        return report.to_dict()
