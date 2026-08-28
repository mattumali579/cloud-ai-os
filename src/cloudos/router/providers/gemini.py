"""Gemini free-tier client (L2) — REST via httpx, no SDK.

POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key=...

The API key travels as a query parameter, so NOTHING here may ever put the
request URL (or httpx exception text, which can embed it) into an error
message or log line — class names and status codes only.

The router privacy-gates the FULL outbound payload BEFORE calling
:func:`generate` (contracts §9 L2); this module never sends un-gated content.
Build the payload with :func:`build_payload` so the gated text and the sent
bytes are the same object.
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

from . import ProviderResponse, ProviderUnavailable

log = logging.getLogger("cloudos.router.providers.gemini")

API_BASE = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_TIMEOUT = 60.0

# Test hook: set to httpx.MockTransport in tests. None ⇒ real network.
_transport: Optional[httpx.BaseTransport] = None


def _client(transport: Optional[httpx.BaseTransport] = None) -> httpx.Client:
    return httpx.Client(
        transport=transport if transport is not None else _transport,
        timeout=DEFAULT_TIMEOUT,
    )


def build_payload(prompt: str, max_tokens: int) -> dict:
    """The exact generateContent request body. The router serializes this for
    the privacy gate, then passes the same dict to generate()."""
    return {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": max_tokens},
    }


def generate(
    api_key: str,
    model: str,
    payload: dict,
    transport: Optional[httpx.BaseTransport] = None,
) -> ProviderResponse:
    """One generateContent call. Raises ProviderUnavailable on any failure to serve."""
    url = f"{API_BASE}/models/{model}:generateContent"
    try:
        with _client(transport) as client:
            resp = client.post(url, params={"key": api_key}, json=payload)
    except httpx.HTTPError as exc:
        raise ProviderUnavailable(f"gemini network error: {type(exc).__name__}") from exc

    if resp.status_code != 200:
        raise ProviderUnavailable(f"gemini HTTP {resp.status_code}")

    try:
        data = resp.json()
    except ValueError as exc:
        raise ProviderUnavailable("gemini: non-JSON response") from exc

    candidates = data.get("candidates") or []
    if not candidates:
        raise ProviderUnavailable("gemini: no candidates in response")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
    if not parts or not any(isinstance(p, dict) and "text" in p for p in parts):
        raise ProviderUnavailable("gemini: malformed response (no text parts)")

    usage = data.get("usageMetadata") or {}
    return ProviderResponse(
        text=text,
        prompt_tokens=int(usage.get("promptTokenCount") or 0),
        completion_tokens=int(usage.get("candidatesTokenCount") or 0),
    )
