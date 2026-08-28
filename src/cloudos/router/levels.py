"""Level routing (contracts §9): L0 deterministic → L1 Workers AI → L2 Gemini.

L3 (paid) is PERMANENTLY DISABLED. ``ALLOW_PAID_AI`` is treated as false
regardless of the environment — there is no code path that invokes a paid
model, and requesting one raises ``CloudOSError(PAID_DISABLED)``.

Escalation semantics:
  * L0 answers iff ``req.task`` has a registered deterministic implementation.
  * L1/L2 are each tried only when configured AND under their daily budget.
    A level that is unconfigured, at/over budget, or errors (5xx/network) lets
    the next FREE level try.
  * If nothing served: all levels unconfigured ⇒ DEPENDENCY_UNAVAILABLE
    ("no free AI provider configured"); any budget was hit ⇒ QUOTA_EXHAUSTED
    (fail closed — never silently absorbed); otherwise (providers errored)
    ⇒ DEPENDENCY_UNAVAILABLE.
  * L2 privacy failures (PRIVACY_BLOCKED / SECRET_DETECTED) are terminal —
    they never "fall through", and the gate runs BEFORE any network call.

Every route() call — success or failure — writes ONE compact agent_runs row
when a DB is configured (hashes + counts only, never prompt/output text);
a failed write never fails the route.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from cloudos.config import Settings, get_settings
from cloudos.contracts import (
    CloudOSError,
    ErrorCode,
    RouteRequest,
    RouteResult,
    sha256_hash,
)

from . import quota
from .providers import ProviderResponse, ProviderUnavailable
from .providers import gemini as gemini_provider
from .providers import workers_ai as workers_ai_provider

log = logging.getLogger("cloudos.router")

#: $0/month invariant — never flip, never read from env. contracts §0/§9.
PAID_AI_PERMANENTLY_DISABLED = True


# --------------------------------------------------------------------------- L0

def _task_echo(req: RouteRequest) -> str:
    return req.prompt


def _task_hash(req: RouteRequest) -> str:
    return sha256_hash(req.prompt)


def _task_wordcount(req: RouteRequest) -> str:
    return str(len(req.prompt.split()))


#: Deterministic task registry. If req.task has an entry, it answers at L0
#: with no AI involved; otherwise the router escalates to free AI levels.
DETERMINISTIC_TASKS: dict[str, Callable[[RouteRequest], str]] = {
    "echo": _task_echo,
    "hash": _task_hash,
    "wordcount": _task_wordcount,
}


def register_deterministic_task(name: str, fn: Callable[[RouteRequest], str]) -> None:
    """Register (or replace) a deterministic L0 implementation for a task type."""
    DETERMINISTIC_TASKS[name] = fn


# --------------------------------------------------------------------- allowlist

@dataclass(frozen=True)
class Allowlist:
    workers_ai: tuple[str, ...]
    gemini: tuple[str, ...]


def load_allowlist(path: str) -> Allowlist:
    """Load config/free_model_allowlist.json. Unreadable/invalid file FAILS
    CLOSED: no models are allowed (providers become unusable, hints rejected)."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return Allowlist(
            workers_ai=tuple(m for m in data.get("workers_ai", []) if isinstance(m, str)),
            gemini=tuple(m for m in data.get("gemini", []) if isinstance(m, str)),
        )
    except Exception as exc:  # noqa: BLE001
        log.error(
            "free model allowlist unreadable at %s (%s) — failing closed: no models allowed",
            path,
            exc,
        )
        return Allowlist(workers_ai=(), gemini=())


# ------------------------------------------------------------------------ route

def route(req: RouteRequest) -> RouteResult:
    """Route a request through free levels only. Raises CloudOSError on failure
    (QUOTA_EXHAUSTED / PAID_DISABLED / MODEL_NOT_ALLOWED / PRIVACY_BLOCKED /
    SECRET_DETECTED / DEPENDENCY_UNAVAILABLE / VALIDATION_ERROR / INTERNAL_ERROR).
    RouteResult.cost_usd is always 0.0."""
    settings = get_settings()
    started = datetime.now(timezone.utc)
    state: dict = {"level": None, "model": None}

    try:
        result = _route_inner(req, settings, state)
    except CloudOSError as exc:
        _record_run(
            settings, req, state, started,
            status="failed", error_code=exc.code.value, error=exc.message,
        )
        raise
    except Exception as exc:  # noqa: BLE001 — normalize unexpected bugs
        log.exception("router internal error")
        err = CloudOSError(
            ErrorCode.INTERNAL_ERROR, f"router internal error: {type(exc).__name__}"
        )
        _record_run(
            settings, req, state, started,
            status="failed", error_code=err.code.value, error=err.message,
        )
        raise err from exc

    _record_run(settings, req, state, started, status="succeeded", result=result)
    return result


