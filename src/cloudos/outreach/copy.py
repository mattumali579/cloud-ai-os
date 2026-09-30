"""Outreach copy: short, outcome-first, grounded ONLY in facts the lead engine read
from the business's own site or public map listing.

What is sold: more new leads, reactivating leads they already have, and making
sure inquiries get followed up. What is never mentioned: how it is done (no AI,
automation, bots, software, scraping). qa() refuses any email that breaks this.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

# industry -> (what a new customer is called, what old leads are, how they say "a booked job")
INDUSTRY = {
    "roofing": ("roofing jobs", "old estimates", "booked jobs"),
    "hvac": ("service calls and installs", "past quotes and service customers", "booked jobs"),
    "plumbing": ("service calls", "past customers and open quotes", "booked jobs"),
    "electrical": ("new jobs", "past quotes", "booked jobs"),
    "remodeling": ("remodel projects", "estimates that never closed", "signed projects"),
    "painting": ("painting jobs", "estimates that went quiet", "booked jobs"),
    "landscaping": ("new clients", "past estimates and seasonal customers", "booked jobs"),
    "cleaning": ("new clients", "past one-time customers", "recurring clients"),
    "pest_control": ("new service customers", "past one-time treatments", "recurring plans"),
    "gym": ("new members", "past trial sign-ups and old members", "memberships"),
    "martial_arts": ("new students", "past trial-class sign-ups", "enrolled students"),
    "dentist": ("new patients", "patients overdue for a visit", "booked appointments"),
    "med_spa": ("new clients", "past consult requests", "booked treatments"),
    "chiropractor": ("new patients", "past patients who stopped coming", "booked visits"),
    "auto_repair": ("new customers", "past customers due for service", "booked repairs"),
    "veterinary": ("new clients", "pets overdue for a visit", "booked appointments"),
    "law_firm": ("new cases", "past inquiries that never signed", "signed clients"),
    "accounting": ("new clients", "past inquiries and lapsed clients", "signed clients"),
    "real_estate": ("new buyers and sellers", "old leads in your database", "closed deals"),
    "insurance": ("new policyholders", "old quotes that never bound", "written policies"),
    "salon": ("new clients", "clients who haven't booked in a while", "booked appointments"),
    "childcare": ("new families", "families who toured but didn't enroll", "enrollments"),
}
DEFAULT = ("new customers", "old leads you already have", "booked jobs")

# never in a prospect email: how the work is done
BANNED = re.compile(r"\b(ai|a\.i\.|artificial intelligence|automat\w*|bots?|chat ?bots?|n8n|scrap\w*|agents?|"
                    r"software|algorithm\w*|machine learning|llm|gpt|chatgpt|zapier|crm)\b", re.I)
PLACEHOLDER = re.compile(r"\{|\}|\bNone\b|\bnull\b|\bundefined\b|\bnan\b")


@dataclass
class Email:
    subject: str
    body: str
    variant: str


def _facts(row: dict) -> dict:
    f = row.get("personalization") or row.get("personalization_facts") or {}
    if isinstance(f, str):
        try:
            f = json.loads(f)
        except ValueError:
            f = {}
    return f if isinstance(f, dict) else {}


def display_name(name: str) -> str:
    """'ACME ROOFING LLC' -> 'Acme Roofing'. Keeps names that are already mixed case."""
    n = re.sub(r"\s*[,|\-–—:].*$", "", (name or "").strip()) or (name or "").strip()
    n = re.sub(r"\b(llc|l\.l\.c\.|inc\.?|corp\.?|co\.|ltd\.?|pllc|p\.c\.)\s*$", "", n, flags=re.I).strip(" ,.")
    if n.isupper() and len(n) > 4:
        # shouting names read badly; initials and trade acronyms (HVAC, S&S, AC, JB2) stay as written
        keep = {"HVAC", "HVACR", "USA", "DDS", "DMD", "CPA", "PLLC"}
        small = {"AND": "and", "THE": "the", "OF": "of", "&": "&"}
        words = n.split()
        n = " ".join((small[w] if i else w.capitalize()) if w in small else
                     (w.capitalize() if w.isalpha() and len(w) >= 4 and w not in keep else w)
                     for i, w in enumerate(words))
    return n[:60] or "your team"


def observation(row: dict) -> tuple[str, str]:
    """One true sentence about the business, and a short tag of which fact it used."""
    f = _facts(row)
    name = display_name(row.get("company_name", ""))
    city = (row.get("city") or "").strip()
    reviews, rating = f.get("google_reviews"), f.get("google_rating")
    try:
        reviews = int(reviews) if reviews not in (None, "") else None
        rating = float(rating) if rating not in (None, "") else None
    except (TypeError, ValueError):
        reviews, rating = None, None
    if reviews and reviews >= 20 and rating and rating >= 4.5:
        return (f"I came across {name} and saw the {reviews} Google reviews at {rating:g} stars - "
                "that's a reputation a lot of businesses would love to have.", "reviews")
    since = f.get("since_year")
    try:
        since = int(since) if since else None
    except (TypeError, ValueError):
        since = None
    if since and 1900 < since < 2024:
        return (f"I came across {name} and noticed you've been at it since {since} - "
                "that kind of track record usually means a big list of past customers and inquiries.", "since")
    if f.get("free_estimate_offer"):
        return (f"I came across {name} and saw you offer free estimates, which tells me a lot of your "
                "business starts with someone reaching out.", "estimates")
    if f.get("emergency_service"):
        return (f"I came across {name} and saw you take emergency calls - in that business, how fast "
                "someone hears back usually decides who gets the job.", "emergency")
    where = f" in {city}" if city else ""
    return f"I came across {name} while looking at local businesses{where}.", "generic"


def first_touch(row: dict, *, sender_name: str, postal_address: str) -> Email:
    name = display_name(row.get("company_name", ""))
    new, old, booked = INDUSTRY.get((row.get("industry") or "").strip().lower(), DEFAULT)
    obs, tag = observation(row)
    body = "\n".join([
        f"Hi {name} team,",
        "",
        obs,
        "",
        f"I help businesses like yours turn more inquiries into {booked} in three ways: bringing in more {new}, "
        f"getting a second look from {old}, and making sure no one who reaches out falls through the cracks "
        "because nobody followed up.",
        "",
        f"Would it be worth a quick conversation to see if there's more business to get for {name}? "
        "A one-line reply is plenty.",
        "",
        sender_name,
        "",
        "--",
        postal_address,
        "If you'd rather not hear from me, reply \"stop\" and I won't email you again.",
    ])
    subject = f"More {booked} for {name}" if len(name) <= 32 else f"More {booked} this season"
    return Email(subject=subject, body=body, variant=f"v1-{tag}")


def followup(row: dict, step: int, first_subject: str, *, sender_name: str, postal_address: str) -> Email:
    name = display_name(row.get("company_name", ""))
    new, old, booked = INDUSTRY.get((row.get("industry") or "").strip().lower(), DEFAULT)
    if step == 1:
        lines = [
            f"Hi {name} team,",
            "",
            f"Following up on my note below. Most owners I talk to have more {old} sitting around than they "
            f"realize - reaching back out to them is usually the fastest way to add {booked} without new ad spend.",
            "",
            f"Is getting more {new} a priority for you right now, or is the calendar already full?",
        ]
    else:
        lines = [
            f"Hi {name} team,",
            "",
            "I don't want to crowd your inbox, so this is my last note. If more "
            f"{booked} ever becomes a priority, reply any time and I'll show you what I'd do for {name}.",
        ]
    body = "\n".join(lines + ["", sender_name, "", "--", postal_address,
                              "Reply \"stop\" and you won't hear from me again."])
    subj = first_subject if first_subject.lower().startswith("re:") else f"Re: {first_subject}"
    return Email(subject=subj, body=body, variant=f"fu{step}")


def qa(e: Email, *, postal_address: str, company_name: str = "") -> list[str]:
    """Reasons this email must NOT be sent. Empty list = it passes.
    The business's own name is left out of the wording check ('Smith Automotive' is not a pitch)."""
    problems = []
    if not e.subject.strip():
        problems.append("empty subject")
    if len(e.body.strip()) < 120:
        problems.append("body too short")
    text = f"{e.subject}\n{e.body}"
    hit = BANNED.search(text)
    if hit:
        problems.append(f"mentions the technology ('{hit.group(0)}')")
    if PLACEHOLDER.search(text):
        problems.append("unfilled placeholder")
    if not postal_address.strip() or postal_address.strip() not in e.body:
        problems.append("no postal address")
    if '"stop"' not in e.body:
        problems.append("no opt-out line")
    if len(e.body.split()) > 170:
        problems.append("too long")
    return problems
