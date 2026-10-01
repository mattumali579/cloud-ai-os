"""Rule-based qualification. Deterministic so every verdict has a stated reason.

HIGH    real small/medium business in a target industry, validated email on its
        own domain, enough public facts to personalize.
MEDIUM  as HIGH but the email is a published freemail inbox, or facts are thin.
LOW     real business but no usable email yet (not outreach-ready).
REJECT  duplicate, dead/parked site, directory, chain/enterprise, off-target.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from cloudos.leadgen.enrich import Enrichment
from cloudos.leadgen.normalize import FREEMAIL, LEGACY_ISP_MAIL, email_domain, is_directory, normalize_name

OUTREACH_READY_LEVELS = {"HIGH", "MEDIUM"}

_ENTERPRISE_WORDS = re.compile(r"\b(franchis(e|ing) opportunit|investor relations|nasdaq|nyse|fortune 500|\d{3,} locations|locations nationwide|find a location near you)\b", re.I)
_LOCATION_PAGE = re.compile(r"/(locations?|gyms?|clubs?|stores?|offices?|dentist|find-a|studios?|branch(es)?|branch-locator|stores?)/[^/]+", re.I)
_B2B = re.compile(r"\b(supply|supplies|wholesale|distribut\w*|industrial|manufactur\w*|spill|marine|oilfield|offshore|workers.? comp\w*|staffing|logistics|freight|trucking)\b", re.I)
_DEALER = re.compile(r"\b(chevrolet|chevy|toyota|honda|nissan|hyundai|kia|dodge|chrysler|jeep|subaru|mazda|volkswagen|bmw|mercedes|lexus|acura|cadillac|buick|gmc|infiniti|audi)\b", re.I)
_NOT_A_BUSINESS = re.compile(r"\b(city of|county|parish|school district|university|college|church|ministr(y|ies)|government|department of|police|fire dept|hospital|medical center|ymca|foundation)\b", re.I)


@dataclass
class Verdict:
    level: str
    reason: str
    outreach_ready: bool


def chain_match(name: str, website: str, chains: list[str]) -> str:
    norm = f" {normalize_name(name)} "
    host = (website or "").lower()
    for chain in chains:
        key = normalize_name(chain)
        if key and (f" {key} " in norm or key.replace(" ", "") in host.replace("-", "")):
            return chain
    return ""


def qualify(
    *,
    name: str,
    website: str,
    industry: str,
    enrichment: Enrichment | None,
    chains: list[str],
    review_count: int | None = None,
    brand_tag: str = "",
) -> Verdict:
    if not website:
        return Verdict("REJECT", "no public website to verify the business or personalize from", False)
    if is_directory(website):
        return Verdict("REJECT", "directory/aggregator listing, not the business itself", False)
    if _NOT_A_BUSINESS.search(name or ""):
        return Verdict("REJECT", "public body, school, church or hospital - not a lead-buying small business", False)
    chain = brand_tag or chain_match(name, website, chains)
    if chain:
        return Verdict("REJECT", f"national chain/franchise ({chain}) - owner-level lead offer does not fit", False)
    if _DEALER.search(name or ""):
        return Verdict("REJECT", "franchised car dealership - enterprise marketing department", False)
    if _LOCATION_PAGE.search(website):
        return Verdict("REJECT", "website is one location page of a multi-location company", False)
    if _B2B.search(f"{name or ''} {industry or ''}"):
        return Verdict("REJECT", "industrial/B2B supplier - not a local customer-facing business", False)
    path = re.sub(r"^https?://[^/]+", "", website or "").strip("/").lower()
    if path and re.search(r"(baton-rouge|new-orleans|houston|dallas|austin|lafayette|shreveport|[a-z]+-(la|tx|ms|al|fl|ga))\b", path):
        return Verdict("REJECT", "website is a city page of a multi-location company", False)
    if review_count is not None and review_count > 3000:
        return Verdict("REJECT", f"{review_count} reviews - too large for the small-business offer", False)
    if enrichment is None:
        return Verdict("LOW", "not yet enriched", False)
    if enrichment.blocked and not enrichment.emails:
        return Verdict("LOW", f"real {industry} business; its site blocks automated reading and no public listing email", False)
    if enrichment.dead:
        return Verdict("REJECT", f"dead website ({enrichment.error})", False)
    if enrichment.parked:
        return Verdict("REJECT", "parked, empty or for-sale domain", False)

    home_text = " ".join(str(v) for v in enrichment.facts.values())
    if _ENTERPRISE_WORDS.search(home_text):
        return Verdict("REJECT", "site signals a large multi-location enterprise", False)

    best = enrichment.best_email
    if best is None:
        why = "has a contact form" if enrichment.facts.get("has_contact_form") else "no contact form either"
        return Verdict("LOW", f"real {industry} business but no published email ({why})", False)
    if best.status != "validated":
        return Verdict("LOW", f"published email {best.email.split('@')[1]} has no mail server (MX) - would bounce", False)
    mail_domain = email_domain(best.email)
    if mail_domain in LEGACY_ISP_MAIL:
        return Verdict(
            "LOW",
            f"published legacy ISP mailbox ({mail_domain}) - MX exists but mailbox-level bounce risk is too high",
            False,
        )

    fact_keys = {k for k, v in enrichment.facts.items() if v and k not in {"pages_read", "has_chat_or_call_tracking", "has_contact_form"}}
    freemail = mail_domain in FREEMAIL
    angle = []
    if not enrichment.facts.get("online_booking"):
        angle.append("no online booking/quote request found")
    if not enrichment.facts.get("has_chat_or_call_tracking"):
        angle.append("no chat or call-tracking widget found")
    if enrichment.facts.get("free_estimate_offer"):
        angle.append(f"already advertises '{enrichment.facts['free_estimate_offer']}' (lead-driven)")
    angle_text = "; ".join(angle) or "lead-driven local service business"

    if not freemail and len(fact_keys) >= 2:
        return Verdict("HIGH", f"{industry} business, validated own-domain email, {len(fact_keys)} public facts; {angle_text}", True)
    if freemail:
        return Verdict("MEDIUM", f"{industry} business, published freemail inbox (MX ok); {angle_text}", True)
    return Verdict("MEDIUM", f"{industry} business, validated own-domain email, thin public facts; {angle_text}", True)
