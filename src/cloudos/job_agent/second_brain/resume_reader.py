from __future__ import annotations

from pathlib import Path

from .profile_reader import ApplicantProfile


class ResumeReader:
    def __init__(self, profile: ApplicantProfile):
        self.profile = profile

    def sources(self) -> list[Path]:
        result = []
        for item in self.profile.raw.get("resume_sources", []):
            if isinstance(item, dict) and item.get("status") == "VERIFIED" and item.get("value"):
                result.append(Path(item["value"]))
        return result
