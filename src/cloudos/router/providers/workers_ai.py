"""Cloudflare Workers AI client (L1) — REST via httpx, no SDK.

POST https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}
Bearer auth. Called ONLY from the VM-side router (contracts §13: never from
the Cloudflare ingress Worker).
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

from . import ProviderResponse, ProviderUnavailable

log = logging.getLogger("cloudos.router.providers.workers_ai")

API_BASE = "https://api.cloudflare.com/client/v4"
DEFAULT_TIMEOUT = 60.0

# Test hook: set to httpx.MockTransport in tests. None ⇒ real network.
_transport: Optional[httpx.BaseTransport] = None


def _client(transport: Optional[httpx.BaseTransport] = None) -> httpx.Client:
    return httpx.Client(
        transport=transport if transport is not None else _transport,
        timeout=DEFAULT_TIMEOUT,
    )


def generate(
    account_id: str,
    token: str,
    model: str,
    prompt: str,
    max_tokens: int,
    transport: Optional[httpx.BaseTransport] = None,
) -> ProviderResponse:
    """One chat completion. Raises ProviderUnavailable on any failure to serve."""
    url = f"{API_BASE}/accounts/{account_id}/ai/run/{model}"
    body = {
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
    }
    try:
        with _client(transport) as client:
            resp = client.post(url, json=body, headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as exc:
        # exception text may embed URLs/headers — expose the class name only
        raise ProviderUnavailable(f"workers_ai network error: {type(exc).__name__}") from exc

    if resp.status_code != 200:
        raise ProviderUnavailable(f"workers_ai HTTP {resp.status_code}")

    try:
        data = resp.json()
    except ValueError as exc:
        raise ProviderUnavailable("workers_ai: non-JSON response") from exc

    if data.get("success") is False:
        codes = [e.get("code") for e in data.get("errors", []) if isinstance(e, dict)]
        raise ProviderUnavailable(f"workers_ai API error codes={codes}")

    result = data.get("result") or {}
    text = result.get("response")
    if not isinstance(text, str):
        raise ProviderUnavailable("workers_ai: malformed response (missing result.response)")

    usage = result.get("usage") or {}
    return ProviderResponse(
        text=text,
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        completion_tokens=int(usage.get("completion_tokens") or 0),
    )
