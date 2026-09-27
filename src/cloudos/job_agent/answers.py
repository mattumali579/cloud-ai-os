from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .models import QuestionKind


SENSITIVE = re.compile(
    r"\b(ssn|social security|passport|driver.?s? license (number|#)|government.?issued id|"
    r"identification number|id number|bank account|routing number|credit.?card|tax return|tax identification|"
    r"w-?4|medical record|background check|criminal history authorization)\b",
    re.I,
)
ASSESSMENT = re.compile(
    r"\b(personality test|cognitive test|skills assessment|assessment|recorded interview|video interview|"
    r"one.?way interview|typing test|aptitude test|lengthy written assessment|writing sample|essay|cover letter required)\b",
    re.I,
)
GENERATIVE = re.compile(r"\b(why (do you|are you)|describe|tell us|explain|cover letter|essay)\b", re.I)
DISQUALIFYING = re.compile(r"\b(required).*(license|certification|degree|years? of experience)\b", re.I)


@dataclass(frozen=True)
class Answer:
    key: str
    status: str
    value: Any
    source: str


class AnswerBank:
    def __init__(self, path: str | Path):
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        self.answers: dict[str, Answer] = {}
        for category, values in raw.get("answers", {}).items():
            if not isinstance(values, dict):
                continue
            if "status" in values:
                self.answers[category] = Answer(category, str(values.get("status", "UNKNOWN")), values.get("value"), str(values.get("source", "")))
                continue
            for key, item in values.items():
                if isinstance(item, dict):
                    full = f"{category}.{key}"
                    self.answers[full] = Answer(full, str(item.get("status", "UNKNOWN")), item.get("value"), str(item.get("source", "")))

    def resolve(self, question: str) -> Answer | None:
        q = _norm(question)
        if any(term in q for term in ("former employer", "previous employer", "reference name", "supervisor name", "emergency contact")):
            return None
        aliases = {
            "name": ("full name", "legal name", "applicant name", "candidate name"),
            "first_name": ("first name", "given name"),
            "middle_name": ("middle name",),
            "last_name": ("last name", "surname", "family name"),
            "contact.email": ("email", "email address"),
            "contact.phone": ("phone", "mobile", "telephone"),
            "contact.city": ("city",),
            "contact.state": ("state", "province"),
            "contact.country": ("country", "country of residence"),
            "contact.linkedin": ("linkedin",),
            "contact.street_address": ("street address", "address line 1", "home address"),
            "contact.postal_code": ("postal code", "zip code", "zip"),
            "education.school": ("current school", "school name", "college or university", "university name"),
            "education.degree": ("degree", "major", "field of study"),
            "education.graduation": ("graduation", "graduate date"),
            "work_authorization.authorized": ("authorized to work", "legally authorized"),
            "work_authorization.sponsorship": ("sponsorship", "visa"),
            "transportation.reliable": ("reliable transportation",),
            "availability.weekends": ("weekend",),
            "availability.start_date": ("start date", "available to start"),
            "salary.expectation": ("salary", "compensation", "pay expectation"),
            "demographics.gender": ("gender", "sex"),
            "demographics.race": ("race", "ethnicity"),
            "demographics.veteran": ("veteran status", "protected veteran"),
            "demographics.disability": ("disability status", "self identify disability"),
        }
        for key, terms in aliases.items():
            if any(_matches(q, _norm(term)) for term in terms):
                return self.answers.get(key)
        if q == "name":
            return self.answers.get("name")
        return None


class QuestionClassifier:
    def __init__(self, bank: AnswerBank):
        self.bank = bank

    def classify(self, question: str, required: bool = True) -> tuple[QuestionKind, Answer | None]:
        if SENSITIVE.search(question):
            return QuestionKind.SENSITIVE, None
        if ASSESSMENT.search(question):
            return QuestionKind.ASSESSMENT, None
        answer = self.bank.resolve(question)
        if answer and answer.status == "VERIFIED":
            return QuestionKind.SAFE_STANDARD, answer
        if GENERATIVE.search(question):
            return QuestionKind.SAFE_GENERATIVE, None
        if DISQUALIFYING.search(question):
            return QuestionKind.DISQUALIFYING, answer
        if required:
            return QuestionKind.UNKNOWN, answer
        return QuestionKind.SAFE_STANDARD, answer


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value.lower())).strip()


def _matches(question: str, alias: str) -> bool:
    return question == alias or question.startswith(alias + " ") or question.endswith(" " + alias) or f" {alias} " in f" {question} "
