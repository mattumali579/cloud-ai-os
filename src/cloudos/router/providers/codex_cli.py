"""Codex CLI provider — ChatGPT subscription auth ONLY.

Auth facts (learn.chatgpt.com/docs/auth, 2026-08-28): ChatGPT sign-in uses
plan-included usage; API-key auth bills "standard API rates". Codex is known to
silently prefer ``OPENAI_API_KEY`` (scrubbed in base.py). Credentials live in
``~/.codex/auth.json`` or the OS keyring; ``codex login status`` reports the
active mode; headless login is ``codex login --device-auth``.

Mode detection is belt-and-braces: auth.json carrying an API key ⇒ BILLING_RISK
even if ``codex login status`` looks fine.
"""
from __future__ import annotations

import json
from pathlib import Path

from cloudos.config import get_settings
from cloudos.contracts import ProviderState

from . import ProviderError, ProviderResponse, ProviderStatus, ProviderUnavailable
from . import base

NAME = "codex"

_QUOTA_MARKERS = ("usage limit", "rate limit", "quota", "reached your limit", "too many requests")
_AUTH_MARKERS = ("not logged in", "login", "401", "unauthorized", "authentication")


def _auth_json() -> Path:
    return Path(base.home()) / ".codex" / "auth.json"


def probe() -> ProviderStatus:
    settings = get_settings()
    binary = base.which(settings.codex_cli_bin)
    if not binary:
        return ProviderStatus(NAME, ProviderState.UNAVAILABLE, detail="codex binary not found")

    # File check first: an auth.json in API-key mode is a billing risk no matter
    # what login status says.
    auth_file = _auth_json()
    if auth_file.is_file():
        try:
            data = json.loads(auth_file.read_text(encoding="utf-8"))
            if data.get("OPENAI_API_KEY"):
                return ProviderStatus(
                    NAME,
                    ProviderState.BILLING_RISK,
                    detail="auth.json holds API-key auth (metered) — run codex logout, then codex login with ChatGPT",
                )
        except Exception:  # noqa: BLE001
            return ProviderStatus(
                NAME, ProviderState.BILLING_RISK, detail="auth.json unreadable — billing state unverifiable"
            )

    res = base.RUNNER([binary, "login", "status"], timeout=30)
    low = ((res.stdout or "") + " " + (res.stderr or "")).lower()
    if "api key" in low:
        return ProviderStatus(
            NAME, ProviderState.BILLING_RISK, detail="codex login status reports API-key auth (metered)"
        )
    if res.returncode == 0 and ("chatgpt" in low or "logged in" in low):
        return ProviderStatus(
            NAME,
            ProviderState.AVAILABLE_SUBSCRIPTION,
            auth_mode="chatgpt_subscription",
            detail="ChatGPT account sign-in (plan-included usage)",
        )
    return ProviderStatus(
        NAME, ProviderState.AUTH_REQUIRED, detail="not logged in — run: codex login (or codex login --device-auth)"
    )


def _classify_failure(rc: int, text: str, timed_out: bool) -> Exception:
    low = text.lower()
    for marker in _QUOTA_MARKERS:
        if marker in low:
            return ProviderError(ProviderState.QUOTA_EXHAUSTED, f"codex reported a plan limit (matched {marker!r})")
    for marker in _AUTH_MARKERS:
        if marker in low:
            return ProviderError(ProviderState.AUTH_REQUIRED, f"codex reported an auth problem (matched {marker!r})")
    if timed_out:
        return ProviderUnavailable("codex CLI timed out")
    return ProviderUnavailable(f"codex CLI exit={rc}")


def generate(prompt: str, max_tokens: int = 1024, timeout: int = 300) -> ProviderResponse:
    status = probe()
    if status.state is not ProviderState.AVAILABLE_SUBSCRIPTION:
        if status.state in (ProviderState.QUOTA_EXHAUSTED, ProviderState.AUTH_REQUIRED, ProviderState.BILLING_RISK):
            raise ProviderError(status.state, status.detail)
        raise ProviderUnavailable(status.detail)

    settings = get_settings()
    binary = base.which(settings.codex_cli_bin) or settings.codex_cli_bin
    argv = [binary, "exec", "--skip-git-repo-check", prompt]
    res = base.RUNNER(argv, timeout=timeout)
    combined = (res.stdout or "") + "\n" + (res.stderr or "")

    if res.returncode != 0 or res.timed_out:
        raise _classify_failure(res.returncode, combined, res.timed_out)

    text = (res.stdout or "").strip()
    if not text:
        raise ProviderUnavailable("codex CLI returned empty output")
    return ProviderResponse(text=text, auth_mode=status.auth_mode)
