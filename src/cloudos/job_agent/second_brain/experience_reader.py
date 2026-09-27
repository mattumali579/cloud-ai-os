from __future__ import annotations

from .profile_reader import ApplicantProfile


class ExperienceReader:
    def __init__(self, profile: ApplicantProfile):
        self.profile = profile

    def verified(self) -> list[dict]:
        items = self.profile.raw.get("experience", [])
        return [i for i in items if isinstance(i, dict) and i.get("status") == "VERIFIED"]
