from __future__ import annotations

import re
from dataclasses import dataclass

from cloudos.config import Settings

from .models import SkipReason


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str = ""
    detail: str = ""


CAPTCHA_RE = re.compile(r"\bcaptcha\b|recaptcha challenge|hcaptcha challenge|verify you are human", re.I)
AUTH_RE = re.compile(r"sign in|required login|log in|two.?factor|2fa|multi.?factor|verification code", re.I)
ASSESS_RE = re.compile(r"assessment|personality test|cognitive test|video interview|recorded interview|one.?way interview", re.I)
SENSITIVE_RE = re.compile(r"social security|\bssn\b|passport|driver.?s? license number|government.?issued id|bank account|routing number|credit.?card|medical records?|tax returns?|background check authorization", re.I)


def inspect_page_text(text: str, settings: Settings) -> PolicyDecision:
    if SENSITIVE_RE.search(text):
        return PolicyDecision(False, SkipReason.SENSITIVE_INFORMATION_REQUESTED.value, "prohibited sensitive field detected")
    if settings.skip_assessments and ASSESS_RE.search(text):
        return PolicyDecision(False, SkipReason.ASSESSMENT_REQUIRED.value, "assessment or recorded interview detected")
    if CAPTCHA_RE.search(text):
        return PolicyDecision(False, SkipReason.CAPTCHA.value, "CAPTCHA blocks automation")
    if AUTH_RE.search(text):
        return PolicyDecision(False, SkipReason.AUTHENTICATION_REQUIRED.value, "authorized session is unavailable or expired")
    return PolicyDecision(True)


def complexity_decision(required_custom_questions: int, page_count: int, settings: Settings, total_fields: int = 0) -> PolicyDecision:
    if required_custom_questions > settings.max_custom_questions:
        return PolicyDecision(False, SkipReason.APPLICATION_TOO_LONG.value, f"{required_custom_questions} custom questions exceeds limit")
    if settings.skip_long_applications and page_count > 3:
        return PolicyDecision(False, SkipReason.APPLICATION_TOO_LONG.value, f"{page_count} application pages exceeds quick-apply limit")
    estimated_seconds = total_fields * 20 + required_custom_questions * 45 + page_count * 45
    if settings.skip_long_applications and estimated_seconds > settings.max_application_minutes * 60:
        return PolicyDecision(False, SkipReason.APPLICATION_TOO_LONG.value, f"estimated {estimated_seconds // 60} minute application exceeds limit")
    return PolicyDecision(True)
