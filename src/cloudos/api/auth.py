"""Bearer + webhook-HMAC authentication for the Agent API.

Fail closed: if AGENT_API_TOKEN is unset, every authenticated route rejects.
"""
from __future__ import annotations

import hashlib
import hmac

from fastapi import Request

from cloudos.config import get_settings

from .errors import AuthFailed


def _bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization")
    if not header:
        return None
    scheme, _, credentials = header.partition(" ")
    if scheme.lower() != "bearer" or not credentials:
        return None
    return credentials.strip()


def _bearer_ok(request: Request) -> bool:
    configured = get_settings().agent_api_token
    presented = _bearer_token(request)
    if not configured or presented is None:
        return False
    return hmac.compare_digest(presented.encode("utf-8"), configured.encode("utf-8"))


def require_bearer(request: Request) -> None:
    """FastAPI dependency guarding every route except /healthz and the webhook."""
    if not _bearer_ok(request):
        raise AuthFailed("missing or invalid bearer token")


def require_webhook_auth(request: Request, raw_body: bytes) -> None:
    """Webhook accepts EITHER a valid bearer token OR X-Signature HMAC.

    X-Signature = hex sha256 HMAC of the raw request body, keyed with
    AGENT_API_TOKEN. An optional "sha256=" prefix is tolerated.
    """
    if _bearer_ok(request):
        return

    configured = get_settings().agent_api_token
    signature = request.headers.get("x-signature", "").strip().lower()
    if signature.startswith("sha256="):
        signature = signature[len("sha256=") :]
    if configured and signature:
        expected = hmac.new(configured.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        if hmac.compare_digest(signature, expected):
            return

    raise AuthFailed("webhook requires a valid bearer token or X-Signature HMAC")
