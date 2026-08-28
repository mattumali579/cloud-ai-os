"""AI Router (Agent 8) — free-tier-only routing per contracts/ARCHITECTURE.md §9.

Levels:
    L0  deterministic code (task registry, no AI)
    L1  Cloudflare Workers AI (free allocation, allowlisted models only)
    L2  Gemini free tier (privacy-gated, allowlisted models only)
    L3  paid — PERMANENTLY DISABLED ($0/month invariant)

Public surface: ``route(req: RouteRequest) -> RouteResult`` plus the L0 task
registry. Budget exhaustion FAILS CLOSED (CloudOSError(QUOTA_EXHAUSTED)) —
there is no paid fallback and never will be.
"""
from .levels import DETERMINISTIC_TASKS, register_deterministic_task, route
from .quota import QuotaStore, QuotaStoreError

__all__ = [
    "route",
    "DETERMINISTIC_TASKS",
    "register_deterministic_task",
    "QuotaStore",
    "QuotaStoreError",
]
