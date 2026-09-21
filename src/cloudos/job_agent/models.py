from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class JobStatus(StrEnum):
    DISCOVERED = "discovered"
    REJECTED = "rejected"
    QUALIFIED = "qualified"
    APPLYING = "applying"
    SUBMITTED = "submitted"
    SKIPPED = "skipped"
    FAILED = "failed"
    DUPLICATE = "duplicate"


class SkipReason(StrEnum):
    SENSITIVE_INFORMATION_REQUESTED = "sensitive_information_requested"
    ASSESSMENT_REQUIRED = "assessment_required"
    APPLICATION_TOO_LONG = "application_too_long"
    CAPTCHA = "captcha"
    AUTHENTICATION_REQUIRED = "authentication_required"
    UNKNOWN_ANSWER = "unknown_answer"
    LOW_QUALITY_JOB = "low_quality_job"
    EXPIRED_JOB = "expired_job"
    DUPLICATE = "duplicate"
    UNSUPPORTED_ATS = "unsupported_ats"
    VALIDATION_FAILED = "validation_failed"
    RESUME_UNAVAILABLE = "resume_unavailable"
    TIMEOUT = "timeout"
    UNREASONABLE_COMMUTE = "unreasonable_commute"


class QuestionKind(StrEnum):
    SAFE_STANDARD = "SAFE_STANDARD"
    SAFE_GENERATIVE = "SAFE_GENERATIVE"
    UNKNOWN = "UNKNOWN"
    SENSITIVE = "SENSITIVE"
    ASSESSMENT = "ASSESSMENT"
    DISQUALIFYING = "DISQUALIFYING"


@dataclass
class Job:
    company: str
    title: str
    location: str
    job_url: str
    application_url: str = ""
    ats: str = "generic"
    ats_job_id: str = ""
    requisition_id: str = ""
    description: str = ""
    category: str = "general-student"
    salary: str = ""
    source: str = ""
    discovered_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    job_score: int = 0
    score_detail: dict[str, int] = field(default_factory=dict)
    application_complexity: int = 0
    resume_variant: str = ""
    tailored_resume_path: str = ""
    status: JobStatus = JobStatus.DISCOVERED
    skip_reason: str = ""
    applied_at: str = ""
    confirmation_id: str = ""
    confirmation_text: str = ""
    last_attempt: str = ""
    attempt_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def description_hash(self) -> str:
        return hashlib.sha256(self.description.encode("utf-8", errors="ignore")).hexdigest()

    @property
    def job_id(self) -> str:
        stable = self.ats_job_id or self.requisition_id or self.application_url or self.job_url
        normalized = "|".join(_norm(v) for v in (self.company, self.title, stable))
        return hashlib.sha256(normalized.encode()).hexdigest()[:32]

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["status"] = self.status.value
        result["job_id"] = self.job_id
        result["description_hash"] = self.description_hash
        return result

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "Job":
        fields = cls.__dataclass_fields__
        data = {k: v for k, v in value.items() if k in fields}
        if "status" in data and not isinstance(data["status"], JobStatus):
            data["status"] = JobStatus(data["status"])
        return cls(**data)


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()
