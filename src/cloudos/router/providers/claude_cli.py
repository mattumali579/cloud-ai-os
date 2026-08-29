"""Claude Code CLI provider — Claude Pro/Max subscription auth ONLY.

Auth facts (official docs, 2026-08-28): in ``-p`` headless mode an
``ANTHROPIC_API_KEY`` in the environment is ALWAYS used (metered Console
billing), so the subprocess env is scrubbed (base.py). Remaining silent
credential sources that could still bill an API account:

* ``apiKeyHelper`` in ``~/.claude/settings.json``  → BILLING_RISK
* an Anthropic profile ``active_config`` (WIF/``ant auth login``) → BILLING_RISK
* a Console (API-billed) login in ``.credentials.json`` → detected by the
  absence of the ``claudeAiOauth`` block → BILLING_RISK (cannot verify)

Sanctioned subscription modes:
* ``CLAUDE_CODE_OAUTH_TOKEN`` (from ``claude setup-token``) — headless/VM mode
* claude.ai ``/login`` OAuth credentials (``claudeAiOauth`` in .credentials.json)
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from cloudos.config import get_settings
from cloudos.contracts import ProviderState

from . import ProviderError, ProviderResponse, ProviderStatus, ProviderUnavailable
from . import base

NAME = "claude"

_QUOTA_MARKERS = ("session limit", "usage limit", "rate limit", "limit reached", "resets ")
_AUTH_MARKERS = (
    "/login",
    "not logged in",
    "login expired",
    "authentication_error",
    "invalid api key",
    "oauth token has expired",
    "credential",
)


def _config_dir() -> Path:
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(override) if override else Path(base.home()) / ".claude"


def _anthropic_profile_dir() -> Path:
    if os.name == "nt":
        appdata = os.environ.get("APPDATA", "")
        return Path(appdata) / "Anthropic" if appdata else Path(base.home()) / "AppData" / "Roaming" / "Anthropic"
    return Path(base.home()) / ".config" / "anthropic"


def probe() -> ProviderStatus:
    """Passive auth/billing check — reads local files only, never calls the model."""
    settings = get_settings()
    if not base.which(settings.claude_cli_bin):
        return ProviderStatus(NAME, ProviderState.UNAVAILABLE, detail="claude binary not found")

    # Billing-risk sources that survive env scrubbing (settings file / profile).
    settings_file = _config_dir() / "settings.json"
    try:
        if settings_file.is_file():
            data = json.loads(settings_file.read_text(encoding="utf-8"))
            if data.get("apiKeyHelper"):
                return ProviderStatus(
                    NAME,
                    ProviderState.BILLING_RISK,
                    detail="apiKeyHelper configured in settings.json — could supply a metered credential",
                )
    except Exception:  # noqa: BLE001 — unreadable settings: cannot verify → risk
        return ProviderStatus(
            NAME, ProviderState.BILLING_RISK, detail="settings.json unreadable — billing state unverifiable"
        )

    profile_dir = _anthropic_profile_dir()
    if (profile_dir / "active_config").is_file() or (profile_dir / "configs" / "default").exists():
        return ProviderStatus(
            NAME,
            ProviderState.BILLING_RISK,
            detail="Anthropic profile active_config present — could route to WIF/org billing",
        )

    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return ProviderStatus(
            NAME,
            ProviderState.AVAILABLE_SUBSCRIPTION,
            auth_mode="subscription_oauth_token",
            detail="CLAUDE_CODE_OAUTH_TOKEN set (claude setup-token)",
        )

    creds = _config_dir() / ".credentials.json"
    if not creds.is_file():
        return ProviderStatus(NAME, ProviderState.AUTH_REQUIRED, detail="no credentials — run: claude /login")
    try:
        cred_data = json.loads(creds.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return ProviderStatus(NAME, ProviderState.AUTH_REQUIRED, detail="credentials unreadable — run: claude /login")
    if "claudeAiOauth" not in cred_data:
        # Console logins bill the API account; without the claude.ai OAuth block
        # the billing mode cannot be verified → fail closed.
        return ProviderStatus(
            NAME,
            ProviderState.BILLING_RISK,
            detail="credentials present but not claude.ai OAuth — billing mode unverifiable",
        )
    return ProviderStatus(
        NAME,
        ProviderState.AVAILABLE_SUBSCRIPTION,
        auth_mode="subscription_oauth",
        detail="claude.ai subscription login",
    )


def _classify_failure(rc: int, text: str, timed_out: bool) -> Exception:
    low = text.lower()
    for marker in _QUOTA_MARKERS:
        if marker in low:
            return ProviderError(ProviderState.QUOTA_EXHAUSTED, f"claude reported a plan limit (matched {marker!r})")
    for marker in _AUTH_MARKERS:
        if marker in low:
            return ProviderError(ProviderState.AUTH_REQUIRED, f"claude reported an auth problem (matched {marker!r})")
    if timed_out:
        return ProviderUnavailable("claude CLI timed out")
    return ProviderUnavailable(f"claude CLI exit={rc}")


def generate(prompt: str, max_tokens: int = 1024, timeout: int = 300) -> ProviderResponse:
    """Run one headless generation on the subscription. Raises ProviderError /
    ProviderUnavailable; never returns raw CLI errors that could carry secrets."""
    status = probe()
    if status.state is not ProviderState.AVAILABLE_SUBSCRIPTION:
        if status.state in (ProviderState.QUOTA_EXHAUSTED, ProviderState.AUTH_REQUIRED, ProviderState.BILLING_RISK):
            raise ProviderError(status.state, status.detail)
        raise ProviderUnavailable(status.detail)

    settings = get_settings()
    argv = [base.which(settings.claude_cli_bin) or settings.claude_cli_bin, "-p", "--output-format", "json"]
    if settings.claude_cli_model:
        argv += ["--model", settings.claude_cli_model]

    res = base.RUNNER(argv, stdin_text=prompt, timeout=timeout)
    combined = (res.stdout or "") + "\n" + (res.stderr or "")

    if res.returncode != 0 or res.timed_out:
        raise _classify_failure(res.returncode, combined, res.timed_out)

    try:
        payload = json.loads(res.stdout.strip().splitlines()[-1]) if res.stdout.strip() else {}
    except Exception:  # noqa: BLE001
        payload = {}

    if payload.get("is_error"):
        raise _classify_failure(res.returncode, combined, False)

    text = payload.get("result")
    if not isinstance(text, str):
        # Fall back to raw stdout for older/plain output — still a success path.
        text = res.stdout.strip()
        if not text:
            raise ProviderUnavailable("claude CLI returned empty output")

    usage = payload.get("usage") or {}
    return ProviderResponse(
        text=text,
        prompt_tokens=int(usage.get("input_tokens") or 0),
        completion_tokens=int(usage.get("output_tokens") or 0),
        auth_mode=status.auth_mode,
    )
