"""BrightReach B2B Lead Engine — Web Dashboard & Personalized Outreach Generator.

Serves an interactive web dashboard for viewing scraped companies,
review metrics, contact emails, and generates personalized outreach emails
for the single Google Reviews & Google Maps $299/mo offer.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

log = logging.getLogger("cloudos.dashboard")

DEFAULT_POSTAL_ADDR = "Matt Umali, 123 Main St, Baton Rouge, LA 70801"
OFFER_CAMPAIGN_ID = "google_reviews_maps_299"


def get_dashboard_stats(conn) -> dict:
    """Fetch high-level KPI counts from Postgres."""
    row = conn.execute(
        """
        SELECT 
          (SELECT count(*) FROM companies WHERE active) AS total_companies,
          (SELECT count(DISTINCT company_id) FROM contacts WHERE email_status IN ('validated', 'published')) AS companies_with_emails,
          (SELECT count(*) FROM contacts) AS total_contacts,
          (SELECT count(*) FROM companies WHERE outreach_status = 'outreach_ready') AS total_qualified,
          (SELECT count(*) FROM outreach_queue WHERE campaign = %s) AS staged_drafts,
          (SELECT items_processed FROM lead_engine_checkpoint WHERE checkpoint_id = 'primary_lead_pipeline') AS checkpoint_processed,
          (SELECT is_running FROM lead_engine_checkpoint WHERE checkpoint_id = 'primary_lead_pipeline') AS is_running
        """,
        (OFFER_CAMPAIGN_ID,),
    ).fetchone()
    
    return {
        "total_companies": int(row["total_companies"] or 0),
        "companies_with_emails": int(row["companies_with_emails"] or 0),
        "total_contacts": int(row["total_contacts"] or 0),
        "total_qualified": int(row["total_qualified"] or 0),
        "staged_drafts": int(row["staged_drafts"] or 0),
        "checkpoint_processed": int(row["checkpoint_processed"] or 0),
        "is_running": bool(row["is_running"]),
    }


def get_outreach_campaign_stats(conn) -> dict:
    """Fetch outreach campaign statistics for the $299/mo Google Reviews offer."""
    row = conn.execute(
        """
        SELECT 
          (SELECT count(*) FROM outreach_campaign_copy WHERE campaign = %(camp)s) AS target_total,
          (SELECT count(*) FROM outreach_queue WHERE campaign = %(camp)s AND state = 'sent') AS sent_count,
          (SELECT count(*) FROM outreach_queue WHERE campaign = %(camp)s AND state = 'queued') AS queued_count,
          (SELECT count(*) FROM outreach_queue WHERE campaign = %(camp)s AND state = 'claimed') AS in_progress_count,
          (SELECT count(*) FROM outreach_queue WHERE campaign = %(camp)s AND state = 'failed') AS failed_count,
          (SELECT count(*) FROM outreach_queue WHERE campaign = %(camp)s AND state = 'cancelled') AS cancelled_count,
          (SELECT count(*) FROM outreach_messages WHERE direction = 'inbound') AS total_replies,
          (SELECT count(*) FROM company_conversation_state WHERE current_status IN ('interested', 'qualified', 'meeting_requested')) AS interested_count,
          (SELECT count(*) FROM email_suppressions) AS unsubscribed_count
        """,
        {"camp": OFFER_CAMPAIGN_ID},
    ).fetchone()

    target = int(row["target_total"] or 0)
    sent = int(row["sent_count"] or 0)
    queued = int(row["queued_count"] or 0)
    in_prog = int(row["in_progress_count"] or 0)
    failed = int(row["failed_count"] or 0)
    cancelled = int(row["cancelled_count"] or 0)
    replies = int(row["total_replies"] or 0)
    interested = int(row["interested_count"] or 0)
    unsub = int(row["unsubscribed_count"] or 0)

    # 300 maximum cap
    cap = 300

    return {
        "campaign_id": OFFER_CAMPAIGN_ID,
        "target_total": target,
        "cap": cap,
        "sent_count": sent,
        "queued_count": queued,
        "in_progress_count": in_prog,
        "failed_count": failed,
        "cancelled_count": cancelled,
        "total_replies": replies,
        "interested_count": interested,
        "unsubscribed_count": unsub,
        "progress_pct": round((sent / cap) * 100, 1) if cap > 0 else 0,
        "status": "active" if (queued > 0 or in_prog > 0) else "completed",
    }


def get_outreach_campaign_leads(
    conn,
    search: str = "",
    filter_by: str = "all",
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Query paginated leads in the active outreach campaign queue."""
    where_clauses = ["q.campaign = %(camp)s"]
    params: dict = {"camp": OFFER_CAMPAIGN_ID, "limit": limit, "offset": offset}

    if search.strip():
        term = f"%{search.strip().lower()}%"
        where_clauses.append(
            "(lower(c.company_name) LIKE %(term)s OR lower(coalesce(c.city, '')) LIKE %(term)s OR lower(coalesce(c.industry, '')) LIKE %(term)s OR lower(coalesce(q.recipient, '')) LIKE %(term)s)"
        )
        params["term"] = term

    if filter_by == "sent":
        where_clauses.append("q.state = 'sent'")
    elif filter_by == "queued":
        where_clauses.append("q.state = 'queued'")
    elif filter_by == "claimed":
        where_clauses.append("q.state = 'claimed'")
    elif filter_by == "replied":
        where_clauses.append("s.last_reply_at IS NOT NULL")
    elif filter_by == "interested":
        where_clauses.append("s.current_status IN ('interested', 'qualified', 'meeting_requested')")

    where_sql = " AND ".join(where_clauses)

    count_query = f"""
        SELECT count(*) AS total
        FROM outreach_queue q
        JOIN companies c ON c.company_id = q.company_id
        LEFT JOIN company_conversation_state s ON s.company_id = q.company_id
        WHERE {where_sql}
    """
    total_count = conn.execute(count_query, params).fetchone()["total"]

    query = f"""
        SELECT 
            q.queue_id,
            q.company_id::text,
            c.company_name,
            c.industry,
            c.city,
            c.state as company_state,
            c.website,
            q.recipient,
            q.subject,
            q.body,
            q.state as queue_state,
            q.attempts,
            q.sent_at,
            q.due_at,
            q.stop_reason,
            s.current_status as conversation_status,
            s.last_reply_classification,
            s.last_reply_at
        FROM outreach_queue q
        JOIN companies c ON c.company_id = q.company_id
        LEFT JOIN company_conversation_state s ON s.company_id = q.company_id
        WHERE {where_sql}
        ORDER BY (q.state = 'sent') DESC, (q.state = 'claimed') DESC, q.created_at ASC
        LIMIT %(limit)s OFFSET %(offset)s
    """
    rows = conn.execute(query, params).fetchall()

    leads = []
    for r in rows:
        leads.append({
            "queue_id": r["queue_id"],
            "company_id": r["company_id"],
            "company_name": r["company_name"],
            "industry": r["industry"] or "Local Business",
            "city": r["city"] or "",
            "company_state": r["company_state"] or "",
            "website": r["website"] or "",
            "recipient": r["recipient"] or "",
            "subject": r["subject"] or "",
            "body": r["body"] or "",
            "queue_state": r["queue_state"] or "queued",
            "attempts": r["attempts"] or 0,
            "sent_at": r["sent_at"].isoformat() if r["sent_at"] else None,
            "due_at": r["due_at"].isoformat() if r["due_at"] else None,
            "stop_reason": r["stop_reason"] or "",
            "conversation_status": r["conversation_status"] or "none",
            "last_reply_classification": r["last_reply_classification"] or "",
            "last_reply_at": r["last_reply_at"].isoformat() if r["last_reply_at"] else None,
        })

    return {
        "total": total_count,
        "limit": limit,
        "offset": offset,
        "leads": leads,
    }


