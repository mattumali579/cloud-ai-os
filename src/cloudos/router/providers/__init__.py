"""Free-tier provider clients — plain httpx REST, no SDKs (contracts §9).

Each provider module exposes a module-level ``_transport`` hook: tests set it
to an ``httpx.MockTransport``; production leaves it ``None`` (real network).
Every client also accepts an explicit ``transport=`` argument that overrides
the module hook.

Error messages raised from providers must contain SAFE METADATA ONLY —
never tokens, API keys, URLs carrying keys, prompts, or response text.
"""
from dataclasses import dataclass


class ProviderUnavailable(Exception):
    """The provider could not serve (HTTP error / network failure / malformed
    response). The router treats the level as unavailable and escalates to the
    next free level; if no level can serve, the route fails closed."""


@dataclass(frozen=True)
class ProviderResponse:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


__all__ = ["ProviderUnavailable", "ProviderResponse"]
