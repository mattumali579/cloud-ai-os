"""Decide which emails are worth a phone notification.

The rules here are a direct port and generalisation of the school-message
filter that already shipped in ``supabase/functions/school-system/school.ts``
(ACTION/URGENT/NOISE regexes, actionable-verb and urgency tiers). That file
only understood coursework; a real mailbox also carries money, security, job
and automation-failure mail, so the scorer below keeps the school rules and
adds the other categories.

Design rules, calibrated against the real inbox (~14 messages/day, of which
1-3 actually matter):

* Fail quiet, not loud. An unscored message is NOT sent. Spam costs more than
  a missed newsletter, because a channel that cries wolf gets muted.
* Bulk mail is rejected on structure (List-Unsubscribe / bulk precedence),
  not on vocabulary, because marketing copy imitates urgency on purpose.
* Mail the owner sent to themselves is always dropped: the digests this very
  system produces would otherwise notify about themselves forever.

Pure stdlib, no dependencies, no network: this module is trivially testable
and is the only place the "is it important?" question is answered.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Optional

# --- Category signals ------------------------------------------------------

MONEY_RE = re.compile(
    r"\b(?:paid you|sent you \$|payment (?:received|failed|declined|due)|"
    r"you(?:'ve| have) been paid|refund(?:ed)?|deposit(?:ed)?|"
    r"past due|overdue (?:balance|invoice|payment)|invoice due|"
    r"card (?:declined|expired)|insufficient funds|direct deposit)\b",
    re.I,
)
MONEY_SOFT_RE = re.compile(
    r"\b(?:receipt|invoice|statement|billing|charged|subscription renew)\b", re.I
)

SCHOOL_RE = re.compile(
    r"\b(?:assignment|quiz|exam|midterm|final(?:s)?|homework|lab report|"
    r"discussion post|essay|paper|project|syllabus|grade(?:s|d)?|"
    r"registration|enroll(?:ment)?|advisor|advising|bursar|tuition|"
    r"financial aid|fafsa|scholarship|transcript|hold on your account|"
    r"course|class|semester|professor|instructor|moodle|canvas|blackboard)\b",
    re.I,
)

# Kept verbatim from school.ts so coursework behaviour does not regress.
ACTION_RE = re.compile(
    r"\b(?:assignment|quiz|exam|test|project|homework|lab|discussion|paper|"
    r"essay|presentation|worksheet|submit|submission|complete|deadline|due)\b",
    re.I,
)
URGENT_RE = re.compile(
    r"\b(?:urgent|asap|immediately|overdue|final reminder|"
    r"due (?:today|tonight)|last chance|action required|"
    r"expires? (?:today|tomorrow|soon)|respond by|reply by)\b",
    re.I,
)
NOISE_RE = re.compile(
    r"\b(?:unsubscribe|newsletter|campus event|career fair|promotion|"
    r"survey invitation|webinar|read online|view (?:online|in browser)|"
    r"deals?|percent off|sale|shop now|best books|new arrivals|"
    r"you(?:'ve| have) been invited to|welcome to)\b",
    re.I,
)

JOB_RE = re.compile(
    r"\b(?:interview|offer letter|job offer|next steps|hiring (?:manager|team)|"
    r"your application|application status|"
    r"we(?:'d| would) like to (?:speak|talk|meet)|"
    r"schedule a (?:call|chat|screen)|background check|onboarding|start date)\b",
    re.I,
)

SECURITY_RE = re.compile(
    r"\b(?:new sign-?in|unusual (?:activity|sign-?in)|"
    r"suspicious (?:activity|login)|"
    r"password (?:was |has been )?(?:changed|reset)|"
    r"account (?:locked|suspended|compromised)|"
    r"security alert|unrecognized device|someone (?:signed|logged) in)\b",
    re.I,
)

# One-time passcodes arrive because the owner is actively logging in. They are
# read in the app they were requested for; mirroring them to Discord is pure
# noise, and it copies a live code into a chat channel.
OTP_RE = re.compile(
    r"\b(?:your (?:\w+ )?(?:login|verification|security|one-?time) code|"
    r"verification code|one-?time (?:code|passcode|password)|"
    r"2fa code|is your .{0,20}code)\b",
    re.I,
)
OTP_SUBJECT_RE = re.compile(r"^\s*\d{4,8}\s*[-–—]\s*")

# The owner's own automation breaking is operationally important: a silent
# scheduler is exactly the failure this whole system exists to prevent.
AUTOMATION_FAIL_RE = re.compile(
    r"\b(?:run failed|workflow failed|build failed|job failed|"
    r"all jobs have failed|deploy(?:ment)? failed|pipeline failed)\b",
    re.I,
)
QUOTA_RE = re.compile(
    r"\b(?:limit reached|near your .{0,25}limit|quota (?:exceeded|reached)|"
    r"usage limit|rate limit|over your limit|approaching .{0,25}limit)\b",
    re.I,
)

DEADLINE_RE = re.compile(
    r"\b(?:due|deadline|expires?|last day|by (?:tomorrow|today)|"
    r"within \d+ (?:hours?|days?))\b",
    re.I,
)

BULK_SENDER_RE = re.compile(
    r"(?:^|[@.])(?:e|em|email|mail|news|marketing|info|hello|team|no-?reply|"
    r"noreply|notifications?|updates?|alerts?)[.@]",
    re.I,
)

CATEGORY_WEIGHT = {
    "money": 55,
    "security": 55,
    "school": 45,
    "job": 50,
    "automation": 50,
    "deadline": 25,
}

# Categories that describe an event that already happened to the owner's money,
# account, job or infrastructure. Real senders of these (banks, Venmo, GitHub,
# an LMS) all mail in bulk, so the bulk-header penalty must not be able to veto
# them -- otherwise a fraud alert loses to a newsletter heuristic.
STRONG_CATEGORIES = frozenset({"money", "security", "automation", "job"})

NOTIFY_THRESHOLD = 50


@dataclass
class Classification:
    """Why a message was or was not judged important."""

    important: bool
    score: int
    categories: list = field(default_factory=list)
    reasons: list = field(default_factory=list)
    urgency: str = "normal"  # high | medium | normal
    skip_reason: Optional[str] = None

    @property
    def label(self) -> str:
        return self.categories[0] if self.categories else "general"


def _text(subject: str, preview: str) -> str:
    return f"{subject}. {preview}"


def _addr(value: str) -> str:
    match = re.search(r"[\w.+-]+@[\w.-]+", value or "")
    return match.group(0).lower() if match else (value or "").strip().lower()


def _is_bulk(headers: Mapping) -> bool:
    lowered = {str(k).lower(): (v or "") for k, v in headers.items()}
    if lowered.get("list-unsubscribe") or lowered.get("list-id"):
        return True
    if lowered.get("precedence", "").lower() in {"bulk", "list", "junk"}:
        return True
    if lowered.get("x-campaign-id") or lowered.get("x-mailer-campaign"):
        return True
    return False


def classify(
    subject: str,
    preview: str = "",
    sender: str = "",
    headers: Optional[Mapping] = None,
    owner_addresses: Iterable = (),
) -> Classification:
    """Score one message. Never raises; unknown mail scores 0 and is dropped."""
    subject = (subject or "").strip()
    preview = (preview or "").strip()
    headers = headers or {}
    text = _text(subject, preview)
    sender_addr = _addr(sender)
    owners = {str(a).lower() for a in owner_addresses if a}

    # --- hard skips --------------------------------------------------------
    if sender_addr and sender_addr in owners:
        return Classification(False, 0, skip_reason="from_self")

    if OTP_RE.search(text) or OTP_SUBJECT_RE.search(subject):
        # A security *alert* still wins; a bare passcode does not.
        if not SECURITY_RE.search(text):
            return Classification(False, 0, skip_reason="one_time_code")

    bulk = _is_bulk(headers)

    # --- scoring -----------------------------------------------------------
    score = 0
    categories = []
    reasons = []

    def hit(category: str, reason: str, bonus: int = 0) -> None:
        nonlocal score
        if category not in categories:
            categories.append(category)
            score += CATEGORY_WEIGHT[category]
        score += bonus
        reasons.append(reason)

    if MONEY_RE.search(text):
        hit("money", "money movement or failed payment")
    elif MONEY_SOFT_RE.search(text):
        score += 15
        reasons.append("billing document")

    if SECURITY_RE.search(text):
        hit("security", "account security event")

    edu_sender = sender_addr.endswith(".edu")
    if SCHOOL_RE.search(text):
        hit(
            "school",
            "coursework or student-account topic",
            bonus=15 if edu_sender else 0,
        )
        # school.ts parity: an actionable verb is what made a school mail real.
        if ACTION_RE.search(text):
            score += 10
            reasons.append("actionable coursework verb")
    elif edu_sender:
        score += 20
        reasons.append("sent from a school address")

    if JOB_RE.search(text):
        hit("job", "job application progressed")

    if AUTOMATION_FAIL_RE.search(text):
        hit("automation", "an automation reported a failure")
    if QUOTA_RE.search(text):
        hit("automation", "a service limit was hit")

    if DEADLINE_RE.search(text):
        hit("deadline", "carries a deadline")

    urgency = "normal"
    if URGENT_RE.search(text):
        urgency = "high"
        score += 20
        reasons.append("explicitly urgent wording")
    elif categories:
        urgency = "medium"

    # --- noise damping -----------------------------------------------------
    if NOISE_RE.search(text):
        score -= 35
        reasons.append("marketing/newsletter wording")

    if bulk and not (STRONG_CATEGORIES & set(categories)):
        # Bulk mail with nothing but soft signals is almost always marketing.
        # Bulk mail reporting a payment, a sign-in, or a broken job is not.
        score -= 30
        reasons.append("bulk-mail headers")

    if BULK_SENDER_RE.search(sender_addr) and not categories:
        score -= 10
        reasons.append("no-reply/marketing sender")

    score = max(score, 0)
    important = score >= NOTIFY_THRESHOLD

    return Classification(
        important=important,
        score=score,
        categories=categories,
        reasons=reasons,
        urgency=urgency if important else "normal",
        skip_reason=None if important else "below_threshold",
    )


def is_important(*args, **kwargs) -> bool:
    """Convenience wrapper around :func:`classify`."""
    return classify(*args, **kwargs).important
