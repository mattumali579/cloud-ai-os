"""Cloud AI OS — Agent API (contract §7).

App target: `uvicorn cloudos.api.app:app`.
Auth: `Authorization: Bearer $AGENT_API_TOKEN` on everything except
GET /healthz (open) and POST /v1/webhooks/n8n (bearer OR HMAC X-Signature).
"""
from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, Query, Request

from cloudos.config import get_settings
from cloudos.contracts import CloudOSError, ErrorCode, PrivacyLabel, RouteRequest
from cloudos.brightreach.service import BookingInput, ClientSetup, LeadInput, get_service

from . import store
from .auth import require_bearer, require_webhook_auth
from .errors import NotFound, install_exception_handlers
from .schemas import InvokeRequest, JobCreate

log = logging.getLogger("cloudos.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Idempotent migrations at startup; the DB may be down (or db.py absent
    # while agents build in parallel) — log and serve anyway (§5, task brief).
    try:
        from cloudos import db

        applied = db.migrate()
        log.info("startup migrate ok, applied: %s", applied)
    except Exception as exc:  # noqa: BLE001 — never crash startup on DB issues
        log.warning("startup migrate skipped (%s: %s)", exc.__class__.__name__, exc)
    yield


app = FastAPI(title="Cloud AI OS — Agent API", version="1.0", lifespan=lifespan)
install_exception_handlers(app)

authed = Depends(require_bearer)


def _brightreach(callable_):
    """Keep the small BrightReach flow on the API's normal safe error shape."""
    try:
        return callable_()
    except KeyError as exc:
        raise NotFound(str(exc)) from None
    except ValueError as exc:
        raise CloudOSError(ErrorCode.VALIDATION_ERROR, str(exc)) from None


# ---------------------------------------------------------------- health

@app.get("/healthz")
async def healthz() -> dict:
    """Open endpoint. Always 200; `db` reflects reachability, not liveness of the API."""
    db_ok = False
    try:
        from cloudos import db

        db_ok = bool(db.healthcheck())
    except Exception:  # module missing or healthcheck itself broken → report false
        db_ok = False
    return {"status": "ok", "db": db_ok}


# ---------------------------------------------------------------- jobs

@app.post("/v1/jobs", status_code=201, dependencies=[authed])
async def create_job(body: JobCreate) -> dict:
    return store.create_job(
        type=body.type,
        payload=body.payload,
        priority=body.priority,
        run_at=body.run_at,
        max_attempts=body.max_attempts,
    )


@app.get("/v1/jobs/{job_id}", dependencies=[authed])
async def get_job(job_id: str) -> dict:
    return store.get_job(job_id)


@app.get("/v1/jobs", dependencies=[authed])
async def list_jobs(
    status: Optional[str] = Query(default=None),
    type: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    return {"jobs": store.list_jobs(status=status, type=type, limit=limit)}


@app.post("/v1/jobs/{job_id}/cancel", dependencies=[authed])
async def cancel_job(job_id: str) -> dict:
    return store.cancel_job(job_id)


# ---------------------------------------------------------------- agent invoke

@app.post("/v1/agent/invoke", dependencies=[authed])
async def agent_invoke(body: InvokeRequest) -> dict:
    try:
        label = PrivacyLabel(body.privacy_label.strip().lower())
    except ValueError:
        raise CloudOSError(
            ErrorCode.VALIDATION_ERROR,
            "invalid privacy_label",
            {"privacy_label": body.privacy_label[:64], "allowed": [p.value for p in PrivacyLabel]},
        ) from None

    try:
        from cloudos.router import route
    except Exception as exc:  # router package built by Agent 8 — may be absent
        raise CloudOSError(
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            "router unavailable",
            {"reason": exc.__class__.__name__},
        ) from exc

    req = RouteRequest(
        prompt=body.prompt,
        task=body.task,
        privacy_label=label,
        max_tokens=body.max_tokens,
        model_hint=body.model_hint,
    )
    result = route(req)  # CloudOSError propagates to the §6 handler
    return result.to_dict()


# ---------------------------------------------------------------- observability

@app.get("/v1/runs", dependencies=[authed])
async def list_runs(limit: int = Query(default=50, ge=1, le=200)) -> dict:
    return {"runs": store.list_runs(limit=limit)}


@app.get("/v1/quota", dependencies=[authed])
async def quota() -> dict:
    """Daily usage + provider state. Keeps the {usage, budgets, fail_closed}
    shape (n8n daily_ops reads it); budgets are now informational counters —
    hard limits live with each subscription plan, not here."""
    usage = store.quota_today()
    providers = store.provider_status_rows()
    fail_closed = {
        row.get("provider"): row.get("state")
        in ("QUOTA_EXHAUSTED", "AUTH_REQUIRED", "BILLING_RISK")
        for row in providers
    }
    return {"usage": usage, "budgets": {}, "fail_closed": fail_closed, "providers": providers}


# ---------------------------------------------------------------- BrightReach

@app.post("/v1/brightreach/clients", status_code=201, dependencies=[authed])
async def brightreach_create_client(body: ClientSetup) -> dict:
    """Store a client's service, area, and plain qualification rules."""
    return _brightreach(lambda: get_service().create_client(body))


@app.post("/v1/brightreach/clients/{client_id}/leads", status_code=201, dependencies=[authed])
async def brightreach_create_lead(client_id: str, body: LeadInput) -> dict:
    """Capture a lead and immediately create the safe response to hand off."""
    return _brightreach(lambda: get_service().create_lead(client_id, body))


@app.post("/v1/brightreach/leads/{lead_id}/qualify", dependencies=[authed])
async def brightreach_qualify(lead_id: str) -> dict:
    return _brightreach(lambda: get_service().qualify(lead_id))


@app.post("/v1/brightreach/leads/{lead_id}/follow-up", dependencies=[authed])
async def brightreach_follow_up(lead_id: str) -> dict:
    return _brightreach(lambda: get_service().follow_up(lead_id))


@app.post("/v1/brightreach/leads/{lead_id}/booking", dependencies=[authed])
async def brightreach_record_booking(lead_id: str, body: BookingInput) -> dict:
    """Record an event only after the connected calendar returns its real ID."""
    return _brightreach(lambda: get_service().record_booking(lead_id, body))


@app.get("/v1/brightreach/clients/{client_id}/report", dependencies=[authed])
async def brightreach_report(client_id: str) -> dict:
    return _brightreach(lambda: get_service().report(client_id))


@app.get("/v1/providers", dependencies=[authed])
async def providers(probe: bool = Query(default=False)) -> dict:
    """Subscription provider observability. probe=true runs a live (passive,
    no-AI-call) auth probe and refreshes stored rows."""
    live = None
    if probe:
        try:
            from cloudos.router import probe_all

            live = [
                {
                    "provider": s.provider,
                    "state": s.state.value,
                    "auth_mode": s.auth_mode,
                    "detail": s.detail,
                }
                for s in probe_all()
            ]
        except Exception:  # noqa: BLE001 — router absent: stored rows still serve
            live = None
    return {"providers": store.provider_status_rows(), "probe": live}


# ---------------------------------------------------------------- webhooks

@app.post("/v1/webhooks/n8n", status_code=201)
async def n8n_webhook(request: Request) -> dict:
    raw = await request.body()
    # Auth first (bearer OR HMAC over the RAW body) — never parse unauthenticated input.
    require_webhook_auth(request, raw)

    try:
        body = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise CloudOSError(ErrorCode.VALIDATION_ERROR, "malformed JSON body") from None
    if not isinstance(body, dict):
        raise CloudOSError(ErrorCode.VALIDATION_ERROR, "body must be a JSON object")

    event = body.get("event")
    if not isinstance(event, str) or not event.strip():
        raise CloudOSError(ErrorCode.VALIDATION_ERROR, "event must be a non-empty string")
    data = body.get("data", {})
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise CloudOSError(ErrorCode.VALIDATION_ERROR, "data must be a JSON object")

    return store.create_job(type=f"webhook.{event.strip()}", payload=data)
