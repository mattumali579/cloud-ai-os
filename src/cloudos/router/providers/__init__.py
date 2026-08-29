"""Subscription CLI provider layer (SUBSCRIPTION_PROVIDERS.md).

Providers invoke locally installed, subscription-authenticated CLIs
(Claude Code, Codex CLI, Gemini CLI). There is NO HTTP client in this package:
no code path exists that could call a metered AI API.

Every provider module exposes:

    probe()                 -> ProviderStatus   (passive: no AI call, no quota use)
    generate(req, timeout)  -> ProviderResponse (raises ProviderError with a
                                                 ProviderState classification)

Subprocesses run with metered-credential env vars REMOVED (base.scrubbed_env);
error messages and statuses carry SAFE METADATA ONLY — never tokens, keys,
prompts, or raw CLI output that could embed them.
"""
from dataclasses import dataclass

from cloudos.contracts import ProviderState


class ProviderUnavailable(Exception):
    """The provider could not serve (binary/subprocess failure). The router
    skips it and tries the next subscription provider."""


class ProviderError(Exception):
    """A classified provider failure. ``state`` drives routing:
    QUOTA_EXHAUSTED → defer; AUTH_REQUIRED → defer + notify;
    BILLING_RISK → fail closed; UNAVAILABLE → skip provider."""

    def __init__(self, state: ProviderState, detail: str):
        super().__init__(f"{state.value}: {detail}")
        self.state = state
        self.detail = detail


@dataclass(frozen=True)
class ProviderResponse:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    auth_mode: str = ""


@dataclass(frozen=True)
class ProviderStatus:
    provider: str
    state: ProviderState
    auth_mode: str = ""  # e.g. "subscription_oauth", "subscription_oauth_token"
    detail: str = ""     # safe metadata only


__all__ = [
    "ProviderUnavailable",
    "ProviderError",
    "ProviderResponse",
    "ProviderStatus",
    "ProviderState",
]
