"""Google Reviews / Maps campaign on the existing lead and sender stack.

The database remains the canonical company/contact ledger.  This module only
adds campaign-specific scoring plus a staging area for Claude Free-authored
copy.  The normal send guard, queue, provider confirmation, reply handling,
suppression, and Airtable mirror remain authoritative.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import yaml

from cloudos.config import REPO_ROOT

CAMPAIGN_PATH = REPO_ROOT / "config" / "campaigns" / "google_reviews_maps_299.yaml"
CAMPAIGN = "google_reviews_maps_299"
COPY_VERSION = "claude-review-v1"
URL = re.compile(r"https?://|www\.", re.I)
OLD_OFFER = re.compile(
    r"missed[- ]?call|missed callers?|quote follow[- ]?up|\$497|\$1,?500|5 recovered|5 missed|"
    r"text[- ]?back|booking link|office-agent-demo",
    re.I,
)
GENERIC = re.compile(
    r"\bwe help businesses\b|\bunlock\b|\bleverage\b|\brevolutioni[sz]e\b|\bcutting[- ]edge\b|"
    r"\bgame[- ]changer\b|\bsynerg\w*\b|\bboost your online presence\b",
    re.I,
)
UNSUPPORTED = re.compile(
    r"\bodds are\b|\bjust (?:have not|haven't) been asked\b|\bmust not be asking\b|"
    r"\bprobably because\b|\busually comes? down to\b|\bgoogle penalizes\b|"
    r"\bguarantee(?:d|s)?\b|\bwill rank\b|\btop[- ]?3\b",
    re.I,
)
REVIEW_GATING = re.compile(
    r"\b(?:happy|satisfied) (?:customer|client|patient)s?\b|"
    r"\bonly (?:ask|request|invite)\w*\b.{0,30}\b(?:happy|satisfied)\b",
    re.I | re.S,
)


def load_config(path: Path = CAMPAIGN_PATH) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _facts(row: dict) -> dict:
    value = row.get("personalization") or {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = {}
    return value if isinstance(value, dict) else {}


def _number(value, cast):
    try:
        return cast(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class Score:
    value: int
    strongest_signal: str
    exact_evidence: str
    competitor_context: str
    email_hook: str
    source: str


def score(row: dict, competitor: dict | None = None) -> Score:
    """Score only facts already captured from public Google Maps listings."""
    facts = _facts(row)
    rating = _number(facts.get("google_rating"), float)
    reviews = _number(facts.get("google_reviews"), int)
    if rating is None or reviews is None:
        return Score(0, "missing Google Maps evidence", "", "", "", row.get("discovery_source") or "")

    points = 0
    signals: list[tuple[int, str]] = []
    research = facts.get("review_research") if isinstance(facts.get("review_research"), dict) else facts
    negatives = _number(research.get("recent_negative_reviews"), int) or 0
    unanswered = _number(research.get("recent_unanswered_negative_reviews"), int) or 0
    if rating <= 4.0:
        points += 4; signals.append((4, f"{rating:g}-star rating"))
    elif rating <= 4.3:
        points += 3; signals.append((3, f"{rating:g}-star rating"))
    elif rating <= 4.6:
        points += 2; signals.append((2, f"{rating:g}-star rating"))
    elif rating <= 4.8:
        points += 1; signals.append((1, f"{rating:g}-star rating"))

    if reviews < 20:
        points += 4; signals.append((4, f"only {reviews} Google reviews"))
    elif reviews < 50:
        points += 3; signals.append((3, f"only {reviews} Google reviews"))
    elif reviews < 100:
        points += 2; signals.append((2, f"{reviews} Google reviews"))
    elif reviews < 200:
        points += 1; signals.append((1, f"{reviews} Google reviews"))

    if unanswered:
        points += 2; signals.append((2, f"{unanswered} sampled recent negative review(s) without an owner response"))
    elif negatives:
        points += 1; signals.append((1, f"{negatives} sampled recent negative review(s)"))

    competitor_context = ""
    if competitor:
        cf = _facts(competitor)
        c_reviews = _number(cf.get("google_reviews"), int)
        c_rating = _number(cf.get("google_rating"), float)
        if c_reviews and c_reviews > reviews:
            ratio = reviews / c_reviews
            gap_points = 5 if ratio <= 0.2 else 4 if ratio <= 0.45 else 3 if ratio <= 0.7 else 2 if ratio <= 0.9 else 0
            if gap_points:
                points += gap_points
                signals.append((gap_points, f"local competitor has {c_reviews} reviews"))
                competitor_context = (
                    f"{competitor['company_name']} in {competitor.get('city') or row.get('city')} has "
                    f"{c_reviews} Google reviews" + (f" at {c_rating:g} stars" if c_rating else "") + "."
                )

    points = min(points, 10)
    strongest = max(signals, default=(0, "Google Maps profile opportunity"))[1]
    exact = f"Google Maps shows {rating:g} stars from {reviews} reviews."
    if unanswered:
        exact += f" The sampled review feed includes {unanswered} negative review(s) without an owner response."
    if unanswered:
        hook = (f"I noticed {row['company_name']} has {reviews} Google reviews at {rating:g} stars, and the sampled "
                f"review feed includes {unanswered} negative review(s) without an owner response.")
    elif competitor_context:
        hook = (f"I noticed {row['company_name']} has {reviews} Google reviews at {rating:g} stars, while "
                f"{competitor['company_name']} has {_facts(competitor).get('google_reviews')}.")
    elif reviews < 100:
        hook = f"I noticed {row['company_name']} has {reviews} Google reviews at {rating:g} stars."
    else:
        hook = f"I noticed {row['company_name']} is at {rating:g} stars on Google from {reviews} reviews."
    source = facts.get("google_profile_url") or facts.get("evidence_url") or row.get("website") or "Google Maps"
    return Score(points, strongest, exact, competitor_context, hook, source)


def _eligible_rows(conn, industries: list[str]) -> list[dict]:
    return conn.execute(
        """
        SELECT c.company_id::text, c.company_name, c.website, c.industry, c.city, c.state,
               c.discovery_source, c.personalization, ct.email
        FROM companies c
        JOIN LATERAL (
            SELECT email FROM contacts
            WHERE company_id = c.company_id AND email_status IN ('validated','published')
            ORDER BY (email_status = 'validated') DESC, (role = 'generic') DESC, discovered_at LIMIT 1
        ) ct ON true
        WHERE c.active AND c.first_contacted_at IS NULL
          AND c.industry = ANY(%s::text[])
          AND c.personalization ? 'google_reviews' AND c.personalization ? 'google_rating'
          AND c.outreach_status NOT IN ('contacted','replied','bounced','unsubscribed','do_not_contact')
          AND NOT EXISTS (
              SELECT 1 FROM email_suppressions s
              WHERE (s.email IS NOT NULL AND outreach_norm_email(s.email) = outreach_norm_email(ct.email))
                 OR (coalesce(s.domain, '') <> '' AND s.domain = split_part(lower(trim(ct.email)), '@', 2))
                 OR s.company_id = c.company_id)
          AND NOT EXISTS (
              SELECT 1 FROM outreach_history h
              WHERE h.status = 'sent' AND (h.company_id = c.company_id OR outreach_norm_email(h.email) = outreach_norm_email(ct.email)))
          AND NOT EXISTS (
              SELECT 1 FROM outreach_messages m
              WHERE m.direction = 'outbound' AND (m.company_id = c.company_id OR outreach_norm_email(m.recipient) = outreach_norm_email(ct.email)))
        """, (industries,)).fetchall()


def _peer_rows(conn, industries: list[str]) -> list[dict]:
    """All locally comparable map listings, including those without an email.
    A competitor is market evidence, not an outreach recipient."""
    return conn.execute(
        """
        SELECT company_id::text, company_name, industry, city, state, personalization
        FROM companies
        WHERE active AND industry = ANY(%s::text[])
          AND qualification_status <> 'REJECT'
          AND personalization ? 'google_reviews' AND personalization ? 'google_rating'
        """, (industries,),
    ).fetchall()


def qualify(conn, cfg: dict | None = None) -> dict:
    cfg = cfg or load_config()
    selection = cfg["selection"]
    industries = list(selection["industry_priority"])
    rows = [dict(r) for r in _eligible_rows(conn, industries)]
    by_market: dict[tuple[str, str], list[dict]] = {}
    for row in (dict(r) for r in _peer_rows(conn, industries)):
        by_market.setdefault((row["industry"], (row.get("city") or "").lower()), []).append(row)
    scored = []
    for row in rows:
        peers = [p for p in by_market.get((row["industry"], (row.get("city") or "").lower()), [])
                 if p["company_id"] != row["company_id"]]
        competitor = max(peers, key=lambda p: _number(_facts(p).get("google_reviews"), int) or -1, default=None)
        scored.append((row, score(row, competitor)))
    order = {industry: n for n, industry in enumerate(industries)}
    scored.sort(key=lambda item: (-item[1].value, order.get(item[0]["industry"], 999), item[0]["company_name"].lower()))
    minimum = int(selection["minimum_score"])
    chosen = [(row, s) for row, s in scored if s.value >= minimum][: int(selection["target"])]
    chosen_ids = {row["company_id"] for row, _ in chosen}
    for row, s in scored:
        payload = {
            "campaign": CAMPAIGN, "selected": row["company_id"] in chosen_ids, "score": s.value,
            "strongest_signal": s.strongest_signal, "exact_evidence": s.exact_evidence,
            "competitor_context": s.competitor_context, "email_hook": s.email_hook, "source": s.source,
        }
        conn.execute(
            "UPDATE companies SET personalization = jsonb_set(personalization, '{review_campaign}', %s::jsonb, true), "
            "qualification_status = CASE WHEN %s >= 8 THEN 'HIGH' WHEN %s >= %s THEN 'MEDIUM' ELSE qualification_status END, "
            "outreach_status = CASE WHEN %s THEN 'outreach_ready' ELSE outreach_status END, updated_at = now() "
            "WHERE company_id = %s::uuid",
            (json.dumps(payload), s.value, s.value, minimum, row["company_id"] in chosen_ids, row["company_id"]),
        )
    conn.commit()
    return {
        "raw_eligible": len(rows), "strong_qualified": sum(1 for _, s in scored if s.value >= minimum),
        "selected": len(chosen), "by_industry": {i: sum(1 for r, _ in chosen if r["industry"] == i) for i in industries},
    }


def selected(conn) -> list[dict]:
    rows = conn.execute(
        """
        SELECT c.company_id::text, c.company_name, c.industry, c.city, c.state, c.website,
               c.personalization->'review_campaign' campaign_data, ct.email
        FROM companies c
        JOIN LATERAL (
            SELECT email FROM contacts WHERE company_id = c.company_id AND email_status IN ('validated','published')
            ORDER BY (email_status = 'validated') DESC, (role = 'generic') DESC, discovered_at LIMIT 1
        ) ct ON true
        WHERE c.personalization->'review_campaign'->>'campaign' = %s
          AND (c.personalization->'review_campaign'->>'selected')::boolean
          AND c.first_contacted_at IS NULL AND c.active
        ORDER BY (c.personalization->'review_campaign'->>'score')::int DESC, c.company_name
        """, (CAMPAIGN,)).fetchall()
    out = []
    for r in rows:
        d = dict(r); campaign_data = d.pop("campaign_data") or {}
        out.append({
            "company_id": d["company_id"], "company": d["company_name"], "first_name": "",
            "email": d["email"], "vertical": d["industry"], "city": d["city"], "state": d["state"],
            "score": campaign_data.get("score"), "strongest_signal": campaign_data.get("strongest_signal"),
            "exact_evidence": campaign_data.get("exact_evidence"),
            "competitor_context": campaign_data.get("competitor_context"),
            "email_hook": campaign_data.get("email_hook"), "source": campaign_data.get("source"),
        })
    return out


def qa_copy(subject: str, body: str, lead: dict, *, cfg: dict | None = None) -> list[str]:
    cfg = cfg or load_config()
    problems: list[str] = []
    subject, body = subject.strip(), body.strip()
    if not subject:
        problems.append("empty subject")
    if len(subject.split()) > int(cfg["copy"]["subject_max_words"]):
        problems.append("subject too long")
    if len(body.split()) > int(cfg["copy"]["first_touch_max_words"]):
        problems.append("over 80 words before footer")
    if URL.search(subject + "\n" + body):
        problems.append("link in first touch")
    if OLD_OFFER.search(subject + "\n" + body):
        problems.append("old offer language")
    if GENERIC.search(body):
        problems.append("generic marketing language")
    if UNSUPPORTED.search(body):
        problems.append("unsupported speculative claim")
    if REVIEW_GATING.search(body):
        problems.append("review gating language")
    if body.count("?") != 1:
        problems.append("must contain exactly one CTA question")
    if not re.search(r"\b(send|sent|share)\b.{0,35}\b(video|breakdown)\b|\b(video|breakdown)\b.{0,35}\b(send|sent|share)\b", body, re.I | re.S):
        problems.append("CTA does not ask permission to send the video")
    if not re.search(r"(?m)^Matt\s*$", body):
        problems.append("not signed as Matt")
    evidence = " ".join(filter(None, [lead.get("exact_evidence"), lead.get("competitor_context")]))
    evidence_numbers = set(re.findall(r"\b\d+(?:\.\d+)?\b", evidence))
    if evidence_numbers and not any(re.search(rf"\b{re.escape(n)}\b", body) for n in evidence_numbers):
        problems.append("no verifiable evidence from the lead record")
    allowed_numbers = evidence_numbers | set(re.findall(r"\b\d+(?:\.\d+)?\b", lead.get("company") or ""))
    body_numbers = set(re.findall(r"\b\d+(?:\.\d+)?\b", body))
    if body_numbers - allowed_numbers:
        problems.append("unsupported numeric claim")
    company = (lead.get("company") or "").strip()
    company_tokens = re.findall(r"[a-z0-9]+", company.casefold())
    body_normalized = " ".join(re.findall(r"[a-z0-9]+", body.casefold()))
    # Claude may reasonably omit a legal suffix or shorten a long trading name,
    # but the first distinctive part must still identify the intended business.
    company_anchor = " ".join(company_tokens[: min(2, len(company_tokens))])
    if company_anchor and company_anchor not in body_normalized:
        problems.append("company name missing from body")
    return problems


def qa_followup(subject: str, body: str, *, cfg: dict | None = None) -> list[str]:
    cfg = cfg or load_config()
    problems: list[str] = []
    if not subject.strip():
        problems.append("empty subject")
    if len(body.split()) > int(cfg["copy"]["first_touch_max_words"]):
        problems.append("follow-up over 80 words before footer")
    if OLD_OFFER.search(subject + "\n" + body):
        problems.append("old offer language")
    if GENERIC.search(body):
        problems.append("generic marketing language")
    if body.count("?") > 1:
        problems.append("multiple CTA questions")
    if not re.search(r"(?m)^Matt\s*$", body):
        problems.append("not signed as Matt")
    return problems


def _fingerprint(body: str, lead: dict) -> str:
    value = re.sub(r"\W+", " ", body.lower())
    for item in (lead["company"], lead.get("city") or "", lead.get("state") or ""):
        value = value.replace(item.lower(), " ")
    value = re.sub(r"\b\d+(?:\.\d+)?\b", "#", value)
    return " ".join(value.split())


def _records(value: str) -> list[dict]:
    value = value.strip()
    if not value:
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else [parsed]
    except json.JSONDecodeError:
        return [json.loads(line) for line in value.splitlines() if line.strip()]


def import_copy(conn, text: str) -> dict:
    leads = {lead["company_id"]: lead for lead in selected(conn)}
    out = {"received": 0, "stored": 0, "failed": 0, "failures": {}}
    normalized: dict[str, str] = {}
    existing = conn.execute(
        "SELECT company_id::text, body FROM outreach_campaign_copy WHERE campaign=%s AND step=0 AND qa_passed_at IS NOT NULL",
        (CAMPAIGN,),
    ).fetchall()
    for row in existing:
        if row["company_id"] in leads:
            normalized[_fingerprint(row["body"], leads[row["company_id"]])] = row["company_id"]
    for item in _records(text):
        out["received"] += 1
        cid = str(item.get("company_id") or "")
        lead = leads.get(cid)
        if not lead:
            out["failed"] += 1; out["failures"][cid or "missing_company_id"] = ["not a selected lead"]; continue
        step = int(item.get("step") or 0)
        if step not in (0, 1, 2, 3):
            out["failed"] += 1; out["failures"][f"{cid}:step"] = ["step must be 0, 1, 2 or 3"]; continue
        subject, body = str(item.get("subject") or "").strip(), str(item.get("body") or "").strip()
        problems = qa_copy(subject, body, lead) if step == 0 else qa_followup(subject, body)
        if step == 0:
            fingerprint = _fingerprint(body, lead)
            if fingerprint in normalized and normalized[fingerprint] != cid:
                problems.append("simple mail-merge duplicate")
            normalized[fingerprint] = cid
        conn.execute(
            """
            INSERT INTO outreach_campaign_copy
                (company_id, campaign, step, subject, body, copy_version, evidence, qa_problems, qa_passed_at)
            VALUES (%s::uuid,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,CASE WHEN %s THEN now() ELSE NULL END)
            ON CONFLICT (company_id, campaign, step) DO UPDATE SET subject=EXCLUDED.subject, body=EXCLUDED.body,
                copy_version=EXCLUDED.copy_version, evidence=EXCLUDED.evidence, qa_problems=EXCLUDED.qa_problems,
                qa_passed_at=EXCLUDED.qa_passed_at, updated_at=now()
            """, (cid, CAMPAIGN, step, subject, body, COPY_VERSION, json.dumps(lead), json.dumps(problems), not problems),
        )
        if problems:
            out["failed"] += 1; out["failures"][f"{cid}:{step}"] = problems
        else:
            out["stored"] += 1
    conn.commit()
    return out


def add_footer(body: str, postal_address: str, cfg: dict | None = None) -> str:
    cfg = cfg or load_config()
    return "\n".join([body.strip(), "", "--", postal_address.strip(), cfg["copy"]["footer_opt_out"]])


def approved_count(conn) -> int:
    return conn.execute(
        "SELECT count(*) n FROM outreach_campaign_copy WHERE campaign=%s AND step=0 AND qa_passed_at IS NOT NULL",
        (CAMPAIGN,),
    ).fetchone()["n"]


def queue_approved(conn, *, postal_address: str, limit: int | None = None) -> dict:
    if not postal_address.strip():
        return {"queued": 0, "stopped": "SENDER_POSTAL_ADDRESS is not set - nothing prepared"}
    rows = conn.execute(
        """
        SELECT cc.*, ct.email recipient, c.company_name
        FROM outreach_campaign_copy cc JOIN companies c USING (company_id)
        JOIN LATERAL (
            SELECT email FROM contacts WHERE company_id=c.company_id AND email_status IN ('validated','published')
            ORDER BY (email_status='validated') DESC, (role='generic') DESC, discovered_at LIMIT 1
        ) ct ON true
        WHERE cc.campaign=%s AND cc.step=0 AND cc.qa_passed_at IS NOT NULL
          AND c.first_contacted_at IS NULL AND c.active
          AND NOT EXISTS (SELECT 1 FROM outreach_queue q WHERE q.company_id=cc.company_id)
        ORDER BY (cc.evidence->>'score')::int DESC NULLS LAST, cc.updated_at
        LIMIT %s
        """, (CAMPAIGN, limit or 1000000)).fetchall()
    out = {"queued": 0, "blocked": 0}
    from cloudos.conversations import guard
    from cloudos.outreach import copy as copywriter
    for row in rows:
        verdict = guard.check_fail_closed(conn, row["recipient"], "cold", str(row["company_id"]))
        if verdict.get("allowed") is not True:
            out["blocked"] += 1
            continue
        body = add_footer(row["body"], postal_address)
        problems = copywriter.qa(copywriter.Email(row["subject"], body, row["copy_version"]),
                                 postal_address=postal_address, company_name=row["company_name"])
        if problems:
            out["blocked"] += 1
            continue
        cur = conn.execute(
            """
            INSERT INTO outreach_queue
                (company_id, step, recipient, subject, body, copy_variant, state, campaign, copy_source, evidence)
            VALUES (%s,0,%s,%s,%s,%s,'queued',%s,%s,%s)
            ON CONFLICT (company_id,step) DO NOTHING
            """, (row["company_id"], row["recipient"], row["subject"], body, row["copy_version"],
                   CAMPAIGN, COPY_VERSION, json.dumps(row["evidence"] or {}))).rowcount
        out["queued"] += int(bool(cur))
    conn.commit()
    return out


def status(conn) -> dict:
    one = lambda sql, args=(): conn.execute(sql, args).fetchone()["n"]  # noqa: E731
    return {
        "campaign": CAMPAIGN,
        "selected": one("SELECT count(*) n FROM companies WHERE personalization->'review_campaign'->>'campaign'=%s "
                        "AND (personalization->'review_campaign'->>'selected')::boolean", (CAMPAIGN,)),
        "claude_written": one("SELECT count(*) n FROM outreach_campaign_copy WHERE campaign=%s AND step=0", (CAMPAIGN,)),
        "qa_passed": approved_count(conn),
        "ready_to_send": one("SELECT count(*) n FROM outreach_queue WHERE campaign=%s AND step=0 AND state='queued'", (CAMPAIGN,)),
        "sent": one("SELECT count(*) n FROM outreach_queue WHERE campaign=%s AND step=0 AND state='sent'", (CAMPAIGN,)),
    }

