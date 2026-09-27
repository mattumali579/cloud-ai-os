"""Deterministic V1 scoring and outreach for BrightReach roofing prospects.

No AI dependency: this module is intentionally cheap, testable, and fail-closed.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RoofingProspect:
    company: str
    city: str
    state: str
    website: str | None = None
    employee_count: int | None = None
    residential_roofing: bool = False
    active_service_area: bool = False
    has_google_business_profile: bool = False
    has_meta_ads: bool | None = None
    instant_lead_followup: bool | None = None
    owner_or_decision_maker: str | None = None
    observation: str | None = None


@dataclass(frozen=True)
class ProspectScore:
    score: int
    qualified: bool
    reasons: tuple[str, ...]


def score_roofing_prospect(p: RoofingProspect) -> ProspectScore:
    """Score 0-100. Qualification requires a real residential roofer in service area."""
    if not p.company.strip() or not p.city.strip() or not p.state.strip():
        return ProspectScore(0, False, ("missing_identity",))
    if not p.residential_roofing:
        return ProspectScore(0, False, ("not_residential_roofing",))
    if not p.active_service_area:
        return ProspectScore(0, False, ("outside_target_service_area",))

    score = 45
    reasons: list[str] = ["residential_roofer", "target_service_area"]

    if p.website:
        score += 10
        reasons.append("website_present")
    if p.has_google_business_profile:
        score += 10
        reasons.append("google_business_present")
    if p.employee_count is not None and 5 <= p.employee_count <= 50:
        score += 15
        reasons.append("ideal_company_size")
    if p.has_meta_ads is False:
        score += 10
        reasons.append("no_active_meta_ads")
    if p.instant_lead_followup is False:
        score += 10
        reasons.append("followup_gap")
    if p.owner_or_decision_maker:
        score += 5
        reasons.append("decision_maker_found")
    if p.observation:
        score += 5
        reasons.append("personalization_evidence")

    score = min(score, 100)
    return ProspectScore(score, score >= 65, tuple(reasons))


def build_outreach(p: RoofingProspect) -> dict[str, str]:
    """Create concise end-state-first outreach. Does not claim unverified results."""
    first = (p.owner_or_decision_maker or "").strip().split(" ")[0]
    greeting = f"{first} — quick one." if first else "Quick one."
    observation = (p.observation or f"I was looking at {p.company}'s marketing in {p.city}.").strip()
    subject = f"more booked roof inspections for {p.company}"
    body = (
        f"{greeting}\n\n"
        f"{observation}\n\n"
        "I build a system for roofers that turns local ads into qualified homeowners "
        "booked directly onto the inspection calendar — creative, lead capture, "
        "follow-up and booking included.\n\n"
        "I'm taking on a few founding roofing clients on a $500 launch + "
        "pay-per-qualified-booking model, so most of my upside depends on producing "
        "real appointments.\n\n"
        f"I made a quick idea for {p.company} based on the {p.city} market. Want me to send it?\n\n"
        "— BrightReach"
    )
    return {"subject": subject, "body": body}
