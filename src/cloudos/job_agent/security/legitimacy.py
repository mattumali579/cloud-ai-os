from __future__ import annotations

import re
from urllib.parse import urlparse

from ..models import Job


SUSPICIOUS_HOSTS = {"bit.ly", "tinyurl.com", "t.me", "telegram.me", "wa.me", "forms.gle", "docs.google.com"}
SUSPICIOUS_TEXT = re.compile(r"\b(pay to apply|application fee|buy equipment|send money|telegram|whatsapp recruiter|crypto payment|gift card)\b", re.I)


def verify_legitimacy(job: Job) -> tuple[bool, str]:
    url = job.application_url or job.job_url
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host:
        return False, "insecure_or_invalid_application_url"
    if host in SUSPICIOUS_HOSTS or any(host.endswith("." + item) for item in SUSPICIOUS_HOSTS):
        return False, "suspicious_application_host"
    if SUSPICIOUS_TEXT.search(f"{job.title} {job.description}"):
        return False, "low_quality_job"
    return True, ""