def _route_inner(req: RouteRequest, settings: Settings, state: dict) -> RouteResult:
    if not isinstance(req.prompt, str):
        raise CloudOSError(ErrorCode.VALIDATION_ERROR, "prompt must be a string")

    # L3 guard first: paid AI is permanently disabled. settings.allow_paid_ai is
    # deliberately never consulted to ENABLE anything — even ALLOW_PAID_AI=true
    # in the environment changes nothing.
    if _paid_requested(req):
        raise CloudOSError(
            ErrorCode.PAID_DISABLED,
            "L3 paid AI is permanently disabled ($0/month invariant)",
            details={"requested": "paid/level>=3"},
        )

    allow = load_allowlist(settings.free_model_allowlist_path)
    if req.model_hint and (
        req.model_hint not in allow.workers_ai and req.model_hint not in allow.gemini
    ):
        raise CloudOSError(
            ErrorCode.MODEL_NOT_ALLOWED,
            "model_hint is not in the free model allowlist",
            details={"model_hint": req.model_hint},
        )

    # ---- L0: deterministic code, no AI
    impl = DETERMINISTIC_TASKS.get(req.task)
    if impl is not None:
        model = f"deterministic:{req.task}"
        state.update(level=0, model=model)
        return RouteResult(ok=True, text=impl(req), model=model, level=0, cost_usd=0.0)

    store = quota.get_store()
    day = quota.today()
    budget_hit: list[str] = []
    provider_errors: list[str] = []
    any_configured = False

    # ---- L1: Cloudflare Workers AI (free allocation)
    if settings.cf_account_id and settings.cf_workers_ai_token and allow.workers_ai:
        any_configured = True
        model = req.model_hint if req.model_hint in allow.workers_ai else allow.workers_ai[0]
        if _reserve(store, "workers_ai", day, settings.workers_ai_daily_budget):
            state.update(level=1, model=model)
            try:
                pr = workers_ai_provider.generate(
                    account_id=settings.cf_account_id,
                    token=settings.cf_workers_ai_token,
                    model=model,
                    prompt=req.prompt,
                    max_tokens=req.max_tokens,
                )
                return _success(pr, model=model, level=1)
            except ProviderUnavailable as exc:
                log.warning("L1 workers_ai unavailable, trying next free level: %s", exc)
                provider_errors.append(f"workers_ai: {exc}")
        else:
            budget_hit.append("workers_ai")
            log.warning(
                "L1 workers_ai at/over daily budget (%d) — fail closed",
                settings.workers_ai_daily_budget,
            )

    # ---- L2: Gemini free tier (privacy-gated)
    if settings.gemini_api_key and allow.gemini:
        any_configured = True
        model = req.model_hint if req.model_hint in allow.gemini else allow.gemini[0]
        state.update(level=2, model=model)
        payload = gemini_provider.build_payload(req.prompt, req.max_tokens)
        # BEFORE any network call; raises PRIVACY_BLOCKED / SECRET_DETECTED
        # (terminal — privacy failures never fall through).
        _privacy_gate_or_raise(payload, req)
        if _reserve(store, "gemini", day, settings.gemini_daily_request_budget):
            try:
                pr = gemini_provider.generate(
                    api_key=settings.gemini_api_key, model=model, payload=payload
                )
                return _success(pr, model=model, level=2)
            except ProviderUnavailable as exc:
                log.warning("L2 gemini unavailable: %s", exc)
                provider_errors.append(f"gemini: {exc}")
        else:
            budget_hit.append("gemini")
            log.warning(
                "L2 gemini at/over daily budget (%d) — fail closed",
                settings.gemini_daily_request_budget,
            )

    # ---- nothing served
    if not any_configured:
        raise CloudOSError(
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            "no free AI provider configured",
            details={"task": req.task},
        )
    if budget_hit:
        raise CloudOSError(
            ErrorCode.QUOTA_EXHAUSTED,
            "daily free-tier budget exhausted — failing closed (no paid fallback exists)",
            details={"providers": budget_hit, "day": day.isoformat()},
        )
    raise CloudOSError(
        ErrorCode.DEPENDENCY_UNAVAILABLE,
        "all configured free AI providers failed",
        details={"errors": provider_errors},
    )


def _success(pr: ProviderResponse, model: str, level: int) -> RouteResult:
    return RouteResult(
        ok=True,
        text=pr.text,
        model=model,
        level=level,
        prompt_tokens=pr.prompt_tokens,
        completion_tokens=pr.completion_tokens,
        cost_usd=0.0,  # invariant
    )


