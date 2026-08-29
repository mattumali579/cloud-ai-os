"""Gemini CLI provider — Google-account OAuth ONLY, marked unavailable otherwise.

Owner rule: use Google subscription authentication only where officially
supported and stable; NEVER fall back to ``GEMINI_API_KEY``, Vertex, or any
metered path (all such env vars are scrubbed in base.py).

State of the world (2026-08-28): consumer Login-with-Google was reported
removed from Gemini CLI on 2026-06-18 (gemini-cli issue #28439 — the CLI now
demands GEMINI_API_KEY / GOOGLE_GENAI_USE_VERTEXAI / GOOGLE_GENAI_USE_GCA);
Google-account OAuth remains documented for Google AI Pro/Ultra subscribers.
This provider therefore runs only on cached OAuth creds
(``~/.gemini/oauth_creds.json``) with ``GOOGLE_GENAI_USE_GCA=true`` (the
Google-account selector), and reports UNAVAILABLE when the CLI demands a
metered auth method instead.
"""
from __future__ import annotations

from pathlib import Path

from cloudos.config import get_settings
from cloudos.contracts import ProviderState

from . import ProviderError, ProviderResponse, ProviderStatus, ProviderUnavailable
from . import base

NAME = "gemini"

_QUOTA_MARKERS = ("quota", "rate limit", "429", "resource_exhausted", "resource has been exhausted")
_AUTH_DEMAND_MARKERS = ("set an auth method", "gemini_api_key", "google_genai_use_vertexai")
_AUTH_MARKERS = ("login", "credentials", "oauth", "unauthorized", "401")


def _oauth_creds() -> Path:
    return Path(base.home()) / ".gemini" / "oauth_creds.json"


def probe() -> ProviderStatus:
    settings = get_settings()
    if not base.which(settings.gemini_cli_bin):
        return ProviderStatus(NAME, ProviderState.UNAVAILABLE, detail="gemini binary not found")
    if not _oauth_creds().is_file():
        return ProviderStatus(
            NAME,
            ProviderState.AUTH_REQUIRED,
            detail="no Google OAuth creds — sign in with a Google account (AI Pro/Ultra) if supported",
        )
    return ProviderStatus(
        NAME,
        ProviderState.AVAILABLE_SUBSCRIPTION,
        auth_mode="google_oauth",
        detail="cached Google-account OAuth (subscription-limits mode)",
    )


def _classify_failure(rc: int, text: str, timed_out: bool) -> Exception:
    low = text.lower()
    for marker in _QUOTA_MARKERS:
        if marker in low:
            return ProviderError(ProviderState.QUOTA_EXHAUSTED, f"gemini reported a quota limit (matched {marker!r})")
    for marker in _AUTH_DEMAND_MARKERS:
        if marker in low:
            # The CLI is demanding a METERED auth method — per owner rule this
            # provider is then simply unavailable, never api-key-backed.
            return ProviderUnavailable(
                "gemini CLI demands a metered auth method — OAuth mode unsupported in this build"
            )
    for marker in _AUTH_MARKERS:
        if marker in low:
            return ProviderError(ProviderState.AUTH_REQUIRED, f"gemini reported an auth problem (matched {marker!r})")
    if timed_out:
        return ProviderUnavailable("gemini CLI timed out")
    return ProviderUnavailable(f"gemini CLI exit={rc}")


def generate(prompt: str, max_tokens: int = 1024, timeout: int = 300) -> ProviderResponse:
    status = probe()
    if status.state is not ProviderState.AVAILABLE_SUBSCRIPTION:
        if status.state in (ProviderState.QUOTA_EXHAUSTED, ProviderState.AUTH_REQUIRED, ProviderState.BILLING_RISK):
            raise ProviderError(status.state, status.detail)
        raise ProviderUnavailable(status.detail)

    settings = get_settings()
    binary = base.which(settings.gemini_cli_bin) or settings.gemini_cli_bin
    res = base.RUNNER(
        [binary, "-p", prompt],
        timeout=timeout,
        extra_env={"GOOGLE_GENAI_USE_GCA": "true"},
    )
    combined = (res.stdout or "") + "\n" + (res.stderr or "")

    if res.returncode != 0 or res.timed_out:
        raise _classify_failure(res.returncode, combined, res.timed_out)

    text = (res.stdout or "").strip()
    if not text:
        raise ProviderUnavailable("gemini CLI returned empty output")
    return ProviderResponse(text=text, auth_mode=status.auth_mode)
