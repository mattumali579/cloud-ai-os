from __future__ import annotations

from .generic import GenericAdapter
from .governmentjobs import GovernmentJobsAdapter
from .greenhouse import GreenhouseAdapter
from .icims import ICIMSAdapter
from .lever import LeverAdapter
from .smartrecruiters import SmartRecruitersAdapter
from .workday import WorkdayAdapter


ADAPTERS = {
    "greenhouse": GreenhouseAdapter,
    "lever": LeverAdapter,
    "workday": WorkdayAdapter,
    "icims": ICIMSAdapter,
    "smartrecruiters": SmartRecruitersAdapter,
    "generic": GenericAdapter,
    "governmentjobs": GovernmentJobsAdapter,
}


def adapter_for(ats: str, settings, profile, answers):
    return ADAPTERS.get((ats or "generic").lower(), GenericAdapter)(settings, profile, answers)
