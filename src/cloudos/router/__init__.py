"""AI Router — subscription-CLI routing per docs/SUBSCRIPTION_PROVIDERS.md.

Order:
    L0   deterministic code (task registry, no AI)
    1..n subscription CLI providers in configured priority
         (claude → codex → gemini by default)
    —    NO PAID FALLBACK: exhausted ⇒ QUOTA_EXHAUSTED (job deferred),
         unauthenticated ⇒ AUTH_REQUIRED (deferred + owner notified),
         billing-risk ⇒ BILLING_RISK (fail closed, owner-only clear)

Public surface: ``route(req) -> RouteResult``, ``probe_all()`` for provider
status, plus the L0 task registry. No HTTP client exists in this package —
no code path can call a metered AI API.
"""
from .levels import (
    DETERMINISTIC_TASKS,
    PROVIDERS,
    probe_all,
    register_deterministic_task,
    route,
)
from .quota import QuotaStore, QuotaStoreError

__all__ = [
    "route",
    "probe_all",
    "PROVIDERS",
    "DETERMINISTIC_TASKS",
    "register_deterministic_task",
    "QuotaStore",
    "QuotaStoreError",
]