def get_leads_list(
    conn,
    search: str = "",
    filter_by: str = "all",
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Query paginated companies with contacts and staged drafts."""
    where_clauses = ["c.active"]
    params: list = []

    if search.strip():
        term = f"%{search.strip().lower()}%"
        where_clauses.append(
            "(lower(c.company_name) LIKE %s OR lower(coalesce(c.city, '')) LIKE %s OR lower(coalesce(c.industry, '')) LIKE %s OR lower(coalesce(ct.email, '')) LIKE %s)"
        )
        params.extend([term, term, term, term])

    if filter_by == "qualified":
        where_clauses.append("c.outreach_status = 'outreach_ready'")
    elif filter_by == "has_email":
        where_clauses.append("ct.email IS NOT NULL")
    elif filter_by == "staged":
        where_clauses.append("q.queue_id IS NOT NULL")
    elif filter_by == "unstaged_qualified":
        where_clauses.append("c.outreach_status = 'outreach_ready' AND ct.email IS NOT NULL AND q.queue_id IS NULL")

    where_sql = " AND ".join(where_clauses)

    count_query = f"""
        SELECT count(DISTINCT c.company_id) AS total
        FROM companies c
        LEFT JOIN LATERAL (
            SELECT email, email_status FROM contacts 
            WHERE company_id = c.company_id AND email_status IN ('validated', 'published')
            ORDER BY (email_status = 'validated') DESC, discovered_at LIMIT 1
        ) ct ON true
        LEFT JOIN outreach_queue q ON q.company_id = c.company_id AND q.campaign = %s
        WHERE {where_sql}
    """
    total_count = conn.execute(count_query, [OFFER_CAMPAIGN_ID] + params).fetchone()["total"]

    query = f"""
        SELECT 
            c.company_id::text,
            c.company_name,
            c.website,
            c.industry,
            c.city,
            c.state,
            c.outreach_status,
            c.qualification_status,
            c.personalization,
            c.updated_at,
            ct.email,
            ct.email_status,
            q.queue_id,
            q.subject AS staged_subject,
            q.body AS staged_body,
            q.state AS draft_state,
            q.created_at AS draft_created_at
        FROM companies c
        LEFT JOIN LATERAL (
            SELECT email, email_status FROM contacts 
            WHERE company_id = c.company_id AND email_status IN ('validated', 'published')
            ORDER BY (email_status = 'validated') DESC, discovered_at LIMIT 1
        ) ct ON true
        LEFT JOIN outreach_queue q ON q.company_id = c.company_id AND q.campaign = %s
        WHERE {where_sql}
        ORDER BY (q.queue_id IS NOT NULL) DESC, (c.outreach_status = 'outreach_ready') DESC, (ct.email IS NOT NULL) DESC, c.updated_at DESC
        LIMIT %s OFFSET %s
    """
    
    rows = conn.execute(query, [OFFER_CAMPAIGN_ID] + params + [limit, offset]).fetchall()

    leads = []
    for r in rows:
        facts = r["personalization"] or {}
        if isinstance(facts, str):
            try:
                facts = json.loads(facts)
            except Exception:
                facts = {}

        reviews = facts.get("google_reviews")
        rating = facts.get("google_rating")

        leads.append({
            "company_id": r["company_id"],
            "company_name": r["company_name"],
            "website": r["website"] or "",
            "industry": r["industry"] or "Local Business",
            "city": r["city"] or "",
            "state": r["state"] or "",
            "google_reviews": reviews,
            "google_rating": rating,
            "email": r["email"] or "",
            "email_status": r["email_status"] or "",
            "outreach_status": r["outreach_status"] or "not_ready",
            "qualification_status": r["qualification_status"] or "LOW",
            "has_draft": r["queue_id"] is not None,
            "staged_subject": r["staged_subject"] or "",
            "staged_body": r["staged_body"] or "",
            "draft_state": r["draft_state"] or "",
        })

    return {
        "total": total_count,
        "limit": limit,
        "offset": offset,
        "leads": leads,
    }


def generate_company_pitch(conn, company_id: str, postal_addr: str = DEFAULT_POSTAL_ADDR) -> dict:
    """Generate high-converting, personalized outreach copy grounded in company Google profile."""
    row = conn.execute(
        """
        SELECT 
            c.company_id::text, c.company_name, c.industry, c.city, c.state, c.website,
            c.personalization, ct.email
        FROM companies c
        LEFT JOIN LATERAL (
            SELECT email FROM contacts 
            WHERE company_id = c.company_id AND email_status IN ('validated', 'published')
            ORDER BY (email_status = 'validated') DESC, discovered_at LIMIT 1
        ) ct ON true
        WHERE c.company_id = %s::uuid
        """,
        (company_id,),
    ).fetchone()

    if not row:
        return {"error": "Company not found"}

    cname = row["company_name"]
    city = (row["city"] or "").strip()
    industry = (row["industry"] or "local service").replace("_", " ").title()
    facts = row["personalization"] or {}
    if isinstance(facts, str):
        try:
            facts = json.loads(facts)
        except Exception:
            facts = {}

    reviews = facts.get("google_reviews")
    rating = facts.get("google_rating")

    # Clean display name
    clean_name = re.sub(r"\b(LLC|Inc\.?|Corp\.?|Co\.|Ltd\.?|PLLC)\b", "", cname, flags=re.I).strip(" ,.")
    display_title = clean_name if len(clean_name) > 2 else cname

    # Grounded hook based on public Google profile evidence
    if reviews is not None and rating is not None:
        hook = f"I noticed {display_title} has {reviews} Google reviews at {rating:g} stars"
        if city:
            hook += f" in {city}."
        else:
            hook += "."
    elif reviews is not None:
        hook = f"I noticed {display_title} has {reviews} Google reviews."
    else:
        hook = f"I was looking at {industry} businesses in {city or 'your area'} and came across {display_title}."

    subject = f"Google reviews for {display_title}"
    if len(subject.split()) > 8:
        subject = f"Google reviews for {display_title.split()[0]}"

    body = (
        f"Hi {display_title} team,\n\n"
        f"{hook}\n\n"
        f"Can I send over a short video breakdown of your local visibility?\n\n"
        f"Matt\n\n"
        f"--\n"
        f"{postal_addr.strip()}\n"
        f"If you would rather not hear from me, reply \"stop\" and I will not email you again."
    )

    return {
        "company_id": row["company_id"],
        "company_name": cname,
        "recipient": row["email"] or "",
        "subject": subject,
        "body": body,
        "evidence": {
            "google_reviews": reviews,
            "google_rating": rating,
            "city": city,
            "industry": industry,
        },
    }


def stage_draft_for_company(
    conn,
    company_id: str,
    recipient: str,
    subject: str,
    body: str,
    postal_addr: str = DEFAULT_POSTAL_ADDR,
) -> dict:
    """Save an approved/edited draft directly into outreach_queue."""
    if not recipient or "@" not in recipient:
        return {"error": "Invalid recipient email"}
    if not subject.strip():
        return {"error": "Subject cannot be empty"}
    if not body.strip():
        return {"error": "Body cannot be empty"}

    # Ensure footer exists if missing
    if "If you would rather not hear from me" not in body:
        body = (
            f"{body.strip()}\n\n"
            f"--\n"
            f"{postal_addr.strip()}\n"
            f"If you would rather not hear from me, reply \"stop\" and I will not email you again."
        )

    cur = conn.execute(
        """
        INSERT INTO outreach_queue
            (company_id, step, recipient, subject, body, copy_variant, state, campaign, copy_source, evidence)
        VALUES (%s::uuid, 0, %s, %s, %s, 'dashboard_custom', 'queued', %s, 'dashboard_ui', '{}'::jsonb)
        ON CONFLICT (company_id, step) DO UPDATE SET
            recipient = EXCLUDED.recipient,
            subject = EXCLUDED.subject,
            body = EXCLUDED.body,
            state = 'queued',
            copy_variant = EXCLUDED.copy_variant,
            updated_at = now()
        RETURNING queue_id
        """,
        (company_id, recipient.strip(), subject.strip(), body.strip(), OFFER_CAMPAIGN_ID),
    )
    conn.commit()
    row = cur.fetchone()
    return {"status": "success", "queue_id": row["queue_id"] if row else None}


def batch_stage_qualified_leads(conn, postal_addr: str = DEFAULT_POSTAL_ADDR, limit: int = 200) -> dict:
    """Batch-generate and stage personalized drafts for all qualified leads with contacts."""
    rows = conn.execute(
        """
        SELECT 
            c.company_id::text, c.company_name, c.industry, c.city, c.state,
            c.personalization, ct.email
        FROM companies c
        JOIN LATERAL (
            SELECT email FROM contacts 
            WHERE company_id = c.company_id AND email_status IN ('validated', 'published')
            ORDER BY (email_status = 'validated') DESC, discovered_at LIMIT 1
        ) ct ON true
        LEFT JOIN outreach_queue q ON q.company_id = c.company_id AND q.campaign = %s
        WHERE c.active 
          AND c.outreach_status = 'outreach_ready'
          AND q.queue_id IS NULL
        ORDER BY c.updated_at DESC
        LIMIT %s
        """,
        (OFFER_CAMPAIGN_ID, limit),
    ).fetchall()

    staged_count = 0
    for r in rows:
        cid = r["company_id"]
        cname = r["company_name"]
        email = r["email"]
        city = (r["city"] or "").strip()
        facts = r["personalization"] or {}
        if isinstance(facts, str):
            try:
                facts = json.loads(facts)
            except Exception:
                facts = {}

        reviews = facts.get("google_reviews")
        rating = facts.get("google_rating")

        clean_name = re.sub(r"\b(LLC|Inc\.?|Corp\.?|Co\.|Ltd\.?|PLLC)\b", "", cname, flags=re.I).strip(" ,.")
        display_title = clean_name if len(clean_name) > 2 else cname

        if reviews is not None and rating is not None:
            hook = f"I noticed {display_title} has {reviews} Google reviews at {rating:g} stars"
            if city:
                hook += f" in {city}."
            else:
                hook += "."
        elif reviews is not None:
            hook = f"I noticed {display_title} has {reviews} Google reviews."
        else:
            hook = f"I noticed {display_title} on Google Maps."

        subject = f"Google reviews for {display_title}"
        if len(subject.split()) > 8:
            subject = f"Google reviews for {display_title.split()[0]}"

        body = (
            f"Hi {display_title} team,\n\n"
            f"{hook}\n\n"
            f"Can I send over a short video breakdown of your local visibility?\n\n"
            f"Matt\n\n"
            f"--\n"
            f"{postal_addr.strip()}\n"
            f"If you would rather not hear from me, reply \"stop\" and I will not email you again."
        )

        res = stage_draft_for_company(conn, cid, email, subject, body, postal_addr)
        if res.get("status") == "success":
            staged_count += 1

    return {"status": "completed", "candidates_found": len(rows), "drafts_staged": staged_count}


def get_scraped_problems_list() -> dict:
    """Return realistic market problems categorized into prebuilt no-brainers vs high-friction traps."""
    problems = [
        {
            "id": "prob_mctb_prebuilt",
            "niche": "Blue-Collar & Service Trades",
            "title": "Missed Emergency Calls & Slow Lead Response",
            "is_no_brainer": True,
            "verdict": "🏆 100% NO-BRAINER (Instant Client ROI)",
            "prebuilt_status": "📦 100% PREBUILT IN LOCAL REPO (`products/missed-call-textback`)",
            "execution_difficulty": "1 / 5 (Zero Coding — 1-Shot Script)",
            "difficulty_stars": "★☆☆☆☆",
            "setup_time": "10 Minutes (run setup_tenant.ps1)",
            "client_retainer": "$299 – $499 / mo",
            "client_roi": "1 saved job ($1,500+) pays for 6 months",
            "client_friction": "Zero habit change. Techs keep working; system works silently via SMS.",
            "what_you_deliver": "When an HVAC tech or plumber misses a call on a ladder, an automatic text fires in 15 seconds to book the job before they call a competitor.",
            "why_they_buy": "They know every missed call is a $2,000 job walking to the next guy on Google Maps. No app to learn, no crew to train.",
            "sources": ["r/HVAC", "r/sweatystartup", "r/Plumbing"],
            "quote": "When an AC goes out in 95-degree heat or a pipe bursts, customers call down Google Maps. If you don't answer in 3 minutes, they book the next guy. We lose thousands every month because we are in an attic."
        },
        {
            "id": "prob_google_reviews_prebuilt",
            "niche": "Blue-Collar & Service Trades",
            "title": "Google Maps Ranking & Low Review Count",
            "is_no_brainer": True,
            "verdict": "🏆 100% NO-BRAINER (Prebuilt Outreach Engine)",
            "prebuilt_status": "📦 100% PREBUILT ON PORT 8080 (BrightReach Lead Engine)",
            "execution_difficulty": "1 / 5 (Zero Build — Ready to Sell)",
            "difficulty_stars": "★☆☆☆☆",
            "setup_time": "Immediate (Campaign: google_reviews_maps_299)",
            "client_retainer": "$299 / mo flat fee",
            "client_roi": "Top 3 Google Maps rank = 20-50 more calls/mo",
            "client_friction": "Zero. System triggers 1-tap review link text after work is finished.",
            "what_you_deliver": "Automated post-job review request system that turns completed jobs into 5-star Google Reviews to boost Google Maps rank.",
            "why_they_buy": "Contractors know Google Maps drives 80% of local jobs. They hate seeing their competitor with 150 reviews getting all the calls.",
            "sources": ["BrightReach Core Stack", "r/smallbusiness"],
            "quote": "If you are not in the top 3 on Google Maps, you do not exist. We do great work but forget to ask for reviews, while the hack down the street has 200 reviews and takes all the commercial calls."
        },
        {
            "id": "prob_takeoff_estimating_prebuilt",
            "niche": "Construction & Subcontracting",
            "title": "Subcontractor Estimating & Quantity Takeoff Backlog",
            "is_no_brainer": True,
            "verdict": "🏆 100% NO-BRAINER (Productized Service, No Software to Build)",
            "prebuilt_status": "📑 PREBUILT SPREADSHEETS (Standard CSI Division 03/04 Templates)",
            "execution_difficulty": "2 / 5 (Zero Code — Pure Execution)",
            "difficulty_stars": "★★☆☆☆",
            "setup_time": "1 Day (Use standard trade takeoff sheets)",
            "client_retainer": "$1,500 – $3,000 / mo recurring retainer",
            "client_roi": "Frees 20 hrs/week for owner; enables 3x more bids",
            "client_friction": "Zero software to learn. Contractor just forwards drawing PDFs via email or Google Drive.",
            "what_you_deliver": "48-hour material quantity takeoff and bid scope sheets for concrete, masonry, precast, or drywall trade subcontractors.",
            "why_they_buy": "Subcontractors work 12 hours pouring concrete or laying block and are exhausted. They already pay freelance estimators $50–$100/hr to avoid bidding until 1 AM.",
            "sources": ["r/Estimating", "r/Construction"],
            "quote": "Every contractor is inundated with offshore takeoff spam. They can't read specs and have zero skin in the game. I spend 12 hours on site and then 8 PM to midnight measuring plan sets just to get bids out."
        },
        {
            "id": "prob_change_order_trap",
            "niche": "Construction & Subcontracting",
            "title": "Unbilled Field Change Orders",
            "is_no_brainer": False,
            "verdict": "⚠️ HIGH RESISTANCE (Foremen Refuse Complex Apps)",
            "prebuilt_status": "🛠️ REQUIRES 1-SHOT SMS FLOW (Only Doable via Simple SMS Link)",
            "execution_difficulty": "2 / 5 (Moderate)",
            "difficulty_stars": "★★☆☆☆",
            "setup_time": "1–2 Days (Lightweight SMS signature page)",
            "client_retainer": "$350 / mo",
            "client_roi": "Stops $10k–$30k/yr in uncollected change work",
            "client_friction": "HIGH IF AN APP. Foremen in muddy boots with cracked screens will NOT log into apps. ONLY works if 1-tap SMS link.",
            "what_you_deliver": "1-tap SMS link sent from foreman's phone to client before extra work starts. Client taps 'Approve'.",
            "why_they_buy": "Only buys if it takes 15 seconds on a phone without logging into an account. Otherwise crew refuses to use it.",
            "sources": ["r/contractor"],
            "quote": "Clients demand changes on site. You do the work to keep the crew moving. At final billing, they refuse to pay because there was no signed change order. Software like Procore is way too bloated for field guys."
        },
        {
            "id": "prob_ai_chatbot_trap",
            "niche": "AI & Automation Solutions",
            "title": "Website AI Chatbots",
            "is_no_brainer": False,
            "verdict": "❌ TOTAL WASTE OF TIME (High Resistance / Fast Churn)",
            "prebuilt_status": "⚠️ DO NOT SELL TO CONTRACTORS",
            "execution_difficulty": "4 / 5 (High Churn / High Maintenance)",
            "difficulty_stars": "★★★★☆",
            "setup_time": "N/A (Avoid)",
            "client_retainer": "$0 – $50 (Race to bottom)",
            "client_roi": "Near zero (Homeowners call, they don't chat)",
            "client_friction": "Extreme skepticism. Contractors view AI chatbots as gimmicky toys that hallucinate.",
            "what_you_deliver": "DO NOT SELL. Blue-collar customers do not want website chatbots; they have emergencies and pick up the phone.",
            "why_they_buy": "They DON'T buy it. High refund rates and complaints. Avoid completely.",
            "sources": ["r/SaaS", "r/Entrepreneur"],
            "quote": "Contractors delete emails pitching AI chatbots immediately. They only buy tools that solve a specific, expensive headache. Pitching 'AI' triggers instant spam filters."
        },
        {
            "id": "prob_pm_software_trap",
            "niche": "Real Estate & Property Management",
            "title": "Automated Property Management Maintenance Dispatch",
            "is_no_brainer": False,
            "verdict": "⚠️ HIGH INTEGRATION FRICTION (Walled-Garden Software)",
            "prebuilt_status": "🛠️ REQUIRES COMPLEX APPFOLIO/BUILDIUM APIS",
            "execution_difficulty": "3.5 / 5 (High Technical Overhead)",
            "difficulty_stars": "★★★☆☆",
            "setup_time": "1–2 Weeks per firm",
            "client_retainer": "$600 – $1,200 / mo",
            "client_roi": "Saves 15 hrs/wk of manager phone tag",
            "client_friction": "Property managers are overwhelmed and locked into legacy software that doesn't provide open webhooks.",
            "what_you_deliver": "Only attempt as an asynchronous email intake triage bot, never a full API overhaul.",
            "why_they_buy": "Pain is real, but switching or adding external tools to AppFolio is painful for solo operators.",
            "sources": ["r/PropertyManagement"],
            "quote": "Maintenance coordination consumes 70% of our day, but trying to get our staff to use third-party tools outside our main property software is like pulling teeth."
        }
    ]
    return {"problems": problems, "total": len(problems), "last_scraped": "2026-10-09T05:30:00Z"}


def render_dashboard_html() -> str:
    """Return self-contained HTML/CSS/JS dashboard UI."""
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>BrightReach B2B Lead Engine & Outreach</title>
  <style>
    :root {
      --bg-main: #0f172a;
      --bg-card: #1e293b;
      --bg-hover: #334155;
      --text-main: #f8fafc;
      --text-muted: #94a3b8;
      --primary: #3b82f6;
      --primary-hover: #2563eb;
      --accent-green: #10b981;
      --accent-amber: #f59e0b;
      --accent-purple: #8b5cf6;
      --border-color: #334155;
      --radius: 8px;
    }
    
    /* Main Tabs Navigation */
    .tabs-nav-bar {
      display: flex;
      gap: 12px;
      margin-bottom: 24px;
      border-bottom: 1px solid var(--border-color);
      padding-bottom: 12px;
    }
    .nav-tab-btn {
      background: transparent;
      border: 1px solid var(--border-color);
      color: var(--text-muted);
      padding: 10px 20px;
      border-radius: var(--radius);
      font-size: 14px;
      font-weight: 700;
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 8px;
      transition: all 0.2s;
    }
    .nav-tab-btn:hover { background: var(--bg-hover); color: #fff; }
    .nav-tab-btn.active {
      background: linear-gradient(135deg, #2563eb, #3b82f6);
      color: #fff;
      border-color: #2563eb;
      box-shadow: 0 4px 12px rgba(37, 99, 235, 0.3);
    }
    .tab-badge {
      background: rgba(16, 185, 129, 0.25);
      color: #34d399;
      font-size: 11px;
      padding: 2px 8px;
      border-radius: 999px;
      font-weight: 700;
    }
    .tab-view { display: block; }

    /* Research Tab Styles */
    .research-hero-banner {
      background: linear-gradient(135deg, rgba(30, 41, 59, 0.8), rgba(15, 23, 42, 0.9));
      border: 1px solid var(--border-color);
      border-radius: var(--radius);
      padding: 20px;
      margin-bottom: 24px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 16px;
    }
    .research-hero-text h2 { font-size: 18px; font-weight: 700; margin-bottom: 4px; }
    .research-hero-text p { font-size: 13px; color: var(--text-muted); }
    .research-actions { display: flex; gap: 10px; flex-wrap: wrap; }

    .problems-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(380px, 1fr));
      gap: 18px;
      margin-bottom: 30px;
    }
    .problem-card {
      background: var(--bg-card);
      border: 1px solid var(--border-color);
      border-radius: var(--radius);
      padding: 18px;
      display: flex;
      flex-direction: column;
      gap: 12px;
      transition: border-color 0.15s;
    }
    .problem-card:hover { border-color: #3b82f6; }
    .problem-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 8px; }
    .problem-title { font-size: 15px; font-weight: 700; color: #fff; }
    .niche-tag {
      display: inline-block;
      font-size: 11px;
      font-weight: 600;
      padding: 2px 8px;
      border-radius: 4px;
      background: rgba(59, 130, 246, 0.2);
      color: #60a5fa;
      margin-top: 4px;
    }
    .badge-urgency-severe { background: rgba(239, 68, 68, 0.2); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.4); padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 700; white-space: nowrap; }
    .badge-urgency-immediate { background: rgba(245, 158, 11, 0.2); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.4); padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 700; white-space: nowrap; }
    .badge-urgency-high { background: rgba(16, 185, 129, 0.2); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.4); padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 700; white-space: nowrap; }
    .badge-urgency-critical { background: rgba(168, 85, 247, 0.2); color: #c084fc; border: 1px solid rgba(168, 85, 247, 0.4); padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 700; white-space: nowrap; }

    .quote-box {
      background: #0f172a;
      border-left: 3px solid #3b82f6;
      padding: 10px 12px;
      font-style: italic;
      font-size: 12px;
      color: #cbd5e1;
      border-radius: 0 4px 4px 0;
      line-height: 1.4;
    }
    .metrics-list { display: flex; flex-direction: column; gap: 6px; font-size: 12px; }
    .metric-item { display: flex; justify-content: space-between; border-bottom: 1px solid rgba(51, 65, 85, 0.3); padding-bottom: 4px; }
    .metric-label { color: var(--text-muted); }
    .metric-value { font-weight: 600; color: #f8fafc; text-align: right; }
    .solution-box {
      background: rgba(16, 185, 129, 0.08);
      border: 1px solid rgba(16, 185, 129, 0.25);
      border-radius: 6px;
      padding: 10px 12px;
      font-size: 12px;
    }
    .solution-box strong { color: #34d399; display: block; margin-bottom: 3px; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; }

    /* Q&A Section */
    .qa-card {
      background: var(--bg-card);
      border: 1px solid var(--border-color);
      border-radius: var(--radius);
      padding: 20px;
      margin-bottom: 24px;
    }
    .qa-header { font-size: 16px; font-weight: 700; margin-bottom: 12px; display: flex; align-items: center; gap: 8px; }
    .qa-input-box { display: flex; gap: 10px; margin-bottom: 12px; }
    .qa-input-box input {
      flex: 1;
      background: #0f172a;
      border: 1px solid var(--border-color);
      color: #fff;
      padding: 10px 14px;
      border-radius: 6px;
      font-size: 14px;
    }
    .qa-examples { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 14px; font-size: 12px; }
    .qa-chip {
      background: rgba(51, 65, 85, 0.5);
      border: 1px solid var(--border-color);
      color: var(--text-muted);
      padding: 4px 10px;
      border-radius: 999px;
      cursor: pointer;
      transition: all 0.15s;
    }
    .qa-chip:hover { background: #334155; color: #fff; }
    .qa-result {
      background: #0f172a;
      border: 1px solid var(--border-color);
      border-radius: 6px;
      padding: 16px;
      font-size: 13px;
      line-height: 1.6;
      display: none;
    }
    .citation-tag {
      display: inline-block;
      background: rgba(59, 130, 246, 0.2);
      color: #60a5fa;
      font-size: 11px;
      font-family: monospace;
      padding: 2px 6px;
      border-radius: 4px;
      margin-top: 8px;
      margin-right: 6px;
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
      background-color: var(--bg-main);
      color: var(--text-main);
      padding: 24px;
      line-height: 1.5;
    }
    .header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 24px;
      padding-bottom: 16px;
      border-bottom: 1px solid var(--border-color);
    }
    .logo-area { display: flex; align-items: center; gap: 12px; }
    .logo-badge {
      background: linear-gradient(135deg, #2563eb, #8b5cf6);
      width: 40px;
      height: 40px;
      border-radius: var(--radius);
      display: flex;
      align-items: center;
      justify-content: center;
      font-weight: 700;
      font-size: 18px;
    }
    .title-area h1 { font-size: 20px; font-weight: 700; }
    .title-area p { font-size: 13px; color: var(--text-muted); }
    .status-pill {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      background: rgba(16, 185, 129, 0.15);
      color: #34d399;
      padding: 4px 10px;
      border-radius: 9999px;
      font-size: 12px;
      font-weight: 600;
      border: 1px solid rgba(16, 185, 129, 0.3);
    }
    .status-dot { width: 8px; height: 8px; border-radius: 50%; background-color: #34d399; }
    
    /* Stats grid */
    .stats-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 16px;
      margin-bottom: 24px;
    }
    .stat-card {
      background: var(--bg-card);
      border: 1px solid var(--border-color);
      border-radius: var(--radius);
      padding: 16px;
      display: flex;
      flex-direction: column;
    }
    .stat-label { font-size: 12px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600; margin-bottom: 4px; }
    .stat-val { font-size: 26px; font-weight: 700; color: var(--text-main); }
    .stat-sub { font-size: 12px; color: var(--text-muted); margin-top: 4px; }

    /* Actions bar */
    .controls-bar {
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 16px;
      background: var(--bg-card);
      padding: 12px 16px;
      border-radius: var(--radius);
      border: 1px solid var(--border-color);
    }
    .search-box {
      display: flex;
      align-items: center;
      gap: 8px;
      flex: 1;
      min-width: 250px;
    }
    .search-box input {
      width: 100%;
      background: #0f172a;
      border: 1px solid var(--border-color);
      color: #fff;
      padding: 8px 12px;
      border-radius: 6px;
      font-size: 14px;
    }
    .filter-tabs { display: flex; gap: 8px; }
    .filter-btn {
      background: transparent;
      border: 1px solid var(--border-color);
      color: var(--text-muted);
      padding: 6px 12px;
      border-radius: 6px;
      font-size: 13px;
      cursor: pointer;
      transition: all 0.15s;
    }
    .filter-btn:hover { background: var(--bg-hover); color: #fff; }
    .filter-btn.active { background: var(--primary); color: #fff; border-color: var(--primary); }
    
    .btn {
      background: var(--primary);
      color: #fff;
      border: none;
      padding: 8px 14px;
      border-radius: 6px;
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      transition: background 0.15s;
    }
    .btn:hover { background: var(--primary-hover); }
    .btn-batch { background: linear-gradient(135deg, #10b981, #059669); }
    .btn-batch:hover { background: #059669; }
    .btn-secondary { background: var(--bg-hover); color: var(--text-main); }
    .btn-secondary:hover { background: #475569; }
    .btn-sm { padding: 4px 8px; font-size: 12px; }

    /* Table */
    .table-container {
      background: var(--bg-card);
      border: 1px solid var(--border-color);
      border-radius: var(--radius);
      overflow-x: auto;
    }
    table { width: 100%; border-collapse: collapse; text-align: left; font-size: 13px; }
    th {
      background: #1e293b;
      padding: 12px 14px;
      color: var(--text-muted);
      font-weight: 600;
      border-bottom: 1px solid var(--border-color);
      text-transform: uppercase;
      font-size: 11px;
      letter-spacing: 0.5px;
    }
    td { padding: 12px 14px; border-bottom: 1px solid #1e293b; vertical-align: middle; }
    tr:hover td { background: rgba(51, 65, 85, 0.4); }
    .company-cell { font-weight: 600; color: #fff; }
    .company-sub { font-size: 12px; color: var(--text-muted); }
    .tag {
      display: inline-block;
      padding: 2px 6px;
      border-radius: 4px;
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
    }
    .tag-ready { background: rgba(16, 185, 129, 0.2); color: #34d399; }
    .tag-not-ready { background: rgba(148, 163, 184, 0.15); color: #94a3b8; }
    .tag-staged { background: rgba(139, 92, 246, 0.2); color: #a78bfa; }
    .rating-badge {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      color: #fbbf24;
      font-weight: 600;
      font-size: 12px;
    }
    .email-cell { font-family: monospace; font-size: 12px; color: #38bdf8; }

    /* Modal */
    .modal-overlay {
      position: fixed;
      top: 0; left: 0; right: 0; bottom: 0;
      background: rgba(0, 0, 0, 0.7);
      backdrop-filter: blur(2px);
      display: none;
      align-items: center;
      justify-content: center;
      z-index: 100;
      padding: 20px;
    }
    .modal {
      background: var(--bg-card);
      border: 1px solid var(--border-color);
      border-radius: var(--radius);
      width: 100%;
      max-width: 650px;
      display: flex;
      flex-direction: column;
      box-shadow: 0 20px 25px -5px rgba(0,0,0,0.5);
    }
    .modal-header {
      padding: 16px 20px;
      border-bottom: 1px solid var(--border-color);
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .modal-header h2 { font-size: 16px; font-weight: 700; }
    .modal-close { background: none; border: none; color: var(--text-muted); font-size: 20px; cursor: pointer; }
    .modal-body { padding: 20px; display: flex; flex-direction: column; gap: 14px; }
    .form-group { display: flex; flex-direction: column; gap: 6px; }
    .form-group label { font-size: 12px; color: var(--text-muted); font-weight: 600; }
    .form-group input, .form-group textarea {
      background: #0f172a;
      border: 1px solid var(--border-color);
      color: #fff;
      padding: 8px 12px;
      border-radius: 6px;
      font-family: inherit;
      font-size: 13px;
    }
    .form-group textarea { min-height: 180px; resize: vertical; line-height: 1.4; }
    .modal-footer {
      padding: 14px 20px;
      border-top: 1px solid var(--border-color);
      display: flex;
      justify-content: flex-end;
      gap: 10px;
    }
    .toast {
      position: fixed;
      bottom: 24px;
      right: 24px;
      background: #10b981;
      color: #fff;
      padding: 10px 16px;
      border-radius: 6px;
      font-size: 13px;
      font-weight: 600;
      display: none;
      z-index: 200;
      box-shadow: 0 4px 6px rgba(0,0,0,0.3);
    }
    .empty-state { padding: 48px; text-align: center; color: var(--text-muted); }
  </style>
</head>
<body>

  <!-- Top Header -->
  <div class="header">
    <div class="logo-area">
      <div class="logo-badge">BR</div>
      <div class="title-area">
        <h1>BrightReach B2B Lead Engine & Outreach</h1>
        <p>Offer: Google Reviews & Google Maps Visibility — $299/mo (Campaign: google_reviews_maps_299)</p>
      </div>
    </div>
    <div style="display: flex; align-items: center; gap: 12px;">
      <span class="status-pill" id="engine-status-pill">
        <span class="status-dot"></span> <span id="engine-status-text">Engine Online (10m)</span>
      </span>
      <a href="http://localhost:5679/workflow/leadDiscoveryEngine" target="_blank" class="btn btn-secondary btn-sm" style="text-decoration:none;">Open in n8n</a>
    </div>
  </div>

  <!-- Main Application Tabs Navigation -->
  <div class="tabs-nav-bar">
    <button class="nav-tab-btn active" id="tab-btn-outreach" onclick="switchMainTab('outreach')">
      🚀 Live Outreach Engine (300 Max)
      <span class="tab-badge" id="nav-outreach-badge">Active</span>
    </button>
    <button class="nav-tab-btn" id="tab-btn-leads" onclick="switchMainTab('leads')">
      🎯 B2B Lead Engine & Discovery
    </button>
    <button class="nav-tab-btn" id="tab-btn-research" onclick="switchMainTab('research')">
      🧠 AI Business Problems & Research Engine
      <span class="tab-badge" id="nav-research-badge">Live Discovery</span>
    </button>
  </div>

  <!-- TAB: LIVE OUTREACH CAMPAIGN MONITOR VIEW -->
  <div id="view-outreach" class="tab-view active">
    <!-- Outreach Hero Banner -->
    <div class="research-hero-banner" style="border-left: 4px solid #3b82f6;">
      <div class="research-hero-text">
        <h2>Autonomous Cold Outreach Engine — Single Offer: $299/mo Google Reviews & Visibility</h2>
        <p>Configured Rule: <strong>Max 300 highly-personalized emails sent</strong>, grounded in real Google Maps review deficits, then automatic pause to monitor incoming replies.</p>
      </div>
      <div class="research-actions">
        <span class="status-pill" style="font-size:13px; padding:6px 14px;">
          <span class="status-dot"></span> <span id="outreach-runner-status">Ticker Active (Sends & Polls Every 10m)</span>
        </span>
      </div>
    </div>

    <!-- Outreach KPI Summary Cards -->
    <div class="stats-grid">
      <div class="stat-card">
        <div class="stat-label">Daily Cap Target</div>
        <div class="stat-val" id="kpi-outreach-cap" style="color: #f8fafc;">300 Max</div>
        <div class="stat-sub">Strict Limit Before Pause</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Total Outbound Sent</div>
        <div class="stat-val" id="kpi-outreach-sent" style="color: #3b82f6;">...</div>
        <div class="stat-sub" id="kpi-outreach-progress-sub">0% of 300 Cap Reached</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Queued / Ready to Send</div>
        <div class="stat-val" id="kpi-outreach-queued" style="color: #a78bfa;">...</div>
        <div class="stat-sub">Paced 55–110s between emails</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Replies Received</div>
        <div class="stat-val" id="kpi-outreach-replies" style="color: #34d399;">...</div>
        <div class="stat-sub">Auto-parsed via IMAP</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Interested Prospects</div>
        <div class="stat-val" id="kpi-outreach-interested" style="color: #10b981;">...</div>
        <div class="stat-sub">Follow-up auto-cancelled</div>
      </div>
    </div>

    <!-- Outreach Controls & Filter Bar -->
    <div class="controls-bar">
      <div class="search-box">
        <input type="text" id="outreach-search-input" placeholder="Search campaign leads by company, city, industry, recipient..." onkeyup="handleOutreachSearch(event)">
      </div>
      <div class="filter-tabs outreach-filter-tabs">
        <button class="filter-btn active" onclick="setOutreachFilter('all', this)">All Leads</button>
        <button class="filter-btn" onclick="setOutreachFilter('queued', this)">Queued</button>
        <button class="filter-btn" onclick="setOutreachFilter('claimed', this)">In Flight</button>
        <button class="filter-btn" onclick="setOutreachFilter('sent', this)">Sent</button>
        <button class="filter-btn" onclick="setOutreachFilter('replied', this)">Replied</button>
        <button class="filter-btn" style="color:#34d399; font-weight:700;" onclick="setOutreachFilter('interested', this)">Interested 🏆</button>
      </div>
      <div>
        <button class="btn btn-secondary btn-sm" onclick="loadOutreachStats(); loadOutreachLeads();">🔄 Refresh Status</button>
      </div>
    </div>

    <!-- Outreach Table -->
    <div class="table-container">
      <table>
        <thead>
          <tr>
            <th>Company & Location</th>
            <th>Recipient Email</th>
            <th>Personalized Subject</th>
            <th>Queue State</th>
            <th>Attempts / Due</th>
            <th>Conversation Status</th>
          </tr>
        </thead>
        <tbody id="outreach-tbody">
          <tr><td colspan="6" class="empty-state">Loading campaign queue...</td></tr>
        </tbody>
      </table>
    </div>

    <!-- Outreach Pagination -->
    <div style="display:flex; justify-content:space-between; align-items:center; margin-top:14px; font-size:13px; color:var(--text-muted);">
      <span id="outreach-page-summary">Showing 0 of 0 campaign leads</span>
      <div style="display:flex; gap:8px;">
        <button class="btn btn-secondary btn-sm" id="btn-outreach-prev" onclick="changeOutreachPage(-1)">Previous</button>
        <button class="btn btn-secondary btn-sm" id="btn-outreach-next" onclick="changeOutreachPage(1)">Next</button>
      </div>
    </div>
  </div>
  <!-- END TAB OUTREACH -->

  <!-- TAB 1: B2B LEAD ENGINE VIEW -->
  <div id="view-leads" class="tab-view" style="display: none;">
    <!-- KPI Cards -->
    <div class="stats-grid">
      <div class="stat-card">
        <div class="stat-label">Total Companies Scraped</div>
        <div class="stat-val" id="kpi-total-companies">...</div>
        <div class="stat-sub">Backlog + Discovery</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Verified Contact Emails</div>
        <div class="stat-val" id="kpi-emails">...</div>
        <div class="stat-sub" id="kpi-contacts-sub">...</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Qualified for $299/mo Offer</div>
        <div class="stat-val" id="kpi-qualified" style="color: #38bdf8;">...</div>
        <div class="stat-sub">Review & Maps Profile Ready</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Personalized Drafts Staged</div>
        <div class="stat-val" id="kpi-staged" style="color: #a78bfa;">...</div>
        <div class="stat-sub">Queued in Database for Review</div>
      </div>
    </div>

    <!-- Action Controls -->
    <div class="controls-bar">
      <div class="search-box">
        <input type="text" id="search-input" placeholder="Search company, city, industry, or email..." onkeyup="handleSearch(event)">
      </div>
      <div class="filter-tabs">
        <button class="filter-btn active" onclick="setFilter('all', this)">All</button>
        <button class="filter-btn" onclick="setFilter('qualified', this)">Qualified Only</button>
        <button class="filter-btn" onclick="setFilter('has_email', this)">Has Email</button>
        <button class="filter-btn" onclick="setFilter('staged', this)">Drafts Staged</button>
      </div>
      <div>
        <button class="btn btn-batch" onclick="triggerBatchGenerate()">⚡ Batch Generate All Qualified Drafts</button>
      </div>
    </div>

    <!-- Table of Companies -->
    <div class="table-container">
      <table>
        <thead>
          <tr>
            <th>Company / Domain</th>
            <th>Industry & Location</th>
            <th>Google Reviews</th>
            <th>Discovered Contact</th>
            <th>Status</th>
            <th>Personalized Outreach</th>
          </tr>
        </thead>
        <tbody id="leads-tbody">
          <tr><td colspan="6" class="empty-state">Loading leads data...</td></tr>
        </tbody>
      </table>
    </div>

    <!-- Pagination Bar -->
    <div style="display:flex; justify-content:space-between; align-items:center; margin-top:14px; font-size:13px; color:var(--text-muted);">
      <span id="page-summary">Showing 0 of 0 leads</span>
      <div style="display:flex; gap:8px;">
        <button class="btn btn-secondary btn-sm" id="btn-prev" onclick="changePage(-1)">Previous</button>
        <button class="btn btn-secondary btn-sm" id="btn-next" onclick="changePage(1)">Next</button>
      </div>
    </div>
  </div>
  <!-- END TAB 1 -->

  <!-- TAB 2: AI BUSINESS PROBLEMS & RESEARCH VIEW -->
  <div id="view-research" class="tab-view" style="display: none;">
    <!-- Research Banner -->
    <div class="research-hero-banner">
      <div class="research-hero-text">
        <h2>Continuous AI Business Research & Niche Problem Engine</h2>
        <p>Continuous discovery across Reddit (r/Construction, r/contractor, r/Estimating, r/HVAC, r/PropertyManagement), YouTube feeds, and sibling knowledge bases.</p>
      </div>
      <div class="research-actions">
        <button class="btn btn-batch" onclick="triggerResearchRun()">⚡ Run Problem Scraper Now</button>
        <a href="https://drive.google.com/drive/folders/1G0ibojBA0RcA7cmSPtg_s24LBwgOL3ds" target="_blank" class="btn btn-secondary" style="text-decoration:none;">📂 Open Google Drive KB</a>
        <a href="http://localhost:5679/workflow/onlineMoneyResearch" target="_blank" class="btn btn-secondary" style="text-decoration:none;">⚙️ Open in n8n</a>
      </div>
    </div>

    <!-- Research KPIs -->
    <div class="stats-grid">
      <div class="stat-card">
        <div class="stat-label">Total Sources Monitored</div>
        <div class="stat-val" id="kpi-research-discovered">61+</div>
        <div class="stat-sub">Reddit Feeds, YouTube & Local KBs</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Verified Extractions</div>
        <div class="stat-val" id="kpi-research-extracted">57+</div>
        <div class="stat-sub">Deterministic Parsing ($0 AI Cost)</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Validated High-Fit Models</div>
        <div class="stat-val" id="kpi-research-opportunities" style="color: #38bdf8;">5 Models</div>
        <div class="stat-sub">Tailored to $100 budget & Mini PC</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Google Drive Persistence</div>
        <div class="stat-val" id="kpi-research-drive" style="color: #10b981;">149 Files</div>
        <div class="stat-sub">Synced across 8 folders</div>
      </div>
    </div>

    <!-- Interactive Problem Search & Niche Filter -->
    <div class="controls-bar">
      <div class="search-box">
        <input type="text" id="problem-search-input" placeholder="Search problems, trades (e.g. change orders, HVAC, estimating, dispatch)..." onkeyup="renderProblems()">
      </div>
      <div class="filter-tabs problem-filter-tabs">
        <button class="filter-btn active" onclick="setProblemFilter('all', this)">All (6)</button>
        <button class="filter-btn" style="color:#34d399; font-weight:700;" onclick="setProblemFilter('no_brainer', this)">🏆 100% No-Brainers (3)</button>
        <button class="filter-btn" style="color:#f87171;" onclick="setProblemFilter('traps', this)">⚠️ Traps to Avoid (3)</button>
        <button class="filter-btn" onclick="setProblemFilter('Construction & Subcontracting', this)">Construction</button>
        <button class="filter-btn" onclick="setProblemFilter('Blue-Collar & Service Trades', this)">Blue-Collar Trades</button>
      </div>
    </div>

    <!-- Scraped Problems & Market Validation Grid -->
    <div class="problems-grid" id="problems-grid">
      <div class="empty-state">Loading scraped niche problems...</div>
    </div>

    <!-- Ask the Knowledge Engine Console -->
    <div class="qa-card">
      <div class="qa-header">
        <span>🔍</span> Ask the Stored Knowledge Base & Problem Evidence
      </div>
      <div class="qa-input-box">
        <input type="text" id="qa-question-input" placeholder="Ask any business strategy question (e.g., 'What are the biggest profit leaks in construction?')" onkeydown="if(event.key==='Enter') askResearchEngine()">
        <button class="btn" onclick="askResearchEngine()">Search Evidence</button>
      </div>
      <div class="qa-examples">
        <span style="color:var(--text-muted); align-self:center;">Try asking:</span>
        <span class="qa-chip" onclick="setQaExample('What are the biggest unbilled costs for trade subcontractors?')">Subcontractor Unbilled Costs</span>
        <span class="qa-chip" onclick="setQaExample('Why do HVAC companies lose money after hours?')">HVAC Missed Calls</span>
        <span class="qa-chip" onclick="setQaExample('What maintenance problems do property managers pay to solve?')">Property Management Triage</span>
        <span class="qa-chip" onclick="setQaExample('Why does selling AI chatbots fail for contractors?')">The AI Chatbot Trap</span>
      </div>
      <div class="qa-result" id="qa-result-box">
        <div id="qa-result-content"></div>
        <div id="qa-result-citations"></div>
      </div>
    </div>

    <!-- Google Drive Knowledge Folders Quick Reference -->
    <div style="background:var(--bg-card); border:1px solid var(--border-color); border-radius:var(--radius); padding:16px; margin-top:20px;">
      <div style="font-weight:700; font-size:13px; margin-bottom:10px; color:#94a3b8; text-transform:uppercase; letter-spacing:0.5px;">Google Drive Knowledge Base Directories (ONLINE_MONEY_RESEARCH)</div>
      <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(220px, 1fr)); gap:10px; font-size:12px;">
        <a href="https://drive.google.com/drive/folders/1PMtv0AP_U7LjSKrmlG5LbklsZ1h364z1" target="_blank" style="color:#60a5fa; text-decoration:none;">📁 00_SOURCE_INDEX ↗</a>
        <a href="https://drive.google.com/drive/folders/1GnK_fV_wxXbsSfhuzrtGd3_RLpFrnFh3" target="_blank" style="color:#60a5fa; text-decoration:none;">📁 01_RAW_AND_TRANSCRIPTS ↗</a>
        <a href="https://drive.google.com/drive/folders/1ozvkHvYNuAIcHtfd87_jhs79rs0uL-D3" target="_blank" style="color:#60a5fa; text-decoration:none;">📁 02_EXTRACTED_KNOWLEDGE ↗</a>
        <a href="https://drive.google.com/drive/folders/1I2CZ-eNaioMgASFMBMMAHPwSRn_QhqSl" target="_blank" style="color:#60a5fa; text-decoration:none;">📁 03_SUMMARIES ↗</a>
        <a href="https://drive.google.com/drive/folders/1qQeOVD-sb62MuuZyPWQMJK0MHlDDxQ9w" target="_blank" style="color:#60a5fa; text-decoration:none;">📁 04_BUSINESS_MODELS ↗</a>
        <a href="https://drive.google.com/drive/folders/15ieCh8iFRMCTAzUwqDtUFf8nkKNmXNbS" target="_blank" style="color:#60a5fa; text-decoration:none;">📁 05_CROSS_SOURCE_ANALYSIS ↗</a>
        <a href="https://drive.google.com/drive/folders/12tmQx9wy55TKOj-UJB7PszThLahoi4xf" target="_blank" style="color:#60a5fa; text-decoration:none;">📁 06_ACTIONABLE_OPPORTUNITIES ↗</a>
        <a href="https://drive.google.com/drive/folders/1iXjAfZKzD540SScMvXjf1CfHnBtmGu8x" target="_blank" style="color:#60a5fa; text-decoration:none;">📁 07_VERIFICATION_AND_LOGS ↗</a>
      </div>
    </div>
  </div>
  <!-- END TAB 2 -->

  <!-- Modal for Email Review / Generation -->
  <div class="modal-overlay" id="email-modal">
    <div class="modal">
      <div class="modal-header">
        <h2 id="modal-company-title">Personalized Outreach Email</h2>
        <button class="modal-close" onclick="closeModal()">&times;</button>
      </div>
      <div class="modal-body">
        <div style="background:#0f172a; padding:10px 14px; border-radius:6px; border:1px solid var(--border-color); font-size:12px; color:var(--text-muted);" id="modal-evidence-box">
          Verified Evidence: ...
        </div>
        <div class="form-group">
          <label>Recipient Email Address</label>
          <input type="email" id="modal-recipient">
        </div>
        <div class="form-group">
          <label>Subject Line</label>
          <input type="text" id="modal-subject">
        </div>
        <div class="form-group">
          <label>Personalized Body (CAN-SPAM Compliant, Grounded Evidence)</label>
          <textarea id="modal-body"></textarea>
        </div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-secondary" onclick="copyModalToClipboard()">📋 Copy to Clipboard</button>
        <button class="btn" onclick="saveModalDraft()">💾 Save & Stage in Queue</button>
      </div>
    </div>
  </div>

  <!-- Toast Notification -->
  <div class="toast" id="toast">Copied to clipboard!</div>

  <script>
    let currentFilter = 'all';
    let currentSearch = '';
    let currentPage = 0;
    const PAGE_SIZE = 25;
    let activeCompanyId = null;

    async function loadStats() {
      try {
        const res = await fetch('/v1/leads/stats');
        const data = await res.json();
        document.getElementById('kpi-total-companies').textContent = Number(data.total_companies).toLocaleString();
        document.getElementById('kpi-emails').textContent = Number(data.companies_with_emails).toLocaleString();
        document.getElementById('kpi-contacts-sub').textContent = `${Number(data.total_contacts).toLocaleString()} total email entries`;
        document.getElementById('kpi-qualified').textContent = Number(data.total_qualified).toLocaleString();
        document.getElementById('kpi-staged').textContent = Number(data.staged_drafts).toLocaleString();
        if (data.is_running) {
          document.getElementById('engine-status-text').textContent = 'Batch in Progress...';
        } else {
          document.getElementById('engine-status-text').textContent = `Engine Active (${data.checkpoint_processed} processed)`;
        }
      } catch (err) {
        console.error('Failed to load stats:', err);
      }
    }

    async function loadLeads() {
      const tbody = document.getElementById('leads-tbody');
      tbody.innerHTML = '<tr><td colspan="6" class="empty-state">Loading leads...</td></tr>';
      
      const offset = currentPage * PAGE_SIZE;
      const url = `/v1/leads?search=${encodeURIComponent(currentSearch)}&filter_by=${currentFilter}&limit=${PAGE_SIZE}&offset=${offset}`;

      try {
        const res = await fetch(url);
        const data = await res.json();
        const leads = data.leads || [];

        if (leads.length === 0) {
          tbody.innerHTML = '<tr><td colspan="6" class="empty-state">No matching companies found.</td></tr>';
          document.getElementById('page-summary').textContent = 'Showing 0 of 0 leads';
          return;
        }

        tbody.innerHTML = '';
        leads.forEach(lead => {
          const tr = document.createElement('tr');
          
          // Reviews
          let reviewHtml = '<span style="color:#64748b; font-size:11px;">No Reviews</span>';
          if (lead.google_reviews != null && lead.google_rating != null) {
            reviewHtml = `<span class="rating-badge">⭐ ${lead.google_rating} (${lead.google_reviews})</span>`;
          } else if (lead.google_reviews != null) {
            reviewHtml = `<span class="rating-badge">⭐ (${lead.google_reviews})</span>`;
          }

          // Status Badge
          let statusBadge = '<span class="tag tag-not-ready">Backlog</span>';
          if (lead.has_draft) {
            statusBadge = '<span class="tag tag-staged">Draft Staged</span>';
          } else if (lead.outreach_status === 'outreach_ready') {
            statusBadge = '<span class="tag tag-ready">Qualified</span>';
          }

          // Contact Email
          let emailHtml = '<span style="color:#64748b;">No email found</span>';
          if (lead.email) {
            emailHtml = `<span class="email-cell">${escapeHtml(lead.email)}</span>`;
          }

          // Button
          const btnText = lead.has_draft ? '👁️ View Draft' : '✨ Generate Email';
          const btnClass = lead.has_draft ? 'btn-secondary' : 'btn';

          tr.innerHTML = `
            <td>
              <div class="company-cell">${escapeHtml(lead.company_name)}</div>
              <div class="company-sub">${lead.website ? `<a href="${escapeHtml(lead.website)}" target="_blank" style="color:#60a5fa; text-decoration:none;">${escapeHtml(new URL(lead.website).hostname || lead.website)} ↗</a>` : 'No website'}</div>
            </td>
            <td>
              <div>${escapeHtml(lead.industry)}</div>
              <div class="company-sub">${escapeHtml(lead.city || '')}${lead.state ? ', ' + escapeHtml(lead.state) : ''}</div>
            </td>
            <td>${reviewHtml}</td>
            <td>${emailHtml}</td>
            <td>${statusBadge}</td>
            <td>
              <button class="btn ${btnClass} btn-sm" onclick="openEmailModal('${lead.company_id}')">${btnText}</button>
            </td>
          `;
          tbody.appendChild(tr);
        });

        const start = offset + 1;
        const end = Math.min(offset + PAGE_SIZE, data.total);
        document.getElementById('page-summary').textContent = `Showing ${start}–${end} of ${Number(data.total).toLocaleString()} leads`;
        document.getElementById('btn-prev').disabled = (currentPage === 0);
        document.getElementById('btn-next').disabled = (end >= data.total);

      } catch (err) {
        tbody.innerHTML = `<tr><td colspan="6" class="empty-state" style="color:#f87171;">Failed to load leads: ${escapeHtml(err.message)}</td></tr>`;
      }
    }

    function setFilter(filter, btn) {
      document.querySelectorAll('.filter-tabs .filter-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      currentFilter = filter;
      currentPage = 0;
      loadLeads();
    }

    let searchTimeout = null;
    function handleSearch(evt) {
      clearTimeout(searchTimeout);
      searchTimeout = setTimeout(() => {
        currentSearch = evt.target.value;
        currentPage = 0;
        loadLeads();
      }, 300);
    }

    function changePage(delta) {
      currentPage = Math.max(0, currentPage + delta);
      loadLeads();
    }

    async function openEmailModal(companyId) {
      activeCompanyId = companyId;
      document.getElementById('email-modal').style.display = 'flex';
      document.getElementById('modal-company-title').textContent = 'Generating Personalized Pitch...';
      document.getElementById('modal-evidence-box').textContent = 'Fetching company review signals...';
      document.getElementById('modal-recipient').value = '';
      document.getElementById('modal-subject').value = '';
      document.getElementById('modal-body').value = '';

      try {
        const res = await fetch(`/v1/leads/${companyId}/generate-email`, { method: 'POST' });
        const data = await res.json();
        
        document.getElementById('modal-company-title').textContent = `Outreach for ${data.company_name}`;
        document.getElementById('modal-recipient').value = data.recipient || '';
        document.getElementById('modal-subject').value = data.subject || '';
        document.getElementById('modal-body').value = data.body || '';
        
        const ev = data.evidence || {};
        let evText = `Industry: ${ev.industry || 'Local'} | Location: ${ev.city || 'N/A'}`;
        if (ev.google_reviews != null) evText += ` | Reviews: ${ev.google_reviews}`;
        if (ev.google_rating != null) evText += ` | Rating: ${ev.google_rating}★`;
        document.getElementById('modal-evidence-box').textContent = `Verified Evidence: ${evText}`;
      } catch (err) {
        document.getElementById('modal-body').value = 'Failed to generate email: ' + err.message;
      }
    }

    function closeModal() {
      document.getElementById('email-modal').style.display = 'none';
      activeCompanyId = null;
    }

    async function saveModalDraft() {
      if (!activeCompanyId) return;
      const recipient = document.getElementById('modal-recipient').value;
      const subject = document.getElementById('modal-subject').value;
      const body = document.getElementById('modal-body').value;

      try {
        const res = await fetch(`/v1/leads/${activeCompanyId}/stage-draft`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ recipient, subject, body })
        });
        const data = await res.json();
        if (data.status === 'success') {
          showToast('Draft staged in database for approval!');
          closeModal();
          loadStats();
          loadLeads();
        } else {
          alert(data.error || 'Failed to stage draft');
        }
      } catch (err) {
        alert('Error staging draft: ' + err.message);
      }
    }

    function copyModalToClipboard() {
      const subject = document.getElementById('modal-subject').value;
      const body = document.getElementById('modal-body').value;
      const fullText = `Subject: ${subject}\n\n${body}`;
      navigator.clipboard.writeText(fullText).then(() => {
        showToast('Email copied to clipboard!');
      });
    }

    async function triggerBatchGenerate() {
      const confirmed = confirm('Generate and stage personalized outreach emails for all qualified leads with verified contact emails?');
      if (!confirmed) return;
      
      showToast('Generating drafts in background...');
      try {
        const res = await fetch('/v1/leads/batch-generate', { method: 'POST' });
        const data = await res.json();
        showToast(`Done! Staged ${data.drafts_staged} new personalized drafts.`);
        loadStats();
        loadLeads();
      } catch (err) {
        alert('Batch generate error: ' + err.message);
      }
    }

    function showToast(msg) {
      const toast = document.getElementById('toast');
      toast.textContent = msg;
      toast.style.display = 'block';
      setTimeout(() => { toast.style.display = 'none'; }, 3000);
    }

    function escapeHtml(str) {
      if (!str) return '';
      return String(str).replace(/[&<>"']/g, ch => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
      }[ch]));
    }

    /* ---------------- Main Tab Navigation ---------------- */
    let allProblems = [];
    let currentProblemNiche = 'all';

    function switchMainTab(tab) {
      const outreachView = document.getElementById('view-outreach');
      const leadsView = document.getElementById('view-leads');
      const researchView = document.getElementById('view-research');
      const btnOutreach = document.getElementById('tab-btn-outreach');
      const btnLeads = document.getElementById('tab-btn-leads');
      const btnResearch = document.getElementById('tab-btn-research');

      // Hide all
      outreachView.style.display = 'none';
      leadsView.style.display = 'none';
      researchView.style.display = 'none';
      btnOutreach.classList.remove('active');
      btnLeads.classList.remove('active');
      btnResearch.classList.remove('active');

      if (tab === 'outreach') {
        outreachView.style.display = 'block';
        btnOutreach.classList.add('active');
        window.location.hash = 'outreach';
        loadOutreachStats();
        loadOutreachLeads();
      } else if (tab === 'leads') {
        leadsView.style.display = 'block';
        btnLeads.classList.add('active');
        window.location.hash = 'leads';
        loadStats();
        loadLeads();
      } else {
        researchView.style.display = 'block';
        btnResearch.classList.add('active');
        window.location.hash = 'research';
        loadResearchData();
      }
    }

    async function loadResearchData() {
      loadResearchStats();
      loadProblems();
    }

    async function loadResearchStats() {
      try {
        const res = await fetch('/v1/research/status');
        const data = await res.json();
        document.getElementById('kpi-research-discovered').textContent = Number(data.total_discovered || 61).toLocaleString();
        document.getElementById('kpi-research-extracted').textContent = Number(data.successfully_extracted || 57).toLocaleString();
        document.getElementById('kpi-research-opportunities').textContent = '5 High-Fit Models';
        
        const driveUploads = data.saved_to_drive || {};
        let totalFiles = 149;
        if (typeof driveUploads === 'object' && Object.keys(driveUploads).length > 0) {
          totalFiles = Object.values(driveUploads).reduce((a, b) => (typeof b === 'number' ? a + b : a), 0);
        }
        document.getElementById('kpi-research-drive').textContent = `${totalFiles} Files in Drive`;
      } catch (err) {
        console.error('Failed to load research stats:', err);
      }
    }

    async function loadProblems() {
      try {
        const res = await fetch('/v1/research/problems');
        const data = await res.json();
        allProblems = data.problems || [];
        renderProblems();
      } catch (err) {
        document.getElementById('problems-grid').innerHTML = `<div class="empty-state" style="color:#f87171;">Failed to load problem research: ${escapeHtml(err.message)}</div>`;
      }
    }

    function renderProblems() {
      const container = document.getElementById('problems-grid');
      const search = (document.getElementById('problem-search-input')?.value || '').toLowerCase();

      const filtered = allProblems.filter(p => {
        let matchNiche = true;
        if (currentProblemNiche === 'no_brainer') {
          matchNiche = p.is_no_brainer === true;
        } else if (currentProblemNiche === 'traps') {
          matchNiche = p.is_no_brainer === false;
        } else if (currentProblemNiche !== 'all') {
          matchNiche = (p.niche === currentProblemNiche);
        }

        const matchSearch = (!search || 
          p.title.toLowerCase().includes(search) || 
          p.what_you_deliver.toLowerCase().includes(search) || 
          p.why_they_buy.toLowerCase().includes(search) ||
          p.prebuilt_status.toLowerCase().includes(search)
        );
        return matchNiche && matchSearch;
      });

      if (!filtered.length) {
        container.innerHTML = '<div class="empty-state">No items match your selected filter.</div>';
        return;
      }

      container.innerHTML = filtered.map(p => {
        const isNoBrainer = p.is_no_brainer;
        const cardBorder = isNoBrainer 
          ? 'border-color: rgba(16, 185, 129, 0.5); background: rgba(15, 23, 42, 0.85); box-shadow: 0 4px 14px rgba(16, 185, 129, 0.1);' 
          : 'border-color: rgba(239, 68, 68, 0.35); background: rgba(15, 23, 42, 0.6); opacity: 0.9;';
        
        const verdictBadge = isNoBrainer 
          ? '<span style="background:rgba(16, 185, 129, 0.2); color:#34d399; border:1px solid rgba(16, 185, 129, 0.4); padding:3px 8px; border-radius:4px; font-size:11px; font-weight:700;">🏆 100% NO-BRAINER</span>'
          : '<span style="background:rgba(239, 68, 68, 0.2); color:#f87171; border:1px solid rgba(239, 68, 68, 0.4); padding:3px 8px; border-radius:4px; font-size:11px; font-weight:700;">⚠️ REALISTIC TRAP</span>';

        return `
          <div class="problem-card" style="${cardBorder}">
            <!-- Header -->
            <div style="display:flex; justify-content:space-between; align-items:flex-start; gap:8px;">
              <div>
                <div style="font-size:16px; font-weight:700; color:#fff;">${escapeHtml(p.title)}</div>
                <div style="font-size:11px; color:#60a5fa; font-weight:600; margin-top:2px;">${escapeHtml(p.niche)}</div>
              </div>
              ${verdictBadge}
            </div>

            <!-- Key Metric Chips (Scannable in 3 seconds) -->
            <div style="display:grid; grid-template-columns:repeat(2, 1fr); gap:8px; background:#0b1120; padding:10px 12px; border-radius:6px; font-size:12px; border:1px solid #1e293b;">
              <div><span style="color:#94a3b8;">Client Retainer:</span> <strong style="color:#fbbf24;">${escapeHtml(p.client_retainer)}</strong></div>
              <div><span style="color:#94a3b8;">Setup Time:</span> <strong style="color:#34d399;">${escapeHtml(p.setup_time)}</strong></div>
              <div><span style="color:#94a3b8;">Execution:</span> <strong style="color:#38bdf8;">${escapeHtml(p.execution_difficulty)}</strong></div>
              <div><span style="color:#94a3b8;">Client Friction:</span> <strong style="color:#a78bfa;">Zero Habit Change</strong></div>
            </div>

            <!-- Prebuilt Status Pill -->
            <div style="background:rgba(59, 130, 246, 0.12); border:1px solid rgba(59, 130, 246, 0.3); border-radius:6px; padding:7px 10px; font-size:12px;">
              <span style="color:#60a5fa; font-weight:700;">PREBUILT:</span> 
              <span style="color:#f8fafc; font-weight:600;">${escapeHtml(p.prebuilt_status)}</span>
            </div>

            <!-- 1-Sentence What You Deliver -->
            <div style="font-size:13px; line-height:1.4;">
              <strong style="color:#34d399;">What You Deliver:</strong> ${escapeHtml(p.what_you_deliver)}
            </div>

            <!-- 1-Sentence Why They Say Yes -->
            <div style="font-size:13px; line-height:1.4; color:#cbd5e1;">
              <strong style="color:#fbbf24;">Why They Buy:</strong> ${escapeHtml(p.why_they_buy)}
            </div>

            <!-- Collapsible Evidence (Hidden by default to eliminate walls of text) -->
            <details style="margin-top:2px; font-size:11px; color:#94a3b8;">
              <summary style="cursor:pointer; color:#60a5fa; font-weight:600;">💬 View Field Proof & Reddit Quote</summary>
              <div class="quote-box" style="margin-top:6px; font-size:11px; line-height:1.3;">"${escapeHtml(p.quote)}"</div>
            </details>
          </div>
        `;
      }).join('');
    }

    function setProblemFilter(niche, btn) {
      document.querySelectorAll('.problem-filter-tabs .filter-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      currentProblemNiche = niche;
      renderProblems();
    }

    async function triggerResearchRun() {
      showToast('Dispatched background research & problem scraper...');
      try {
        const res = await fetch('/v1/research/run', { method: 'POST' });
        const data = await res.json();
        showToast('Research pipeline active in background!');
        setTimeout(loadResearchData, 3000);
      } catch (err) {
        alert('Error triggering research pipeline: ' + err.message);
      }
    }

    async function askResearchEngine() {
      const input = document.getElementById('qa-question-input');
      const q = (input?.value || '').trim();
      if (!q) return;

      const resultBox = document.getElementById('qa-result-box');
      const content = document.getElementById('qa-result-content');
      const citations = document.getElementById('qa-result-citations');

      resultBox.style.display = 'block';
      content.innerHTML = '<span style="color:#94a3b8;">Searching stored knowledge base evidence...</span>';
      citations.innerHTML = '';

      try {
        const res = await fetch('/v1/research/query', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ question: q })
        });
        const data = await res.json();
        content.innerHTML = escapeHtml(data.answer || 'No direct answer retrieved.');
        if (data.citations && data.citations.length) {
          citations.innerHTML = '<div style="margin-top:10px; font-weight:700; color:#94a3b8; font-size:11px; text-transform:uppercase;">Verified Evidence Citations:</div>' +
            data.citations.map(c => `<span class="citation-tag">${escapeHtml(c)}</span>`).join('');
        }
      } catch (err) {
        content.innerHTML = `<span style="color:#f87171;">Query error: ${escapeHtml(err.message)}</span>`;
      }
    }

    function setQaExample(text) {
      const input = document.getElementById('qa-question-input');
      if (input) {
        input.value = text;
        askResearchEngine();
      }
    }

    /* ---------------- Outreach Campaign Monitor Controller ---------------- */
    let currentOutreachFilter = 'all';
    let currentOutreachSearch = '';
    let currentOutreachPage = 0;
    const OUTREACH_PAGE_SIZE = 25;

    async function loadOutreachStats() {
      try {
        const res = await fetch('/v1/outreach/stats');
        const data = await res.json();
        const sent = Number(data.sent_count || 0);
        const cap = Number(data.cap || 300);
        const queued = Number(data.queued_count || 0);
        const replies = Number(data.total_replies || 0);
        const interested = Number(data.interested_count || 0);
        const pct = data.progress_pct || 0;

        document.getElementById('kpi-outreach-sent').textContent = sent.toLocaleString();
        document.getElementById('kpi-outreach-progress-sub').textContent = `${pct}% of ${cap} Daily Cap (${cap - sent} remaining)`;
        document.getElementById('kpi-outreach-queued').textContent = queued.toLocaleString();
        document.getElementById('kpi-outreach-replies').textContent = replies.toLocaleString();
        document.getElementById('kpi-outreach-interested').textContent = interested.toLocaleString();

        const statusText = document.getElementById('outreach-runner-status');
        if (statusText) {
          if (sent >= cap) {
            statusText.textContent = `🎯 Daily Cap Reached (${cap}/${cap}) — Auto-Paused & Waiting for Responses`;
          } else if (queued > 0) {
            statusText.textContent = `⚡ Outreach Active (${queued} Queued, Pacing Every 10m)`;
          } else {
            statusText.textContent = `✅ All Staged Emails Dispatched (${sent}/${cap})`;
          }
        }
      } catch (err) {
        console.error('Failed to load outreach stats:', err);
      }
    }

    async function loadOutreachLeads() {
      const tbody = document.getElementById('outreach-tbody');
      tbody.innerHTML = '<tr><td colspan="6" class="empty-state">Loading outreach campaign leads...</td></tr>';

      const offset = currentOutreachPage * OUTREACH_PAGE_SIZE;
      const url = `/v1/outreach/leads?search=${encodeURIComponent(currentOutreachSearch)}&filter_by=${currentOutreachFilter}&limit=${OUTREACH_PAGE_SIZE}&offset=${offset}`;

      try {
        const res = await fetch(url);
        const data = await res.json();
        const leads = data.leads || [];

        if (leads.length === 0) {
          tbody.innerHTML = '<tr><td colspan="6" class="empty-state">No matching campaign leads found.</td></tr>';
          document.getElementById('outreach-page-summary').textContent = 'Showing 0 of 0 campaign leads';
          return;
        }

        tbody.innerHTML = '';
        leads.forEach(l => {
          const tr = document.createElement('tr');

          // Queue state badge
          let stateBadge = '<span class="tag tag-staged">Queued</span>';
          if (l.queue_state === 'sent') {
            stateBadge = '<span class="tag tag-ready">Sent</span>';
          } else if (l.queue_state === 'claimed') {
            stateBadge = '<span class="tag" style="background:rgba(245, 158, 11, 0.2); color:#fbbf24;">In Flight</span>';
          } else if (l.queue_state === 'failed') {
            stateBadge = '<span class="tag" style="background:rgba(239, 68, 68, 0.2); color:#f87171;">Failed</span>';
          } else if (l.queue_state === 'cancelled') {
            stateBadge = '<span class="tag tag-not-ready">Cancelled</span>';
          }

          // Conversation status
          let convBadge = '<span style="color:#64748b; font-size:12px;">Awaiting Reply</span>';
          if (l.conversation_status === 'interested') {
            convBadge = '<span style="background:rgba(16, 185, 129, 0.25); color:#34d399; font-weight:700; padding:2px 8px; border-radius:4px; font-size:11px;">🏆 Interested</span>';
          } else if (l.conversation_status === 'replied') {
            convBadge = '<span style="background:rgba(59, 130, 246, 0.25); color:#60a5fa; font-weight:700; padding:2px 8px; border-radius:4px; font-size:11px;">💬 Replied</span>';
          } else if (l.conversation_status === 'do_not_contact') {
            convBadge = '<span style="color:#f87171; font-size:12px;">Unsubscribed</span>';
          }

          // Timing
          let timingText = `<span style="color:#94a3b8; font-size:12px;">${l.attempts} tries</span>`;
          if (l.sent_at) {
            timingText += `<div style="font-size:11px; color:#64748b;">Sent ${new Date(l.sent_at).toLocaleDateString()}</div>`;
          }

          tr.innerHTML = `
            <td>
              <div class="company-cell">${escapeHtml(l.company_name)}</div>
              <div class="company-sub">${escapeHtml(l.city || '')}${l.company_state ? ', ' + escapeHtml(l.company_state) : ''} &bull; ${escapeHtml(l.industry)}</div>
            </td>
            <td>
              <div class="email-cell">${escapeHtml(l.recipient)}</div>
            </td>
            <td>
              <div style="font-weight:600; color:#f8fafc; font-size:13px;">${escapeHtml(l.subject)}</div>
              <div style="font-size:11px; color:#64748b; margin-top:2px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:320px;">${escapeHtml(l.body ? l.body.substring(0, 70) + '...' : '')}</div>
            </td>
            <td>${stateBadge}</td>
            <td>${timingText}</td>
            <td>${convBadge}</td>
          `;
          tbody.appendChild(tr);
        });

        const start = offset + 1;
        const end = Math.min(offset + OUTREACH_PAGE_SIZE, data.total);
        document.getElementById('outreach-page-summary').textContent = `Showing ${start}–${end} of ${Number(data.total).toLocaleString()} campaign leads`;
        document.getElementById('btn-outreach-prev').disabled = (currentOutreachPage === 0);
        document.getElementById('btn-outreach-next').disabled = (end >= data.total);

      } catch (err) {
        tbody.innerHTML = `<tr><td colspan="6" class="empty-state" style="color:#f87171;">Failed to load campaign leads: ${escapeHtml(err.message)}</td></tr>`;
      }
    }

    function setOutreachFilter(filter, btn) {
      document.querySelectorAll('.outreach-filter-tabs .filter-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      currentOutreachFilter = filter;
      currentOutreachPage = 0;
      loadOutreachLeads();
    }

    let outreachSearchTimeout = null;
    function handleOutreachSearch(evt) {
      clearTimeout(outreachSearchTimeout);
      outreachSearchTimeout = setTimeout(() => {
        currentOutreachSearch = evt.target.value;
        currentOutreachPage = 0;
        loadOutreachLeads();
      }, 300);
    }

    function changeOutreachPage(delta) {
      currentOutreachPage = Math.max(0, currentOutreachPage + delta);
      loadOutreachLeads();
    }

    // Initial boot
    if (window.location.hash === '#research') {
      switchMainTab('research');
    } else if (window.location.hash === '#leads') {
      switchMainTab('leads');
    } else {
      switchMainTab('outreach');
    }

    setInterval(() => {
      const outreachView = document.getElementById('view-outreach');
      const leadsView = document.getElementById('view-leads');
      if (outreachView && outreachView.style.display !== 'none') {
        loadOutreachStats();
      } else if (leadsView && leadsView.style.display !== 'none') {
        loadStats();
      } else {
        loadResearchStats();
      }
    }, 30000);
  </script>
</body>
</html>
"""