def _paid_requested(req: RouteRequest) -> bool:
    """True if the request explicitly asks for a paid level (meta.level >= 3,
    meta.allow_paid / meta.force_paid). Model hints for paid models are handled
    separately as MODEL_NOT_ALLOWED."""
    meta = req.meta if isinstance(req.meta, dict) else {}
    level = meta.get("level")
    if level is not None:
        try:
            if int(level) >= 3:
                return True
        except (TypeError, ValueError):
            pass
    return bool(meta.get("allow_paid") or meta.get("force_paid"))


def _reserve(store: "quota.QuotaStore", provider: str, day, budget: int) -> bool:
    """Check-then-increment one request against the daily budget.

    True  ⇒ a request slot was reserved (the call may proceed).
    False ⇒ at/over budget, OR the quota store failed — both FAIL CLOSED.
    The increment happens BEFORE the network call so a crash mid-call still
    counts against the budget (conservative accounting).
    """
    try:
        usage = store.get_usage(provider, day)
        if usage.requests >= budget:
            return False
        store.increment(provider, day, requests=1)
        return True
    except quota.QuotaStoreError as exc:
        log.error(
            "quota store failure for %s — treating as OVER budget (fail closed): %s",
            provider,
            exc,
        )
        return False


def _privacy_gate_or_raise(payload: dict, req: RouteRequest) -> None:
    """Run the privacy gate over the FULL outbound payload (contracts §9 L2).

    Lazy import so the router package loads without cloudos.privacy present;
    if the gate itself is unavailable we FAIL CLOSED (no external send).
    Secret hits surface as safe metadata only: pattern names, never material.
    """
    outbound_text = json.dumps(payload, ensure_ascii=False)
    try:
        from cloudos.privacy import gate
    except Exception as exc:  # noqa: BLE001
        raise CloudOSError(
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            "privacy gate unavailable — refusing external AI call (fail closed)",
            details={"import_error": type(exc).__name__},
        ) from exc

    decision = gate(outbound_text, req.privacy_label)
    if getattr(decision, "allowed", False):
        return

    hits = list(getattr(decision, "hits", None) or [])
    if hits:
        patterns = [getattr(h, "pattern_name", "unknown") for h in hits]
        raise CloudOSError(
            ErrorCode.SECRET_DETECTED,
            "secret material detected in outbound payload — external AI call blocked",
            details={"patterns": patterns, "hit_count": len(hits)},
        )
    reason = getattr(decision, "reason", "") or "privacy gate blocked external AI call"
    raise CloudOSError(
        ErrorCode.PRIVACY_BLOCKED,
        reason,
        details={"privacy_label": req.privacy_label.value},
    )


# ------------------------------------------------------------------- agent_runs

def _record_run(
    settings: Settings,
    req: RouteRequest,
    state: dict,
    started: datetime,
    status: str,
    error_code: Optional[str] = None,
    error: Optional[str] = None,
    result: Optional[RouteResult] = None,
) -> None:
    """Write ONE compact agent_runs row (contracts §5): hashes + counts only,
    NEVER prompt or output text. Best-effort — a failed write never fails the
    route. Skipped entirely when no DB is configured."""
    if not settings.database_url:
        return
    finished = datetime.now(timezone.utc)
    try:
        from cloudos import db  # lazy: Agent 4's module

        meta = req.meta if isinstance(req.meta, dict) else {}
        output_hash = (
            sha256_hash(result.text)
            if result is not None and result.text is not None
            else None
        )
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO agent_runs "
                    "(run_id, job_id, agent, workflow, model, level, started_at, "
                    " finished_at, status, prompt_tokens, completion_tokens, cost_usd, "
                    " error_code, error, input_hash, output_hash, meta) "
                    "VALUES (%s, %s, 'router', %s, %s, %s, %s, %s, %s, %s, %s, 0, "
                    "%s, %s, %s, %s, %s)",
                    (
                        str(uuid.uuid4()),
                        meta.get("job_id"),
                        req.task,
                        state.get("model"),
                        state.get("level"),
                        started,
                        finished,
                        status,
                        result.prompt_tokens if result is not None else 0,
                        result.completion_tokens if result is not None else 0,
                        error_code,
                        error,
                        sha256_hash(req.prompt) if isinstance(req.prompt, str) else None,
                        output_hash,
                        json.dumps({}),
                    ),
                )
            try:
                conn.commit()
            except Exception:  # noqa: BLE001 — autocommit pools
                pass
    except Exception as exc:  # noqa: BLE001
        log.warning("agent_runs write failed (route outcome unaffected): %s", exc)
