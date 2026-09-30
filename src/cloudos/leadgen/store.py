"""Postgres access for the lead engine. All dedupe decisions go through find_existing()."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from cloudos.leadgen.normalize import (
    normalize_domain,
    normalize_email,
    normalize_name,
    normalize_phone,
    normalize_state,
)

CONTACTED_STATUSES = ("contacted", "replied", "bounced", "unsubscribed", "do_not_contact")


@dataclass
class Candidate:
    name: str
    website: str = ""
    industry: str = ""
    city: str = ""
    state: str = ""
    phone: str = ""
    source: str = ""
    query: str = ""
    listing_emails: list[str] = field(default_factory=list)
    review_count: int | None = None
    rating: float | None = None
    brand_tag: str = ""
    extra: dict = field(default_factory=dict)
    historical_ids: list[str] = field(default_factory=list)

    @property
    def normalized_domain(self) -> str:
        return normalize_domain(self.website)

    @property
    def normalized_name(self) -> str:
        return normalize_name(self.name)


@dataclass
class Match:
    company_id: str
    key: str          # which dedupe key matched
    outreach_status: str


def find_existing(conn, cand: Candidate, emails: list[str] | None = None) -> Match | None:
    """Return the company this candidate already is, or None if genuinely new."""
    checks: list[tuple[str, str, tuple]] = []
    dom = cand.normalized_domain
    if dom:
        checks.append(("domain", "SELECT company_id, outreach_status FROM companies WHERE normalized_domain = %s", (dom,)))
    name = cand.normalized_name
    if name and cand.city:
        checks.append(("name+location",
                       "SELECT company_id, outreach_status FROM companies WHERE normalized_name = %s "
                       "AND lower(coalesce(city,'')) = lower(%s) AND lower(coalesce(state,'')) = lower(%s)",
                       (name, cand.city, normalize_state(cand.state))))
    if cand.historical_ids:
        checks.append(("historical_id", "SELECT company_id, outreach_status FROM companies WHERE historical_ids && %s",
                       (list(cand.historical_ids),)))
    phone = normalize_phone(cand.phone)
    if phone:
        checks.append(("phone", "SELECT company_id, outreach_status FROM companies WHERE normalized_phone = %s", (phone,)))
    for email in [normalize_email(e) for e in (emails or [])]:
        if email:
            checks.append(("email", "SELECT c.company_id, c.outreach_status FROM contacts ct JOIN companies c USING (company_id) "
                                    "WHERE lower(ct.email) = %s", (email,)))
    for key, sql, params in checks:
        row = conn.execute(sql + " LIMIT 1", params).fetchone()
        if row:
            return Match(str(row["company_id"]), key, row["outreach_status"])
    return None


def insert_company(conn, cand: Candidate, *, qualification: str, reason: str, outreach_status: str,
                   personalization: dict, is_historical: bool = False, enriched: bool = False,
                   first_contacted_at=None, last_contacted_at=None) -> str:
    row = conn.execute(
        """
        INSERT INTO companies (company_name, normalized_name, domain, normalized_domain, website, industry,
            city, state, phone, normalized_phone, discovery_source, qualification_status, qualification_reason,
            personalization, outreach_status, is_historical, historical_ids, enriched_at,
            first_contacted_at, last_contacted_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s)
        RETURNING company_id
        """,
        (
            cand.name.strip()[:200], cand.normalized_name or cand.name.lower()[:200],
            cand.normalized_domain.split("/")[0] or None, cand.normalized_domain or None,
            cand.website or None, cand.industry or None, cand.city or None, normalize_state(cand.state) or None,
            cand.phone or None, normalize_phone(cand.phone) or None, cand.source, qualification, reason,
            json.dumps(personalization, default=str), outreach_status, is_historical, list(cand.historical_ids),
            datetime.now(timezone.utc) if enriched else None, first_contacted_at, last_contacted_at,
        ),
    ).fetchone()
    return str(row["company_id"])


def add_contact(conn, company_id: str, email: str, status: str, *, role: str, source: str, source_url: str = "") -> bool:
    email = normalize_email(email)
    if not email:
        return False
    cur = conn.execute(
        "INSERT INTO contacts (company_id, email, email_status, role, source, source_url) VALUES (%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (lower(email)) DO NOTHING",
        (company_id, email, status, role, source, source_url or None),
    )
    return cur.rowcount == 1


def log_discovery(conn, source: str, query: str, company_id: str | None, outcome: str, detail: str = "") -> None:
    conn.execute(
        "INSERT INTO discovery_history (source, query, company_id, outcome, detail) VALUES (%s,%s,%s,%s,%s)",
        (source, query, company_id, outcome, detail[:500] or None),
    )


def record_outreach(conn, company_id: str | None, email: str, campaign: str, sent_at, status: str,
                    provider: str, dedupe_key: str) -> bool:
    cur = conn.execute(
        "INSERT INTO outreach_history (company_id, email, campaign, sent_at, status, provider, dedupe_key) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (dedupe_key) DO NOTHING",
        (company_id, normalize_email(email) or email, campaign, sent_at, status, provider, dedupe_key),
    )
    return cur.rowcount == 1


# --- query rotation -------------------------------------------------------------

FEEDBACK_RANK = {"increase_priority": 0, "hold": 1, "hold_many_replies_low_intent": 1, "insufficient_data": 1,
                 "decrease_priority": 2}


def order_by_feedback(conn, candidates: list[dict]) -> list[dict]:
    """Reply-layer feedback (cloudos.conversations.reports.feedback) reorders industries only.

    Stable sort: industries without enough evidence (the default) keep their
    config order. Never changes how many companies are found. Any failure ->
    original order, so discovery never depends on the reply layer.
    """
    try:
        from cloudos.conversations.reports import feedback
        conn.execute("SAVEPOINT lead_feedback")
        prio = feedback(conn)["lead_engine_priority"]
        conn.execute("RELEASE SAVEPOINT lead_feedback")
    except Exception:
        try:
            conn.execute("ROLLBACK TO SAVEPOINT lead_feedback")
        except Exception:
            pass
        return candidates
    return sorted(candidates, key=lambda c: FEEDBACK_RANK.get(prio.get(c.get("industry") or "", "insufficient_data"), 1))

def next_queries(conn, source: str, candidates: list[dict], limit: int, repeat_after_days: int = 30) -> list[dict]:
    """Pick never-run combinations first (in priority order), then the stalest
    combos older than ``repeat_after_days`` that historically produced new companies."""
    candidates = order_by_feedback(conn, candidates)
    rows = conn.execute("SELECT query, last_run_at, total_new, runs, last_error FROM lead_queries WHERE source = %s",
                        (source,)).fetchall()
    ran = {r["query"]: r for r in rows if not r["last_error"]}  # errored queries get retried
    fresh = [c for c in candidates if c["query"] not in ran]
    if len(fresh) >= limit:
        return fresh[:limit]
    cutoff = datetime.now(timezone.utc) - timedelta(days=repeat_after_days)
    stale = [c for c in candidates if c["query"] in ran and ran[c["query"]]["last_run_at"] and ran[c["query"]]["last_run_at"] < cutoff
             and (ran[c["query"]]["total_new"] or 0) > 0]
    stale.sort(key=lambda c: ran[c["query"]]["last_run_at"])
    return (fresh + stale)[:limit]


def mark_query(conn, source: str, q: dict, results: int, new: int, error: str = "") -> None:
    conn.execute(
        """
        INSERT INTO lead_queries (source, query, industry, location, runs, last_run_at, last_results, last_new,
                                  total_results, total_new, last_error)
        VALUES (%s,%s,%s,%s,1,now(),%s,%s,%s,%s,%s)
        ON CONFLICT (source, query) DO UPDATE SET runs = lead_queries.runs + 1, last_run_at = now(),
            last_results = EXCLUDED.last_results, last_new = EXCLUDED.last_new,
            total_results = lead_queries.total_results + EXCLUDED.last_results,
            total_new = lead_queries.total_new + EXCLUDED.last_new, last_error = EXCLUDED.last_error
        """,
        (source, q["query"], q.get("industry"), q.get("location"), results, new, results, new, error[:500] or None),
    )


# --- source health (self-healing) ----------------------------------------------

def source_available(conn, source: str) -> tuple[bool, str]:
    row = conn.execute("SELECT disabled_until, last_error FROM lead_source_health WHERE source = %s", (source,)).fetchone()
    if row and row["disabled_until"] and row["disabled_until"] > datetime.now(timezone.utc):
        return False, f"benched until {row['disabled_until']:%Y-%m-%d %H:%M} UTC after repeated failures: {row['last_error']}"
    return True, ""


def source_success(conn, source: str) -> None:
    conn.execute(
        """
        INSERT INTO lead_source_health (source, total_successes, last_success_at) VALUES (%s, 1, now())
        ON CONFLICT (source) DO UPDATE SET consecutive_failures = 0, disabled_until = NULL,
            total_successes = lead_source_health.total_successes + 1, last_success_at = now()
        """,
        (source,),
    )


def source_failure(conn, source: str, error: str, policy: dict) -> dict:
    row = conn.execute(
        """
        INSERT INTO lead_source_health (source, consecutive_failures, total_failures, last_failure_at, last_error)
        VALUES (%s, 1, 1, now(), %s)
        ON CONFLICT (source) DO UPDATE SET consecutive_failures = lead_source_health.consecutive_failures + 1,
            total_failures = lead_source_health.total_failures + 1, last_failure_at = now(), last_error = EXCLUDED.last_error
        RETURNING consecutive_failures
        """,
        (source, error[:500]),
    ).fetchone()
    n = row["consecutive_failures"]
    disabled_until = None
    if n >= int(policy.get("disable_after", 3)):
        minutes = min(int(policy.get("base_backoff_minutes", 15)) * 2 ** (n - int(policy.get("disable_after", 3))),
                      int(policy.get("max_backoff_minutes", 1440)))
        disabled_until = datetime.now(timezone.utc) + timedelta(minutes=minutes)
        conn.execute("UPDATE lead_source_health SET disabled_until = %s WHERE source = %s", (disabled_until, source))
    return {"consecutive_failures": n, "disabled_until": disabled_until}


# --- inventory ------------------------------------------------------------------

def inventory(conn) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT
          count(*)                                                        AS total_companies,
          count(*) FILTER (WHERE is_historical)                           AS historical_companies,
          count(*) FILTER (WHERE NOT is_historical)                       AS discovered_companies,
          count(*) FILTER (WHERE first_contacted_at IS NULL AND outreach_status <> ALL(%s)) AS uncontacted,
          count(*) FILTER (WHERE qualification_status IN ('HIGH','MEDIUM'))                AS qualified,
          count(*) FILTER (WHERE qualification_status IN ('HIGH','MEDIUM') AND NOT is_historical) AS qualified_new,
          count(*) FILTER (WHERE outreach_status = 'outreach_ready')      AS outreach_ready,
          count(*) FILTER (WHERE outreach_status = 'handed_off')          AS handed_off,
          count(*) FILTER (WHERE first_contacted_at IS NOT NULL OR outreach_status = ANY(%s)) AS contacted,
          count(*) FILTER (WHERE qualification_status = 'REJECT')         AS rejected,
          count(*) FILTER (WHERE qualification_status = 'LOW')            AS low
        FROM companies WHERE active
        """,
        (list(CONTACTED_STATUSES), list(CONTACTED_STATUSES)),
    ).fetchone()
    out = dict(row)
    # uncontacted, qualified, waiting to be sent (in our queue or in the sender's tray)
    out["available_inventory"] = out["outreach_ready"] + out["handed_off"]
    out["duplicates_rejected"] = conn.execute(
        "SELECT count(*) AS n FROM discovery_history WHERE outcome = 'duplicate'").fetchone()["n"]
    out["invalid_contacts"] = conn.execute(
        "SELECT count(*) AS n FROM contacts WHERE email_status = 'invalid'").fetchone()["n"]
    out["contacts"] = conn.execute("SELECT count(*) AS n FROM contacts").fetchone()["n"]
    out["domains_indexed"] = conn.execute(
        "SELECT count(*) AS n FROM companies WHERE normalized_domain IS NOT NULL").fetchone()["n"]
    return out


def found_today(conn, tz: str = "America/Chicago") -> int:
    """Companies that became READY since local midnight: qualified, a usable (mail-server-checked) email,
    never contacted, not a duplicate. This - not "discovered" - is what the daily 300 target counts.
    `ready_at` is stamped by the database the moment outreach_status first becomes outreach_ready (009)."""
    return conn.execute(
        "SELECT count(*) AS n FROM companies WHERE qualification_status IN ('HIGH','MEDIUM') "
        "AND ready_at >= (date_trunc('day', now() AT TIME ZONE %s) AT TIME ZONE %s)", (tz, tz)).fetchone()["n"]
