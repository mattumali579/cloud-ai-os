"""Shared contract types for Cloud AI OS. Owned by the lead (Agent 1).

Every subsystem imports these — never redefine them locally.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"


class PrivacyLabel(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    PERSONAL = "personal"
    SENSITIVE = "sensitive"
    SECRET = "secret"


class ErrorCode(str, Enum):
    LIMIT_REACHED = "LIMIT_REACHED"
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
    PAID_DISABLED = "PAID_DISABLED"
    MODEL_NOT_ALLOWED = "MODEL_NOT_ALLOWED"
    PRIVACY_BLOCKED = "PRIVACY_BLOCKED"
    SECRET_DETECTED = "SECRET_DETECTED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    BILLING_RISK = "BILLING_RISK"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ProviderState(str, Enum):
    """Reported state of a subscription AI provider (SUBSCRIPTION_PROVIDERS.md)."""

    AVAILABLE_SUBSCRIPTION = "AVAILABLE_SUBSCRIPTION"
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    UNAVAILABLE = "UNAVAILABLE"
    BILLING_RISK = "BILLING_RISK"


# Error codes that mean "do not retry — fail closed, notify the owner".
# BILLING_RISK is here on purpose: it means money COULD move; only the owner
# may clear it. QUOTA_EXHAUSTED is deliberately NOT here anymore — a plan
# allowance refills, so the task is preserved and deferred instead of blocked.
FAIL_CLOSED_CODES = frozenset(
    {
        ErrorCode.LIMIT_REACHED,
        ErrorCode.PAID_DISABLED,
        ErrorCode.PRIVACY_BLOCKED,
        ErrorCode.SECRET_DETECTED,
        ErrorCode.BILLING_RISK,
    }
)

# Error codes that mean "preserve the task and retry after the quota/auth state
# changes" — requeued with a future run_at, attempts NOT incremented, never a
# paid fallback.
DEFER_CODES = frozenset({ErrorCode.QUOTA_EXHAUSTED, ErrorCode.AUTH_REQUIRED})


class CloudOSError(Exception):
    """Structured error. `code` drives fail-closed vs retry behavior.

    Never put secret material in `message` or `details` — safe metadata only.
    """

    def __init__(self, code: ErrorCode, message: str, details: Optional[dict] = None):
        super().__init__(f"{code.value}: {message}")
        self.code = code
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict:
        return {"error": {"code": self.code.value, "message": self.message, "details": self.details}}


@dataclass
class RouteRequest:
    prompt: str
    task: str = "general"
    privacy_label: PrivacyLabel = PrivacyLabel.INTERNAL
    max_tokens: int = 1024
    model_hint: Optional[str] = None
    meta: dict = field(default_factory=dict)


@dataclass
class RouteResult:
    ok: bool
    text: Optional[str] = None
    model: Optional[str] = None
    level: Optional[int] = None
    error_code: Optional[ErrorCode] = None
    error: Optional[str] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0  # invariant: always 0.0 — paid AI is disabled

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "text": self.text,
            "model": self.model,
            "level": self.level,
            "error_code": self.error_code.value if self.error_code else None,
            "error": self.error,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cost_usd": self.cost_usd,
        }


@dataclass
class SecretHit:
    pattern_name: str
    span: tuple[int, int]
    redacted_sample: str  # first 4 chars + "…" at most — never the full match


@dataclass
class GateDecision:
    allowed: bool
    reason: str
    hits: list[SecretHit] = field(default_factory=list)


@dataclass
class ContextBundle:
    files: list[str] = field(default_factory=list)
    text: str = ""
    truncated: bool = False
    excluded: list[str] = field(default_factory=list)  # paths blocked by privacy gate


def sha256_hash(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def error_response(code: ErrorCode, message: str, details: Optional[dict] = None) -> dict:
    return {"error": {"code": code.value, "message": message, "details": details or {}}}
