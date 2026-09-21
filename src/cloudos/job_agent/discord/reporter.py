from __future__ import annotations

from typing import Any

from cloudos.notify import notify_owner

from ..models import Job


class JobNotifier:
    def __init__(self, conn: Any):
        self.conn = conn

    def submitted(self, job: Job) -> None:
        message = (
            "✅ APPLICATION SUBMITTED\n\n"
            f"Job: {job.title}\nCompany: {job.company}\nLocation: {job.location}\n"
            f"Category: {job.category}\nScore: {job.job_score}/100\n"
            f"Resume: {job.resume_variant}\nSource: {job.source}\n"
            f"Confirmation: {job.confirmation_id or job.confirmation_text or 'verified on employer page'}"
        )
        notify_owner(self.conn, "info", "JOB_APPLICATION_SUBMITTED", message, {"job_id": job.job_id})

    def skipped(self, job: Job, detail: str = "") -> None:
        safe_detail = _redact(detail)
        message = (
            "⏭️ SKIPPED\n\n"
            f"Job: {job.title}\nCompany: {job.company}\nReason: {job.skip_reason}\n"
            f"Detail: {safe_detail}\n\nNO sensitive information was submitted."
        )
        notify_owner(self.conn, "info", "JOB_APPLICATION_SKIPPED", message, {"job_id": job.job_id, "skip_reason": job.skip_reason})

    def daily_report(self, counts: dict[str, int], top: list[Job]) -> None:
        lines = [
            "JOB AGENT DAILY REPORT",
            "",
            f"Jobs discovered: {counts.get('discovered', 0)}",
            f"Qualified: {counts.get('qualified', 0)}",
            f"Applications submitted: {counts.get('submitted', 0)}",
            f"Sensitive-info skips: {counts.get('skip:sensitive_information_requested', 0)}",
            f"Assessment skips: {counts.get('skip:assessment_required', 0)}",
            f"Authentication skips: {counts.get('skip:authentication_required', 0)}",
            f"Duplicates: {counts.get('duplicate', 0)}",
            f"Low-quality rejected: {counts.get('rejected', 0)}",
            f"Failures: {counts.get('failed', 0)}",
            "",
            "Top applications:",
        ]
        lines.extend(f"{i}. {job.title} - {job.company} ({job.job_score}/100)" for i, job in enumerate(top[:3], 1))
        if not top:
            lines.append("None today")
        notify_owner(self.conn, "info", "JOB_AGENT_DAILY_REPORT", "\n".join(lines), {"counts": counts})


def _redact(value: str) -> str:
    import re
    value = re.sub(r"\b\d{3}-\d{2}-\d{4}\b", "[REDACTED_SSN]", value)
    value = re.sub(r"\b\d{9,17}\b", "[REDACTED_NUMBER]", value)
    value = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[REDACTED_EMAIL]", value)
    return value[:500]
