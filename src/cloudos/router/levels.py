"""Subscription-CLI routing (SUBSCRIPTION_PROVIDERS.md; supersedes old §9 levels).

Order: L0 deterministic → subscription CLI providers in configured priority
(claude → codex → gemini by default) → NO PAID FALLBACK, EVER:

* any provider quota-exhausted (or cooling down) ⇒ CloudOSError(QUOTA_EXHAUSTED)
  — the worker DEFERS the job until the plan window resets
* all providers need login                       ⇒ CloudOSError(AUTH_REQUIRED)
* any provider in a billing-risk state           ⇒ CloudOSError(BILLING_RISK)
  — hard fail-closed; only the owner clears it
* nothing installed/enabled                      ⇒ DEPENDENCY_UNAVAILABLE

Privacy (not weakened — extended):
* the secret scan runs on the outbound prompt before EVERY provider call
* PERSONAL / SENSITIVE / SECRET labels never go to any provider
* INTERNAL is sendable to the owner's own paid subscription assistants
  (claude/codex) but keeps the strict external_ok rule for google_cli, whose
  OAuth mode carries unpaid-tier data-use terms.

Paid AI remains PERMANENTLY DISABLED: there is no HTTP client in this package,
``ALLOW_PAID_AI`` is never consulted to enable anything, and explicit paid
requests raise PAID_DISABLED.

Every route() call — success or failure — writes ONE compact agent_runs row
when a DB is configured (hashes + counts + auth_mode only, never prompt/output
text, never credentials); a failed write never fails the route.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from cloudos.config import Settings, get_settings
from cloudos.contracts import (
    CloudOSError,
    ErrorCode,
    PrivacyLabel,
    ProviderState,
    RouteRequest,
    RouteResult,
    sha256_hash,
)

from . import quota
from .providers import (
    ProviderError,
    ProviderResponse,
    ProviderStatus,
    ProviderUnavailable,
)
from .providers import claude_cli, codex_cli, google_cli

log = logging.getLogger("cloudos.router")

#: $0-incremental invariant — never flip, never read from env.
PAID_AI_PERMANENTLY_DISABLED = True

#: Subscription provider registry, keyed by the name used in
#: SUBSCRIPTION_PROVIDERS and model_hint. Order comes from settings.
PROVIDERS = {
    "claude": claude_cli,
    "codex": codex_cli,
    "gemini": google_cli,
}

#: Providers backed by the owner's PAID subscriptions, trusted with INTERNAL
#: content. google_cli is absent on purpose: its OAuth mode carries unpaid-tier
#: data-use terms, so it keeps the strict PUBLIC/external_ok gate.
SUBSCRIPTION_TRUSTED = frozenset({"claude", "codex"})


# --------------------------------------------------------------------------- L0

def _task_echo(req: RouteRequest) -> str:
    return req.prompt


def _task_hash(req: RouteRequest) -> str:
    return sha256_hash(req.prompt)


def _task_wordcount(req: RouteRequest) -> str:
    return str(len(req.prompt.split()))


DETERMINISTIC_TASKS: dict[str, Callable[[RouteRequest], str]] = {
    "echo": _task_echo,
    "hash": _task_hash,
    "wordcount": _task_wordcount,
}


def register_deterministic_task(name: str, fn: Callable[[RouteRequest], str]) -> None:
    """Register (or replace) a deterministic L0 implementation for a task type."""
    DETERMINISTIC_TASKS[name] = fn


# ------------------------------------------------------------------------ route

def route(req: RouteRequest) -> RouteResult:
    """Route a request: L0 → subscription CLIs → defer. Raises CloudOSError.
    RouteResult.cost_usd is always 0.0 (no incremental metered cost exists)."""
    settings = get_settings()
    started = datetime.now(timezone.utc)
    state: dict = {"level": None, "model": None, "auth_mode": None}

    try:
        result = _route_inner(req, settings, state)
    except CloudOSError as exc:
        _record_run(settings, req, state, started, status="failed",
                    error_code=exc.code.value, error=exc.message)
        raise
    except Exception as exc:  # noqa: BLE001 — normalize unexpected bugs
        log.exception("router internal error")
        err = CloudOSError(ErrorCode.INTERNAL_ERROR, f"router internal error: {type(exc).__name__}")
        _record_run(settings, req, state, started, status="failed",
                    error_code=err.code.value, error=err.message)
        raise err from exc

    _record_run(settings, req, state, started, status="succeeded", result=result)
    return result


def _route_inner(req: RouteRequest, settings: Settings, state: dict) -> RouteResult:
    if not isinstance(req.prompt, str):
        raise CloudOSError(ErrorCode.VALIDATION_ERROR, "prompt must be a string")

    if _paid_requested(req):
        raise CloudOSError(
            ErrorCode.PAID_DISABLED,
            "paid AI is permanently disabled (no incremental AI spend, ever)",
            details={"requested": "paid/level>=3"},
        )

    enabled = [p for p in settings.subscription_providers if p in PROVIDERS]
    if req.model_hint:
        if req.model_hint not in PROVIDERS:
            raise CloudOSError(
                ErrorCode.MODEL_NOT_ALLOWED,
                "model_hint must name a subscription provider (claude|codex|gemini)",
                details={"model_hint": req.model_hint},
            )
        if req.model_hint not in enabled:
            raise CloudOSError(
                ErrorCode.MODEL_NOT_ALLOWED,
                "hinted provider is not enabled in SUBSCRIPTION_PROVIDERS",
                details={"model_hint": req.model_hint},
            )
        enabled = [req.model_hint]

    # ---- L0: deterministic code, no AI
    impl = DETERMINISTIC_TASKS.get(req.task)
    if impl is not None:
        model = f"deterministic:{req.task}"
        state.update(level=0, model=model, auth_mode="none")
        return RouteResult(ok=True, text=impl(req), model=model, level=0, cost_usd=0.0)

    if not enabled:
        raise CloudOSError(
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            "no subscription AI provider enabled",
            details={"task": req.task},
        )

    # ---- privacy: the prompt leaves this process for EVERY provider.
    # Baseline (subscription-trusted) pass: PERSONAL+ never goes out; secrets
    # never go out. Non-trusted providers are re-gated strictly in the loop.
    _privacy_gate_or_raise(req, trusted=None)

    exhausted: list[str] = []
    need_auth: list[str] = []
    errors: list[str] = []
    now = datetime.now(timezone.utc)

    for level, name in enumerate(enabled, start=1):
        provider = PROVIDERS[name]

        cooldown = _cooldown_until(settings, name)
        if cooldown and cooldown > now:
            exhausted.append(name)
            log.info("provider %s cooling down until %s — skipped", name, cooldown.isoformat())
            continue

        # google_cli keeps the strict unpaid-external gate; claude/codex are
        # subscription-trusted for INTERNAL. Re-gate only when stricter.
        if name not in SUBSCRIPTION_TRUSTED:
            _privacy_gate_or_raise(req, trusted=False)

        state.update(level=level, model=f"{name}_cli")
        try:
            pr: ProviderResponse = provider.generate(
                req.prompt, max_tokens=req.max_tokens, timeout=settings.cli_timeout_seconds
            )
        except ProviderError as exc:
            _update_provider_status(
                settings, ProviderStatus(name, exc.state, detail=exc.detail),
                success=False,
                cooldown_minutes=_cooldown_for(settings, exc.state),
            )
            if exc.state is ProviderState.BILLING_RISK:
                raise CloudOSError(
                    ErrorCode.BILLING_RISK,
                    f"provider {name} is in a billing-risk state — refusing to execute",
                    details={"provider": name, "detail": exc.detail},
                ) from exc
            if exc.state is ProviderState.QUOTA_EXHAUSTED:
                exhausted.append(name)
            elif exc.state is ProviderState.AUTH_REQUIRED:
                need_auth.append(name)
            log.warning("provider %s: %s — trying next subscription provider", name, exc.detail)
            continue
        except ProviderUnavailable as exc:
            _update_provider_status(
                settings, ProviderStatus(name, ProviderState.UNAVAILABLE, detail=str(exc)), success=False
            )
            errors.append(f"{name}: {exc}")
            continue

        state.update(auth_mode=pr.auth_mode)
        _update_provider_status(
            settings,
            ProviderStatus(name, ProviderState.AVAILABLE_SUBSCRIPTION, auth_mode=pr.auth_mode),
            success=True,
        )
        _count_usage(name)
        log.info("provider %s served task=%s auth_mode=%s", name, req.task, pr.auth_mode)
        return RouteResult(
            ok=True,
            text=pr.text,
            model=f"{name}_cli",
            level=level,
            prompt_tokens=pr.prompt_tokens,
            completion_tokens=pr.completion_tokens,
            cost_usd=0.0,  # invariant: no incremental metered cost exists
        )

    # ---- nothing served — preserve the task, never spend money
    if exhausted:
        raise CloudOSError(
            ErrorCode.QUOTA_EXHAUSTED,
            "subscription allowance exhausted — task deferred until the plan window resets",
            details={
                "providers": exhausted,
                "retry_after_minutes": settings.ai_retry_after_quota_minutes,
            },
        )
    if need_auth:
        raise CloudOSError(
            ErrorCode.AUTH_REQUIRED,
            "no subscription provider is logged in — owner login required",
            details={"providers": need_auth},
        )
    raise CloudOSError(
        ErrorCode.DEPENDENCY_UNAVAILABLE,
        "no subscription AI provider could serve",
        details={"errors": errors} if errors else {"task": req.task},
    )


# ------------------------------------------------------------------- helpers

def probe_all() -> list[ProviderStatus]:
    """Passive probe of every registered provider (no AI calls). Also refreshes
    provider_status rows when a DB is configured."""
    settings = get_settings()
    statuses = []
    for name in settings.subscription_providers:
        provider = PROVIDERS.get(name)
        if provider is None:
            continue
        try:
            status = provider.probe()
        except Exception as exc:  # noqa: BLE001
            status = ProviderStatus(name, ProviderState.UNAVAILABLE, detail=f"probe error: {type(exc).__name__}")
        statuses.append(status)
        _update_provider_status(settings, status, success=None, validate=True)
    return statuses


def _paid_requested(req: RouteRequest) -> bool:
    meta = req.meta if isinstance(req.meta, dict) else {}
    level = meta.get("level")
    if level is not None:
        try:
            if int(level) >= 3:
                return True
        except (TypeError, ValueError):
            pass
    return bool(meta.get("allow_paid") or meta.get("force_paid"))


def _privacy_gate_or_raise(req: RouteRequest, trusted: Optional[bool]) -> None:
    """Secret scan + label gate over the outbound prompt.

    trusted=None  → baseline pass for subscription-trusted providers:
                    INTERNAL is sendable (owner's paid assistants), PERSONAL+
                    and secrets never are.
    trusted=False → strict unpaid-external rule (google_cli): identical to the
                    original §9-L2 gate.
    """
    try:
        from cloudos.privacy import gate
    except Exception as exc:  # noqa: BLE001
        raise CloudOSError(
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            "privacy gate unavailable — refusing AI call (fail closed)",
            details={"import_error": type(exc).__name__},
        ) from exc

    frontmatter = None
    if trusted is None and req.privacy_label is PrivacyLabel.INTERNAL:
        # Deterministic allowance for the owner's paid subscription assistants;
        # the secret scan inside gate() still applies unchanged.
        frontmatter = {"external_ok": True}

    decision = gate(json.dumps({"prompt": req.prompt}, ensure_ascii=False), req.privacy_label, frontmatter)
    if getattr(decision, "allowed", False):
        return

    hits = list(getattr(decision, "hits", None) or [])
    if hits:
        patterns = [getattr(h, "pattern_name", "unknown") for h in hits]
        raise CloudOSError(
            ErrorCode.SECRET_DETECTED,
            "secret material detected in outbound prompt — AI call blocked",
            details={"patterns": patterns, "hit_count": len(hits)},
        )
    reason = getattr(decision, "reason", "") or "privacy gate blocked AI call"
    raise CloudOSError(
        ErrorCode.PRIVACY_BLOCKED, reason, details={"privacy_label": req.privacy_label.value}
    )


def _cooldown_for(settings: Settings, state: ProviderState) -> Optional[int]:
    if state is ProviderState.QUOTA_EXHAUSTED:
        return settings.ai_retry_after_quota_minutes
    if state is ProviderState.AUTH_REQUIRED:
        return settings.ai_retry_after_auth_minutes
    return None


def _cooldown_until(settings: Settings, provider: str) -> Optional[datetime]:
    """Best-effort read of provider_status.cooldown_until. No DB → None."""
    if not settings.database_url:
        return None
    try:
        from cloudos import db

        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT cooldown_until FROM provider_status WHERE provider = %s",
                    (provider,),
                )
                row = cur.fetchone()
        if not row:
            return None
        value = row.get("cooldown_until") if isinstance(row, dict) else row[0]
        return value
    except Exception as exc:  # noqa: BLE001
        log.debug("cooldown read failed for %s: %s", provider, exc)
        return None


def _count_usage(provider: str) -> None:
    """Observability counter (quota_usage) — best effort, no budget gate."""
    try:
        quota.get_store().increment(provider, quota.today(), requests=1)
    except Exception as exc:  # noqa: BLE001
        log.debug("usage count failed for %s: %s", provider, exc)


def _update_provider_status(
    settings: Settings,
    status: ProviderStatus,
    success: Optional[bool],
    cooldown_minutes: Optional[int] = None,
    validate: bool = False,
) -> None:
    """Upsert one provider_status row. Best effort; NEVER stores credentials."""
    if not settings.database_url:
        return
    now = datetime.now(timezone.utc)
    cooldown_until = now + timedelta(minutes=cooldown_minutes) if cooldown_minutes else None
    try:
        from cloudos import db

        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO provider_status
                        (provider, auth_mode, provider_type, state, last_success,
                         last_auth_validation, task_count, last_failure_reason,
                         cooldown_until, updated_at)
                    VALUES (%s, %s, 'subscription_cli', %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (provider) DO UPDATE SET
                        auth_mode = COALESCE(NULLIF(EXCLUDED.auth_mode, ''), provider_status.auth_mode),
                        state = EXCLUDED.state,
                        last_success = COALESCE(EXCLUDED.last_success, provider_status.last_success),
                        last_auth_validation = COALESCE(EXCLUDED.last_auth_validation,
                                                        provider_status.last_auth_validation),
                        task_count = provider_status.task_count + EXCLUDED.task_count,
                        last_failure_reason = COALESCE(EXCLUDED.last_failure_reason,
                                                       provider_status.last_failure_reason),
                        cooldown_until = EXCLUDED.cooldown_until,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (
                        status.provider,
                        status.auth_mode or "",
                        status.state.value,
                        now if success else None,
                        now if (validate or success is not None) else None,
                        1 if success else 0,
                        None if success else (status.detail or None),
                        cooldown_until,
                        now,
                    ),
                )
            try:
                conn.commit()
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001
        log.debug("provider_status upsert failed for %s: %s", status.provider, exc)


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
    """ONE compact agent_runs row: hashes + counts + auth_mode only — NEVER
    prompt/output text, NEVER credentials. Best-effort."""
    if not settings.database_url:
        return
    finished = datetime.now(timezone.utc)
    try:
        from cloudos import db

        meta = req.meta if isinstance(req.meta, dict) else {}
        output_hash = (
            sha256_hash(result.text) if result is not None and result.text is not None else None
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
                        json.dumps({"auth_mode": state.get("auth_mode")}),
                    ),
                )
            try:
                conn.commit()
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001
        log.warning("agent_runs write failed (route outcome unaffected): %s", exc)
