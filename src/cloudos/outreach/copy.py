"""Outreach copy: short, outcome-first, grounded ONLY in facts the lead engine read
from the business's own site or public map listing.

What is sold: more new leads, reactivating leads they already have, and making
sure inquiries get followed up. What is never mentioned: how it is done (no AI,
automation, bots, software, scraping). qa() refuses any email that breaks this.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from urllib.parse import quote

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


# Bump when first-touch wording changes: queued, never-attempted first touches written
# with an older version are rewritten before they go out (sender.refresh_queued).
COPY_VERSION = "v2"

# v3 = A/B challenger to v2 (config: experiment.v3_share / experiment.v3_industries).
# Shorter (<= 90 words above the footer), one concrete outcome for trades, signed with the brand.
V3 = "v3"
V3_MAX_WORDS = 90
V3_BRAND = "BrightReach Media"
V3_TRADE = {"hvac": "HVAC company", "plumbing": "plumber", "roofing": "roofer", "electrical": "electrician"}

# v4 = Missed-Call Rescue (revenue-operator/sales/offer.md): a no-risk guarantee + a reply-for-demo CTA.
# Touch 1 has NO link (deliverability); touches 2-4 carry the personalized demo URL / free check / close.
V4 = "v4"
V4_MAX_WORDS = 120
V4_TRADE = {"hvac": "HVAC company", "plumbing": "plumber", "roofing": "roofer"}
V4_TRADE_PLURAL = {"hvac": "HVAC companies", "plumbing": "plumbers", "roofing": "roofers"}
# LocaliQ 2025 cost per lead (research/competitor_analysis.md section 3, via skygnosis.com)
V4_LEAD_COST = {"hvac": ("an A/C", "$128"), "plumbing": ("a plumbing", "$129"), "roofing": ("a roofing", "$228")}
DEMO_URL = "https://mattumali579.github.io/office-agent-demo/missed-call/"
_URL = re.compile(r"https?://\S+")


def demo_url(row: dict) -> str:
    """Personalized demo link: ?biz=<display name>&trade=<industry>, URL-encoded (demo.js reads both)."""
    ind = (row.get("industry") or "").strip().lower()
    return f"{DEMO_URL}?biz={quote(display_name(row.get('company_name', '')), safe='')}&trade={quote(ind, safe='')}"


def _v4_variant_b(company_id: str) -> bool:
    """Deterministic subject A/B inside the v4 arm (independent of the arm hash: uses a salt)."""
    return int(hashlib.sha256(f"subj:{company_id}".encode()).hexdigest()[:8], 16) % 2 == 1


def first_touch_v4(row: dict, *, sender_name: str, postal_address: str) -> Email:
    name = display_name(row.get("company_name", ""))
    ind = (row.get("industry") or "").strip().lower()
    trade = V4_TRADE.get(ind, "company")
    city = (row.get("city") or "").strip()
    _, tag = observation(row)
    short = len(name) <= 28
    who = f"most people in {city}" if city and len(city) <= 20 else "most people"
    ref = name if short else "your shop"          # long names: greet with it once, then keep it short
    if tag == "emergency":
        first = (f"I saw {'you take' if not short else name + ' takes'} emergency calls. When one of those goes to "
                 f"voicemail, {who} just call the next {trade} on the list.")
    elif tag == "estimates":
        first = (f"I saw {'you offer' if not short else name + ' offers'} free estimates. When a call goes to voicemail, "
                 f"or an estimate goes quiet, {who} just go with the next {trade}.")
    else:
        first = f"When a call to {ref} goes to voicemail, {who} just call the next {trade} on the list."
    lines = [first, "",
             "I fix that: every missed caller gets a text from your business within seconds, it asks what they need, "
             "and you get the lead on your phone. Quotes that go quiet get three follow-up texts.", "",
             "If it doesn't bring back at least 5 missed callers in your first 30 days, you don't pay. No contract.", "",
             "Want a 1-minute demo with your company name on it? Just reply \"yes\"."]
    b = _v4_variant_b(str(row.get("company_id", name)))
    if b:
        subject = f"Quick question about {name}" if short else "Quick question about your missed calls"
    else:
        subject = f"Missed calls at {name}" if short else "Missed calls"
    body = "\n".join([f"Hi {name} team,", "", *lines, "", *_v3_signature(sender_name), "", "--", postal_address,
                      "If you'd rather not hear from me, reply \"stop\" and I won't email you again."])
    return Email(subject=subject, body=body, variant=f"{V4}-{tag}{'-b' if b else ''}")


def followup_v4(row: dict, step: int, first_subject: str, *, sender_name: str, postal_address: str) -> Email:
    name = display_name(row.get("company_name", ""))
    ind = (row.get("industry") or "").strip().lower()
    city = (row.get("city") or "").strip()
    subj = first_subject if first_subject.lower().startswith("re:") else f"Re: {first_subject}"
    if step == 1:      # touch 2: the personalized demo link
        lines = [f"Hi {name} team,", "",
                 f"I built a quick demo with your name on it: {demo_url(row)}", "",
                 "Tap to miss the call, then watch the text conversation and the lead land on the owner's phone. "
                 "Takes about a minute.", "",
                 "Same deal: at least 5 missed callers brought back in your first 30 days, or you pay nothing.", "",
                 "Want it live for your shop? Reply \"yes\" and I'll set up a 10-minute call."]
    elif step == 2:    # touch 3: ROI math + free missed-call check
        kind, cost = V4_LEAD_COST.get(ind, ("", ""))
        math = (f"Quick math: {kind} lead costs about {cost} in ads (LocaliQ, 2025). "
                "Every unanswered call is that money handed to the next company.") if cost else \
               "Every unanswered call is a lead you already paid for, handed to the next company."
        lines = [f"Hi {name} team,", "", math, "",
                 "Want a free missed-call check? Pull up your phone's recent calls and in 10 minutes I'll count "
                 "how many callers you missed last week. Nothing to install, nothing to buy.", "",
                 "Reply \"check\" with a good time."]
    else:              # touch 4: scarcity + close the loop
        where = f" in {city}" if city else " per area"
        lines = [f"Hi {name} team,", "",
                 f"Last note. I'm keeping this to 3 {V4_TRADE_PLURAL.get(ind, 'shops')}{where} so the shops I work with "
                 "aren't competing for the same callers.", "",
                 "If missed calls ever cost you a job, reply \"rescue\". You pay nothing unless it brings back "
                 "5 missed callers in 30 days."]
    body = "\n".join(lines + ["", *_v3_signature(sender_name), "", "--", postal_address,
                              "Reply \"stop\" and you won't hear from me again."])
    return Email(subject=subj, body=body, variant=f"{V4}-fu{step}")



def arm(company_id: str, industry: str, *, share: float, industries,
        v4_share: float = 0.0, v4_industries=()) -> str:
    """Which copy a company gets: 'v4', 'v3' or 'v2'. Deterministic per company (same answer on every
    run, so a rewrite never flips a lead between arms); only industries the arm has copy for.
    One hash bucket: [0, v4_share) -> v4, [v4_share, v4_share + share) -> v3, rest -> v2."""
    ind = (industry or "").strip().lower()
    if ind not in V3_TRADE:
        return COPY_VERSION
    bucket = int(hashlib.sha256(str(company_id).encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    v4 = v4_share if ind in V4_TRADE and ind in set(v4_industries or ()) else 0.0
    if bucket < v4:
        return V4
    if share > 0 and ind in set(industries or ()) and bucket < v4 + share:
        return V3
    return COPY_VERSION


def _v3_signature(sender_name: str) -> list[str]:
    return [sender_name] if V3_BRAND.lower() in sender_name.lower() else [sender_name, V3_BRAND]


def first_touch_v3(row: dict, *, sender_name: str, postal_address: str) -> Email:
    """One outcome, no price: every missed call gets a text back fast / every estimate gets a
    same-day follow-up (recovery_build deliverables in config/brightreach_offers.yaml)."""
    name = display_name(row.get("company_name", ""))
    trade = V3_TRADE.get((row.get("industry") or "").strip().lower(), "company")
    _, tag = observation(row)
    short = len(name) <= 28
    if tag == "estimates":
        lines = [f"I saw {name} offers free estimates. Most estimates that end with \"let me think about it\" "
                 "never get a second follow-up, and the job goes to whoever checks back first.",
                 "",
                 "I make sure every estimate gets a same-day follow-up until it's a clear yes or no."]
        subject = f"Estimates at {name}" if short else "Quiet estimates"
    else:
        first = (f"I saw {name} takes emergency calls. When one of those goes to voicemail" if tag == "emergency"
                 else f"When a call to {name} goes to voicemail")
        lines = [f"{first}, most people just call the next {trade} on the list.",
                 "",
                 "I set it up so every missed call gets a text back within a minute, so more of those callers "
                 "book with you instead."]
        subject = f"Missed calls at {name}" if short else "Missed calls"
    body = "\n".join([f"Hi {name} team,", "", *lines, "",
                      "Worth a short outline of how it would work for you? A yes or no is fine.", "",
                      *_v3_signature(sender_name), "", "--", postal_address,
                      "If you'd rather not hear from me, reply \"stop\" and I won't email you again."])
    return Email(subject=subject, body=body, variant=f"{V3}-{'estimates' if tag == 'estimates' else 'missed'}")


def first_touch(row: dict, *, sender_name: str, postal_address: str, version: str = COPY_VERSION) -> Email:
    """One offer per email, chosen by the strongest true fact we have about the business:
    long track record -> reactivate old leads; free estimates -> follow-up on quotes;
    emergency calls -> speed of reply; strong reviews -> new leads; nothing -> reactivation."""
    if version == V4:
        return first_touch_v4(row, sender_name=sender_name, postal_address=postal_address)
    if version == V3:
        return first_touch_v3(row, sender_name=sender_name, postal_address=postal_address)
    name = display_name(row.get("company_name", ""))
    new, old, booked = INDUSTRY.get((row.get("industry") or "").strip().lower(), DEFAULT)
    obs, tag = observation(row)
    short = len(name) <= 28
    if tag == "since":
        opener = obs.split(" - ")[0] + "."
        pitch = (f"Businesses that have been around that long usually have a long list of {old} - "
                 "and most of those people never hear from you again. "
                 f"I help owners reach back out to those people and turn some of them into {booked} - "
                 "without new ad spend.")
        subject = f"Old leads at {name}" if short else "Your old leads"
    elif tag == "estimates":
        opener = f"I saw {name} offers free estimates."
        pitch = ("A lot of estimates end with \"let me think about it\" and then nobody follows up. "
                 "I help businesses like yours follow up on every estimate and inquiry until it's a clear yes or no, "
                 "so fewer jobs go to whoever called back first.")
        subject = "Estimates that go quiet"
    elif tag == "emergency":
        opener = f"I saw {name} takes emergency calls."
        pitch = ("In that line of work, the company that gets back to people first usually gets the job. "
                 "I help businesses like yours make sure every call and inquiry gets a fast reply and a real "
                 "follow-up, so those jobs don't go to the next name on the list.")
        subject = f"Callbacks at {name}" if short else "Getting back to callers first"
    elif tag == "reviews":
        opener = obs.split(" - ")[0] + "."
        pitch = (f"The hard part - earning people's trust - is already done. I help businesses with a reputation "
                 f"like that get in front of more {new}, and make sure those inquiries turn into {booked}.")
        subject = f"More {booked} for {name}" if short else f"More {booked}"
    else:
        opener = obs
        pitch = (f"Most businesses like yours have a pile of {old} - and most of those people never got a second call. "
                 f"I help owners reach back out to those people and turn some of them into {booked} - "
                 "without new ad spend.")
        subject = f"Old leads at {name}" if short else "Your old leads"
    body = "\n".join([
        f"Hi {name} team,",
        "",
        f"{opener} {pitch}",
        "",
        f"Want me to send over how that would work for {name}? A one-word reply is fine.",
        "",
        sender_name,
        "",
        "--",
        postal_address,
        "If you'd rather not hear from me, reply \"stop\" and I won't email you again.",
    ])
    return Email(subject=subject, body=body, variant=f"{COPY_VERSION}-{tag}")


def followup(row: dict, step: int, first_subject: str, *, sender_name: str, postal_address: str,
             version: str = COPY_VERSION) -> Email:
    name = display_name(row.get("company_name", ""))
    new, old, booked = INDUSTRY.get((row.get("industry") or "").strip().lower(), DEFAULT)
    subj = first_subject if first_subject.lower().startswith("re:") else f"Re: {first_subject}"
    if version == V4:
        return followup_v4(row, step, first_subject, sender_name=sender_name, postal_address=postal_address)
    if version == V3:
        if step == 1:     # touch 2: the existing price and the existing 30-day guarantee, nothing new
            lines = [f"Hi {name} team,", "",
                     "Quick follow-up. It's one build that texts back every missed call and follows up every "
                     "estimate the same day - $1,500 one time, and you own it.", "",
                     "If it isn't texting back missed calls and booking jobs within 30 days of going live, "
                     "I keep working free until it does, or you get your money back.", "",
                     f"Want the one-page outline for {name}? A yes or no is fine."]
        else:
            lines = [f"Hi {name} team,", "",
                     f"Last note from me. If missed calls or quiet estimates ever cost {name} a job, "
                     "reply \"outline\" and I'll send it over."]
        body = "\n".join(lines + ["", *_v3_signature(sender_name), "", "--", postal_address,
                                  "Reply \"stop\" and you won't hear from me again."])
        return Email(subject=subj, body=body, variant=f"{V3}-fu{step}")
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
    words = text
    for n in sorted({company_name.strip(), display_name(company_name)} - {"", "your team"}, key=len, reverse=True):
        words = words.replace(n, " ")
    if postal_address.strip():
        words = words.replace(postal_address.strip(), " ")
    words = _URL.sub(" ", words)          # a link's path ('office-agent-demo') is not a pitch about technology
    hit = BANNED.search(words)
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
    if (e.variant or "").startswith(V3 + "-") and len(e.body.split("\n--\n")[0].split()) > V3_MAX_WORDS:
        problems.append("too long for v3")
    if (e.variant or "").startswith(V4 + "-") and len(e.body.split("\n--\n")[0].split()) > V4_MAX_WORDS:
        problems.append("too long for v4")
    if (e.variant or "").startswith(V4 + "-") and not (e.variant or "").startswith(V4 + "-fu") and _URL.search(e.body):
        problems.append("link in v4 first touch")
    return problems
