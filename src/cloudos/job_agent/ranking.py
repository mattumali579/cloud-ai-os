from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Job
from .second_brain.profile_reader import ApplicantProfile


# The commute radius is personal data, so the specific towns are NOT committed to
# this public repository. They arrive at runtime in JOB_AGENT_LOCAL_TERMS from the
# local .env file. Terms are separated by "|" rather than "," because real terms
# contain commas ("walker, la"). The default keeps the agent working using only
# information that is already public.
_DEFAULT_LOCAL_TERMS = ("baton rouge", "lsu", "remote")


def commute_terms() -> tuple[str, ...]:
    import os

    raw = os.environ.get("JOB_AGENT_LOCAL_TERMS", "").strip()
    if not raw:
        return _DEFAULT_LOCAL_TERMS
    terms = tuple(t.strip().lower() for t in raw.split("|") if t.strip())
    return terms or _DEFAULT_LOCAL_TERMS


CATEGORIES: list[tuple[str, tuple[str, ...], str]] = [
    ("student-worker", ("student worker", "student assistant", "student employee", "student intern", "student office", "student -", "wae"), "general-student"),
    ("desk-office", ("office assistant", "administrative assistant", "front desk", "library assistant", "library page", "membership services", "recreation attendant", "leasing assistant", "apartment office"), "desk-office"),
    ("ai-automation", ("ai operations", "ai trainer", "ai evaluator", "data annotation", "model evaluation", "automation assistant", "prompt evaluator", "video tutor", "model tutor"), "ai-automation"),
    ("construction-management", ("construction management intern", "construction intern", "project assistant", "project coordinator assistant", "estimating assistant", "preconstruction assistant", "construction office assistant"), "construction-management"),
    ("owner-apprentice", ("assistant to ceo", "executive assistant", "assistant to owner", "founder associate"), "desk-office"),
]

SCAM = re.compile(r"\b(mlm|multi.?level marketing|pay to (join|start|work)|crypto investment|wire transfer|commission only|unlimited earning potential)\b", re.I)
SENIOR = re.compile(r"\b(senior|sr\.?|lead|director|principal|vice president|vp|chief|manager|professor|postdoctoral)\b", re.I)
QUICK_NEGATIVE = re.compile(r"\b(assessment|personality test|video interview|recorded interview|portfolio required|writing sample|essay)\b", re.I)
SPECIALIST_TITLE = re.compile(r"\b(engineer|specialist|scientist|architect|attorney|nurse|professor|developer)\b", re.I)
UNVERIFIED_REQUIREMENT = re.compile(
    r"\b(portfolio (?:displaying|required)|premiere pro|davinci resolve|after effects|nuke|vfx|motion graphics|"
    r"professional engineer license|registered architect|commercial driver.?s? license|\bcdl\b|"
    r"active nursing license|bilingual required|native[- ]level|fluency in)\b",
    re.I,
)


@dataclass(frozen=True)
class Score:
    total: int
    detail: dict[str, int]
    category: str
    resume_variant: str
    reject_reason: str = ""


def categorize(title: str, description: str = "") -> tuple[str, str]:
    text = title.lower()
    for category, terms, resume in CATEGORIES:
        if any(term in text for term in terms):
            return category, resume
    if any(term in text for term in ("gym", "fitness", "recreation")):
        return "gym-front-desk", "general-student"
    if any(term in text for term in ("construction", "preconstruction", "estimating")):
        return "construction-management", "construction-management"
    if any(term in text for term in ("artificial intelligence", "automation", "machine learning", "ai ")):
        return "ai-automation", "ai-automation"
    return "general-student", "general-student"


def score_job(job: Job, profile: ApplicantProfile) -> Score:
    text = f"{job.title} {job.description}"
    category, resume = categorize(job.title, job.description)
    if SCAM.search(text):
        return Score(0, {}, category, resume, "low_quality_job")
    if SENIOR.search(job.title) and "assistant" not in job.title.lower():
        return Score(10, {}, category, resume, "clearly_underqualified")
    if SPECIALIST_TITLE.search(job.title) and not re.search(r"\b(intern|student|assistant|trainee)\b", job.title, re.I):
        return Score(12, {}, category, resume, "clearly_underqualified")
    if re.search(r"^AI Tutor\s*-", job.title, re.I):
        return Score(12, {}, category, resume, "unknown_required_specialty")
    if UNVERIFIED_REQUIREMENT.search(job.description):
        return Score(12, {}, category, resume, "clearly_underqualified")
    years = [int(value) for value in re.findall(r"\b(\d{1,2})\+?\s+years?\b", job.description, re.I)]
    if years and max(years) >= 3 and not re.search(r"\b(intern|student|trainee)\b", job.title, re.I):
        return Score(15, {}, category, resume, "clearly_underqualified")
    if re.search(r"(?:minimum|required).{0,30}(?:3\.0|3\.00).{0,10}gpa|gpa.{0,20}(?:minimum|required).{0,10}(?:3\.0|3\.00)", job.description, re.I | re.S):
        return Score(15, {}, category, resume, "clearly_underqualified")
    loc = job.location.lower()
    local_terms = commute_terms()
    if loc and not any(term in loc for term in local_terms):
        return Score(20, {}, category, resume, "unreasonable_commute")

    preferred = any(category == item[0] for item in CATEGORIES) or category in {"gym-front-desk", "owner-apprentice"}
    likely = 21 if preferred else 8
    simplicity = 18 if not QUICK_NEGATIVE.search(text) else 4
    career = {"construction-management": 20, "ai-automation": 19, "owner-apprentice": 18, "desk-office": 15, "student-worker": 15}.get(category, 5)

    location = 15 if any(term in loc for term in local_terms) else 5
    compensation = 6 if not job.salary else 10
    experience = 10 if category in {"construction-management", "ai-automation", "owner-apprentice"} else 7
    detail = {
        "likelihood_of_acceptance": min(25, likely),
        "application_simplicity": min(20, simplicity),
        "career_relevance": min(20, career),
        "schedule_location_fit": min(15, location),
        "compensation": min(10, compensation),
        "experience_value": min(10, experience),
    }
    total = max(0, min(100, sum(detail.values())))
    return Score(total, detail, category, resume)
