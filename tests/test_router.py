"""Router tests — subscription-CLI routing (docs/SUBSCRIPTION_PROVIDERS.md).

No real network, no real subprocess, no real DB:

* Router-level tests swap ``levels.PROVIDERS`` for recording fake CLI modules,
  so routing order, fallback, quota accounting and run recording are tested
  without touching a provider's internals.
* Provider-level tests drive the real ``claude_cli`` / ``codex_cli`` /
  ``google_cli`` modules against a temp HOME and a stub ``base.RUNNER`` — no
  CLI binary is ever executed (an autouse guard makes a real run impossible).
* Privacy: ``cloudos.privacy`` is shadowed with a recording stub for most
  tests, and exercised for real in ``TestPrivacyGateRealModule``.
* DB: ``cloudos.db`` is shadowed with a stub when a DB path is under test;
  otherwise DATABASE_URL is empty (local/no-db quota mode).
"""
from __future__ import annotations

import json
import re
import sys
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

from cloudos.config import reset_settings_cache
from cloudos.contracts import (
    CloudOSError,
    ErrorCode,
    GateDecision,
    PrivacyLabel,
    ProviderState,
    RouteRequest,
    SecretHit,
    sha256_hash,
)
from cloudos.router import levels, quota, route
from cloudos.router.providers import (
    ProviderError,
    ProviderResponse,
    ProviderStatus,
    ProviderUnavailable,
)
from cloudos.router.providers import base, claude_cli, codex_cli, google_cli

#: Built at runtime so no secret-shaped literal is ever stored in this file.
FAKE_AWS_KEY = "AKIA" + "Q" * 16


# ------------------------------------------------------------------- fixtures

@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    """Deterministic settings, fresh quota store, and no real subprocesses.

    Vars are set explicitly (not deleted) so a developer ``.env`` can never
    leak in through ``load_dotenv(override=False)``.
    """
    monkeypatch.setenv("SUBSCRIPTION_PROVIDERS", "claude,codex,gemini")
    for var in (
        "DATABASE_URL",
        "ALLOW_PAID_AI",
        "CLAUDE_CLI_MODEL",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "CLAUDE_CONFIG_DIR",
        "CODEX_WORKSPACE",
        "AI_RETRY_AFTER_QUOTA_MINUTES",
        "AI_RETRY_AFTER_AUTH_MINUTES",
        "CLI_TIMEOUT_SECONDS",
        "NOTIFY_WEBHOOK_URL",
    ):
        monkeypatch.setenv(var, "")
    for var, default in (
        ("CLAUDE_CLI_BIN", "claude"),
        ("CODEX_CLI_BIN", "codex"),
        ("GEMINI_CLI_BIN", "gemini"),
    ):
        monkeypatch.setenv(var, default)

    def _no_subprocess(*args, **kwargs):  # pragma: no cover - guard
        raise AssertionError("a test tried to run a real CLI subprocess")

    base.set_runner(_no_subprocess)
    reset_settings_cache()
    quota.reset_store()
    yield
    base.set_runner(None)
    reset_settings_cache()
    quota.reset_store()


class FakeProvider:
    """Stand-in for a provider module: records generate()/probe() calls."""

    def __init__(self, name, *, response=None, error=None, status=None):
        self.name = name
        self._response = response
        self._error = error
        self._status = status
        self.calls: list[tuple[str, dict]] = []
        self.probes = 0

    def generate(self, prompt, **kwargs) -> ProviderResponse:
        self.calls.append((prompt, kwargs))
        if self._error is not None:
            raise self._error
        if self._response is not None:
            return self._response
        return ProviderResponse(
            text=f"{self.name}-answer",
            prompt_tokens=3,
            completion_tokens=4,
            auth_mode=f"{self.name}_sub",
        )

    def probe(self) -> ProviderStatus:
        self.probes += 1
        if isinstance(self._status, Exception):
            raise self._status
        if self._status is not None:
            return self._status
        return ProviderStatus(
            self.name, ProviderState.AVAILABLE_SUBSCRIPTION, auth_mode=f"{self.name}_sub"
        )


@pytest.fixture
def providers(monkeypatch):
    """Replace the provider registry with fakes; returns {name: FakeProvider}."""
    fakes = {name: FakeProvider(name) for name in ("claude", "codex", "gemini")}
    monkeypatch.setattr(levels, "PROVIDERS", dict(fakes))
    return fakes


def set_provider(providers, name, *, error=None, response=None, status=None):
    """Swap one fake in the live registry (monkeypatched dict is a copy)."""
    fake = FakeProvider(name, error=error, response=response, status=status)
    providers[name] = fake
    levels.PROVIDERS[name] = fake
    return fake


def enable_only(monkeypatch, *names):
    monkeypatch.setenv("SUBSCRIPTION_PROVIDERS", ",".join(names))
    reset_settings_cache()


class PrivacyStub:
    def __init__(self):
        self.calls: list[tuple[str, PrivacyLabel, object]] = []
        self.decision = GateDecision(allowed=True, reason="ok")
        self.queue: list[GateDecision] = []

    def gate(self, content, label, frontmatter=None):
        self.calls.append((content, label, frontmatter))
        if self.queue:
            return self.queue.pop(0)
        return self.decision


@pytest.fixture
def privacy(monkeypatch):
    """Shadow cloudos.privacy with a recording stub gate."""
    stub = PrivacyStub()
    mod = types.ModuleType("cloudos.privacy")
    mod.gate = stub.gate
    monkeypatch.setitem(sys.modules, "cloudos.privacy", mod)
    return stub


class FakeCursor:
    def __init__(self, executed, rows):
        self._executed = executed
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        self._executed.append((sql, params))

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None


class FakeConn:
    def __init__(self, executed, rows):
        self._executed = executed
        self._rows = rows

    def cursor(self):
        return FakeCursor(self._executed, self._rows)

    def commit(self):
        pass


def _install_db_module(monkeypatch, get_conn):
    mod = types.ModuleType("cloudos.db")
    mod.get_conn = get_conn
    monkeypatch.setitem(sys.modules, "cloudos.db", mod)
    import cloudos

    monkeypatch.setattr(cloudos, "db", mod, raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://stub/stub")
    reset_settings_cache()


@pytest.fixture
def fake_db(monkeypatch):
    """Working stub DB: records every execute() as (sql, params)."""
    executed: list[tuple[str, tuple]] = []
    rows: list = []

    @contextmanager
    def get_conn():
        yield FakeConn(executed, rows)

    _install_db_module(monkeypatch, get_conn)
    return executed


@pytest.fixture
def broken_db(monkeypatch):
    """Stub DB whose every connection attempt fails."""

    @contextmanager
    def get_conn():
        raise RuntimeError("db down")
        yield  # pragma: no cover

    _install_db_module(monkeypatch, get_conn)


# -------------------------------------------------------------------- helpers

def make_req(prompt="hello world", **kwargs) -> RouteRequest:
    return RouteRequest(prompt=prompt, **kwargs)


class FakeCli:
    """Stub for base.RUNNER — records argv/kwargs, returns scripted results."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls: list[tuple[list, dict]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        if not self.results:
            return base.CliResult(0, "", "")
        return self.results.pop(0) if len(self.results) > 1 else self.results[0]


def install_cli(monkeypatch, *results) -> FakeCli:
    runner = FakeCli(*results)
    base.set_runner(runner)
    return runner


def ok(stdout="out", stderr=""):
    return base.CliResult(0, stdout, stderr)


def fail(stdout="", stderr="boom", rc=1, timed_out=False):
    return base.CliResult(rc, stdout, stderr, timed_out=timed_out)


@pytest.fixture
def cli_home(monkeypatch, tmp_path):
    """Point every provider's credential lookup at an empty temp HOME."""
    monkeypatch.setattr(base, "home", lambda: str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))
    monkeypatch.setattr(base, "which", lambda binary: f"/usr/bin/{binary}" if binary else None)
    return tmp_path


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


# ------------------------------------------------------------------------- L0

class TestL0Deterministic:
    def test_echo(self):
        res = route(make_req("hello world", task="echo"))
        assert res.ok is True
        assert res.level == 0
        assert res.model == "deterministic:echo"
        assert res.text == "hello world"
        assert res.cost_usd == 0.0

    def test_hash_is_sha256_of_prompt(self):
        res = route(make_req("abc", task="hash"))
        assert res.level == 0
        assert res.text == sha256_hash("abc")
        assert len(res.text) == 64

    def test_wordcount(self):
        res = route(make_req("one two  three", task="wordcount"))
        assert res.level == 0
        assert res.text == "3"

    def test_l0_never_touches_a_provider_or_the_privacy_gate(self, providers, privacy):
        res = route(make_req("x", task="echo"))
        assert res.ok and res.level == 0
        assert all(p.calls == [] for p in providers.values())
        assert privacy.calls == []

    def test_l0_works_with_no_provider_enabled(self, monkeypatch):
        enable_only(monkeypatch)  # SUBSCRIPTION_PROVIDERS=""
        res = route(make_req("hi", task="echo"))
        assert res.ok and res.level == 0

    def test_unknown_task_with_no_provider_enabled_fails(self, monkeypatch, privacy):
        enable_only(monkeypatch)
        with pytest.raises(CloudOSError) as ei:
            route(make_req(task="general"))
        assert ei.value.code is ErrorCode.DEPENDENCY_UNAVAILABLE
        assert "no subscription AI provider enabled" in ei.value.message

    def test_registry_is_extensible(self):
        levels.register_deterministic_task("shout", lambda req: req.prompt.upper())
        try:
            res = route(make_req("hi", task="shout"))
            assert res.level == 0 and res.text == "HI"
        finally:
            del levels.DETERMINISTIC_TASKS["shout"]


# ---------------------------------------------------------- order & fallback

class TestProviderOrder:
    def test_first_enabled_provider_serves(self, providers, privacy):
        res = route(make_req(task="general"))
        assert res.ok and res.level == 1
        assert res.model == "claude_cli"
        assert res.text == "claude-answer"
        assert (res.prompt_tokens, res.completion_tokens) == (3, 4)
        assert len(providers["claude"].calls) == 1
        assert providers["codex"].calls == [] and providers["gemini"].calls == []

    def test_generate_gets_prompt_max_tokens_and_timeout(self, providers, privacy):
        route(make_req("ping", task="general", max_tokens=64))
        prompt, kwargs = providers["claude"].calls[0]
        assert prompt == "ping"
        assert kwargs["max_tokens"] == 64
        assert kwargs["timeout"] == 300  # CLI_TIMEOUT_SECONDS default
        assert "web_search" not in kwargs  # claude has no such parameter

    def test_codex_receives_the_web_search_flag(self, providers, privacy):
        set_provider(providers, "claude", error=ProviderUnavailable("no binary"))
        route(make_req(task="general", meta={"web_search": True}))
        _, kwargs = providers["codex"].calls[0]
        assert kwargs["web_search"] is True

    def test_codex_web_search_defaults_off(self, providers, privacy):
        set_provider(providers, "claude", error=ProviderUnavailable("no binary"))
        route(make_req(task="general"))
        _, kwargs = providers["codex"].calls[0]
        assert kwargs["web_search"] is False

    def test_quota_exhausted_falls_through_to_the_next_provider(self, providers, privacy):
        set_provider(
            providers, "claude", error=ProviderError(ProviderState.QUOTA_EXHAUSTED, "plan limit")
        )
        res = route(make_req(task="general"))
        assert res.level == 2 and res.model == "codex_cli"
        assert len(providers["codex"].calls) == 1

    def test_auth_required_falls_through_to_the_next_provider(self, providers, privacy):
        set_provider(
            providers, "claude", error=ProviderError(ProviderState.AUTH_REQUIRED, "not logged in")
        )
        res = route(make_req(task="general"))
        assert res.level == 2 and res.model == "codex_cli"

    def test_unavailable_provider_is_skipped(self, providers, privacy):
        set_provider(providers, "claude", error=ProviderUnavailable("binary missing"))
        res = route(make_req(task="general"))
        assert res.level == 2 and res.model == "codex_cli"

    def test_third_provider_is_reached_last(self, providers, privacy):
        set_provider(providers, "claude", error=ProviderUnavailable("x"))
        set_provider(providers, "codex", error=ProviderUnavailable("y"))
        res = route(make_req(task="general", privacy_label=PrivacyLabel.PUBLIC))
        assert res.level == 3 and res.model == "gemini_cli"

    def test_configured_priority_order_is_honoured(self, monkeypatch, providers, privacy):
        enable_only(monkeypatch, "codex", "claude")
        res = route(make_req(task="general"))
        assert res.level == 1 and res.model == "codex_cli"
        assert providers["claude"].calls == []

    def test_disabled_provider_is_never_called(self, monkeypatch, providers, privacy):
        enable_only(monkeypatch, "codex")
        route(make_req(task="general"))
        assert providers["claude"].calls == [] and providers["gemini"].calls == []


# ------------------------------------------------------------- error mapping

class TestExhaustionMapping:
    def _all(self, providers, state, detail="x"):
        for name in ("claude", "codex", "gemini"):
            set_provider(providers, name, error=ProviderError(state, detail))

    def test_all_exhausted_defers_with_retry_hint(self, providers, privacy):
        self._all(providers, ProviderState.QUOTA_EXHAUSTED, "plan limit")
        with pytest.raises(CloudOSError) as ei:
            route(make_req(task="general", privacy_label=PrivacyLabel.PUBLIC))
        exc = ei.value
        assert exc.code is ErrorCode.QUOTA_EXHAUSTED
        assert exc.details["providers"] == ["claude", "codex", "gemini"]
        assert exc.details["retry_after_minutes"] == 60

    def test_all_auth_required_asks_for_login(self, providers, privacy):
        self._all(providers, ProviderState.AUTH_REQUIRED, "run login")
        with pytest.raises(CloudOSError) as ei:
            route(make_req(task="general", privacy_label=PrivacyLabel.PUBLIC))
        assert ei.value.code is ErrorCode.AUTH_REQUIRED
        assert ei.value.details["providers"] == ["claude", "codex", "gemini"]

    def test_quota_outranks_auth_so_the_task_is_deferred_not_blocked(self, providers, privacy):
        set_provider(
            providers, "claude", error=ProviderError(ProviderState.QUOTA_EXHAUSTED, "limit")
        )
        set_provider(
            providers, "codex", error=ProviderError(ProviderState.AUTH_REQUIRED, "login")
        )
        set_provider(providers, "gemini", error=ProviderUnavailable("gone"))
        with pytest.raises(CloudOSError) as ei:
            route(make_req(task="general", privacy_label=PrivacyLabel.PUBLIC))
        assert ei.value.code is ErrorCode.QUOTA_EXHAUSTED
        assert ei.value.details["providers"] == ["claude"]

    def test_all_unavailable_is_dependency_unavailable(self, providers, privacy):
        for name in ("claude", "codex", "gemini"):
            set_provider(providers, name, error=ProviderUnavailable(f"{name} missing"))
        with pytest.raises(CloudOSError) as ei:
            route(make_req(task="general", privacy_label=PrivacyLabel.PUBLIC))
        assert ei.value.code is ErrorCode.DEPENDENCY_UNAVAILABLE
        assert len(ei.value.details["errors"]) == 3

    def test_billing_risk_fails_closed_immediately(self, providers, privacy):
        set_provider(
            providers,
            "claude",
            error=ProviderError(ProviderState.BILLING_RISK, "apiKeyHelper configured"),
        )
        with pytest.raises(CloudOSError) as ei:
            route(make_req(task="general"))
        exc = ei.value
        assert exc.code is ErrorCode.BILLING_RISK
        assert exc.details["provider"] == "claude"
        # never falls through to another provider when money could move
        assert providers["codex"].calls == [] and providers["gemini"].calls == []

    def test_validation_error_on_non_string_prompt(self, providers, privacy):
        with pytest.raises(CloudOSError) as ei:
            route(RouteRequest(prompt=123, task="general"))  # type: ignore[arg-type]
        assert ei.value.code is ErrorCode.VALIDATION_ERROR
        assert providers["claude"].calls == []

    def test_unexpected_provider_bug_is_normalized_without_leaking_detail(
        self, providers, privacy
    ):
        set_provider(providers, "claude", error=RuntimeError("secret-bearing traceback text"))
        with pytest.raises(CloudOSError) as ei:
            route(make_req(task="general"))
        exc = ei.value
        assert exc.code is ErrorCode.INTERNAL_ERROR
        assert "RuntimeError" in exc.message
        assert "secret-bearing traceback text" not in exc.message


# --------------------------------------------------------------- model hints

class TestModelHint:
    def test_hint_pins_one_provider(self, providers, privacy):
        res = route(make_req(task="general", model_hint="codex"))
        assert res.model == "codex_cli"
        assert len(providers["codex"].calls) == 1
        assert providers["claude"].calls == [] and providers["gemini"].calls == []

    def test_unknown_hint_is_rejected_before_any_provider_call(self, providers, privacy):
        with pytest.raises(CloudOSError) as ei:
            route(make_req(task="general", model_hint="gpt-4o"))
        assert ei.value.code is ErrorCode.MODEL_NOT_ALLOWED
        assert ei.value.details["model_hint"] == "gpt-4o"
        assert all(p.calls == [] for p in providers.values())
        assert privacy.calls == []

    def test_hint_for_a_disabled_provider_is_rejected(self, monkeypatch, providers, privacy):
        enable_only(monkeypatch, "claude")
        with pytest.raises(CloudOSError) as ei:
            route(make_req(task="general", model_hint="codex"))
        assert ei.value.code is ErrorCode.MODEL_NOT_ALLOWED
        assert ei.value.details["model_hint"] == "codex"

    def test_hint_is_validated_before_the_l0_shortcut(self, providers):
        with pytest.raises(CloudOSError) as ei:
            route(make_req(task="echo", model_hint="gpt-4o"))
        assert ei.value.code is ErrorCode.MODEL_NOT_ALLOWED


# ----------------------------------------------------- zero-cost enforcement

class TestZeroCostEnforcement:
    def test_paid_level_disabled_even_if_env_allows(self, monkeypatch, providers, privacy):
        monkeypatch.setenv("ALLOW_PAID_AI", "true")  # deliberately ignored
        reset_settings_cache()
        with pytest.raises(CloudOSError) as ei:
            route(make_req(task="general", meta={"level": 3}))
        assert ei.value.code is ErrorCode.PAID_DISABLED
        assert all(p.calls == [] for p in providers.values())

    def test_force_paid_meta_disabled(self, providers, privacy):
        with pytest.raises(CloudOSError) as ei:
            route(make_req(task="general", meta={"force_paid": True}))
        assert ei.value.code is ErrorCode.PAID_DISABLED

    def test_allow_paid_meta_disabled(self, providers, privacy):
        with pytest.raises(CloudOSError) as ei:
            route(make_req(task="general", meta={"allow_paid": True}))
        assert ei.value.code is ErrorCode.PAID_DISABLED

    def test_paid_guard_ignores_a_non_numeric_level(self, providers, privacy):
        res = route(make_req(task="general", meta={"level": "high"}))
        assert res.ok and res.model == "claude_cli"

    def test_subscription_levels_below_three_still_route(self, providers, privacy):
        assert route(make_req(task="general", meta={"level": 2})).ok

    def test_paid_disabled_flag_is_constant(self):
        assert levels.PAID_AI_PERMANENTLY_DISABLED is True

    def test_cost_usd_always_zero(self, providers, privacy):
        assert route(make_req(task="echo")).cost_usd == 0.0
        assert route(make_req(task="general")).cost_usd == 0.0

    def test_router_package_contains_no_http_client(self):
        router_dir = Path(levels.__file__).resolve().parent
        offenders = []
        for py in sorted(router_dir.rglob("*.py")):
            text = py.read_text(encoding="utf-8")
            for lib in ("httpx", "requests", "aiohttp", "urllib.request", "http.client"):
                if re.search(rf"^\s*(?:import|from)\s+{re.escape(lib)}\b", text, re.M):
                    offenders.append(f"{py.name}: {lib}")
        assert offenders == [], f"metered-API-capable client in the router: {offenders}"


# -------------------------------------------------------------------- privacy

class TestPrivacyGate:
    def test_gate_sees_the_serialized_prompt_and_label(self, providers, privacy):
        route(make_req("the quick brown fox", task="general", privacy_label=PrivacyLabel.PUBLIC))
        assert len(privacy.calls) == 1
        content, label, frontmatter = privacy.calls[0]
        assert json.loads(content) == {"prompt": "the quick brown fox"}
        assert label is PrivacyLabel.PUBLIC
        assert frontmatter is None

    def test_internal_is_allowed_for_subscription_trusted_providers(self, providers, privacy):
        res = route(make_req(task="general", privacy_label=PrivacyLabel.INTERNAL))
        assert res.model == "claude_cli"
        _, label, frontmatter = privacy.calls[0]
        assert label is PrivacyLabel.INTERNAL
        assert frontmatter == {"external_ok": True}

    def test_google_cli_is_re_gated_strictly(self, providers, privacy):
        set_provider(providers, "claude", error=ProviderUnavailable("x"))
        set_provider(providers, "codex", error=ProviderUnavailable("y"))
        route(make_req(task="general", privacy_label=PrivacyLabel.INTERNAL))
        assert len(privacy.calls) == 2
        assert privacy.calls[0][2] == {"external_ok": True}  # baseline/trusted pass
        assert privacy.calls[1][2] is None  # strict unpaid-external pass

    def test_blocked_label_propagates_and_no_provider_is_called(self, providers, privacy):
        privacy.decision = GateDecision(allowed=False, reason="label blocks external AI")
        with pytest.raises(CloudOSError) as ei:
            route(make_req(task="general", privacy_label=PrivacyLabel.PERSONAL))
        exc = ei.value
        assert exc.code is ErrorCode.PRIVACY_BLOCKED
        assert "label blocks external AI" in exc.message
        assert exc.details["privacy_label"] == "personal"
        assert all(p.calls == [] for p in providers.values())

    def test_secret_detected_carries_safe_metadata_only(self, providers, privacy):
        privacy.decision = GateDecision(
            allowed=False,
            reason="secret found",
            hits=[SecretHit("aws_access_key", (0, 20), FAKE_AWS_KEY[:4] + "…")],
        )
        with pytest.raises(CloudOSError) as ei:
            route(make_req(f"prompt with {FAKE_AWS_KEY} inside", task="general"))
        exc = ei.value
        assert exc.code is ErrorCode.SECRET_DETECTED
        assert exc.details["patterns"] == ["aws_access_key"]
        assert exc.details["hit_count"] == 1
        blob = exc.message + json.dumps(exc.details)
        assert "AKIA" not in blob  # never the secret, nor the redacted sample
        assert all(p.calls == [] for p in providers.values())

    def test_gate_runs_before_any_provider_call(self, monkeypatch, providers, privacy):
        order: list[str] = []
        real_gate = privacy.gate

        def tracking_gate(content, label, frontmatter=None):
            order.append("gate")
            return real_gate(content, label, frontmatter)

        sys.modules["cloudos.privacy"].gate = tracking_gate

        def recording_generate(prompt, **kwargs):
            order.append("provider")
            return ProviderResponse(text="ok")

        monkeypatch.setattr(providers["claude"], "generate", recording_generate)
        route(make_req(task="general"))
        assert order == ["gate", "provider"]

    def test_gate_unavailable_fails_closed(self, monkeypatch, providers):
        # None in sys.modules ⇒ import raises — a missing/broken privacy module
        monkeypatch.setitem(sys.modules, "cloudos.privacy", None)
        with pytest.raises(CloudOSError) as ei:
            route(make_req(task="general"))
        assert ei.value.code is ErrorCode.DEPENDENCY_UNAVAILABLE
        assert all(p.calls == [] for p in providers.values())

    def test_a_strict_block_skips_that_provider_only(self, providers, privacy):
        """A gate that only google_cli fails must not kill the whole route."""
        set_provider(providers, "claude", error=ProviderUnavailable("x"))
        privacy.queue = [
            GateDecision(allowed=True, reason="ok"),  # baseline
            GateDecision(allowed=True, reason="ok"),  # (unused: codex is trusted)
        ]
        res = route(make_req(task="general"))
        assert res.model == "codex_cli"

    def test_strict_block_does_not_mask_a_deferrable_quota_state(self, providers, privacy):
        """claude out of quota + gemini label-blocked ⇒ defer, not fail closed."""
        set_provider(
            providers, "claude", error=ProviderError(ProviderState.QUOTA_EXHAUSTED, "limit")
        )
        set_provider(
            providers, "codex", error=ProviderError(ProviderState.QUOTA_EXHAUSTED, "limit")
        )
        privacy.queue = [
            GateDecision(allowed=True, reason="ok"),  # baseline/trusted
            GateDecision(allowed=False, reason="internal_requires_external_ok"),  # gemini
        ]
        with pytest.raises(CloudOSError) as ei:
            route(make_req(task="general", privacy_label=PrivacyLabel.INTERNAL))
        assert ei.value.code is ErrorCode.QUOTA_EXHAUSTED
        assert ei.value.details["providers"] == ["claude", "codex"]
        assert providers["gemini"].calls == []

    def test_strict_block_is_reported_when_it_is_the_only_reason(
        self, monkeypatch, providers, privacy
    ):
        enable_only(monkeypatch, "gemini")
        privacy.queue = [
            GateDecision(allowed=True, reason="ok"),  # baseline/trusted
            GateDecision(allowed=False, reason="internal_requires_external_ok"),  # gemini
        ]
        with pytest.raises(CloudOSError) as ei:
            route(make_req(task="general", privacy_label=PrivacyLabel.INTERNAL))
        assert ei.value.code is ErrorCode.PRIVACY_BLOCKED
        assert "internal_requires_external_ok" in ei.value.message
        assert providers["gemini"].calls == []

    def test_a_secret_hit_at_the_strict_gate_still_fails_closed(
        self, monkeypatch, providers, privacy
    ):
        set_provider(providers, "claude", error=ProviderUnavailable("x"))
        set_provider(providers, "codex", error=ProviderUnavailable("y"))
        privacy.queue = [
            GateDecision(allowed=True, reason="ok"),
            GateDecision(
                allowed=False,
                reason="secret_detected",
                hits=[SecretHit("github_token", (0, 40), "ghp_…")],
            ),
        ]
        with pytest.raises(CloudOSError) as ei:
            route(make_req(task="general"))
        assert ei.value.code is ErrorCode.SECRET_DETECTED
        assert providers["gemini"].calls == []


class TestPrivacyGateRealModule:
    """The same gating, against the real cloudos.privacy rules + privacy.yaml."""

    def test_internal_reaches_a_subscription_trusted_provider(self, providers):
        res = route(make_req("plan my week", task="general", privacy_label=PrivacyLabel.INTERNAL))
        assert res.model == "claude_cli"

    def test_personal_is_blocked_outright(self, providers):
        with pytest.raises(CloudOSError) as ei:
            route(make_req("notes", task="general", privacy_label=PrivacyLabel.PERSONAL))
        assert ei.value.code is ErrorCode.PRIVACY_BLOCKED
        assert "personal" in ei.value.message
        assert all(p.calls == [] for p in providers.values())

    def test_real_secret_scan_blocks_the_prompt(self, providers):
        with pytest.raises(CloudOSError) as ei:
            route(make_req(f"deploy with {FAKE_AWS_KEY}", task="general"))
        exc = ei.value
        assert exc.code is ErrorCode.SECRET_DETECTED
        assert "aws_access_key" in exc.details["patterns"]
        assert FAKE_AWS_KEY not in exc.message + json.dumps(exc.details)

    def test_internal_does_not_reach_google_cli(self, monkeypatch, providers):
        enable_only(monkeypatch, "gemini")
        with pytest.raises(CloudOSError) as ei:
            route(make_req("plan my week", task="general", privacy_label=PrivacyLabel.INTERNAL))
        assert ei.value.code is ErrorCode.PRIVACY_BLOCKED
        assert providers["gemini"].calls == []

    def test_public_does_reach_google_cli(self, monkeypatch, providers):
        enable_only(monkeypatch, "gemini")
        res = route(make_req("public docs question", task="general",
                             privacy_label=PrivacyLabel.PUBLIC))
        assert res.model == "gemini_cli"


# ---------------------------------------------------------------- probe_all

class TestProbeAll:
    def test_returns_one_status_per_enabled_provider_in_order(self, providers):
        statuses = probe_names(probe_all_statuses())
        assert statuses == ["claude", "codex", "gemini"]

    def test_only_enabled_providers_are_probed(self, monkeypatch, providers):
        enable_only(monkeypatch, "codex")
        statuses = probe_all_statuses()
        assert [s.provider for s in statuses] == ["codex"]
        assert providers["claude"].probes == 0

    def test_probe_is_passive_and_never_generates(self, providers):
        probe_all_statuses()
        assert all(p.probes == 1 for p in providers.values())
        assert all(p.calls == [] for p in providers.values())

    def test_a_failing_probe_is_reported_as_unavailable_safely(self, providers):
        set_provider(providers, "claude", status=RuntimeError("token abc123 leaked"))
        statuses = {s.provider: s for s in probe_all_statuses()}
        claude = statuses["claude"]
        assert claude.state is ProviderState.UNAVAILABLE
        assert "RuntimeError" in claude.detail
        assert "abc123" not in claude.detail

    def test_reports_the_state_each_provider_returns(self, providers):
        set_provider(
            providers,
            "codex",
            status=ProviderStatus("codex", ProviderState.BILLING_RISK, detail="api key mode"),
        )
        statuses = {s.provider: s for s in probe_all_statuses()}
        assert statuses["codex"].state is ProviderState.BILLING_RISK
        assert statuses["claude"].state is ProviderState.AVAILABLE_SUBSCRIPTION


def probe_all_statuses():
    from cloudos.router import probe_all

    return probe_all()


def probe_names(statuses):
    return [s.provider for s in statuses]


# ----------------------------------------------------------------- agent_runs

class TestRunRecording:
    def test_success_writes_one_compact_row(self, providers, privacy, fake_db):
        prompt = "please summarize my extremely private notes"
        res = route(make_req(prompt, task="general"))
        assert res.ok
        rows = [(sql, params) for sql, params in fake_db if "agent_runs" in sql]
        assert len(rows) == 1
        sql, params = rows[0]
        assert "'router'" in sql
        assert sha256_hash(prompt) in params  # input_hash
        assert sha256_hash("claude-answer") in params  # output_hash
        assert "succeeded" in params
        assert "claude_cli" in params
        joined = " ".join(str(p) for p in params)
        assert prompt not in joined  # NEVER the prompt text
        assert "claude-answer" not in joined  # NEVER the output text
        assert "claude_sub" in joined  # auth_mode is safe metadata

    def test_l0_row_records_level_zero(self, privacy, fake_db):
        route(make_req("hi", task="echo"))
        rows = [params for sql, params in fake_db if "agent_runs" in sql]
        assert len(rows) == 1
        assert "deterministic:echo" in rows[0]
        assert 0 in rows[0]

    def test_failure_writes_a_row_with_the_error_code(self, monkeypatch, privacy, fake_db):
        enable_only(monkeypatch, "claude")
        # the enable_only above resets settings; DATABASE_URL stays set by fake_db
        monkeypatch.setenv("DATABASE_URL", "postgresql://stub/stub")
        reset_settings_cache()
        with pytest.raises(CloudOSError):
            route(make_req(task="general", model_hint="gpt-4o"))
        rows = [params for sql, params in fake_db if "agent_runs" in sql]
        assert len(rows) == 1
        assert "failed" in rows[0]
        assert ErrorCode.MODEL_NOT_ALLOWED.value in rows[0]

    def test_provider_status_is_upserted_on_success(self, providers, privacy, fake_db):
        route(make_req(task="general"))
        upserts = [params for sql, params in fake_db if "INSERT INTO provider_status" in sql]
        assert len(upserts) == 1
        assert "claude" in upserts[0]
        assert ProviderState.AVAILABLE_SUBSCRIPTION.value in upserts[0]

    def test_provider_status_records_the_failure_reason_not_the_prompt(
        self, providers, privacy, fake_db
    ):
        set_provider(
            providers, "claude", error=ProviderError(ProviderState.QUOTA_EXHAUSTED, "plan limit")
        )
        route(make_req("sensitive prompt text", task="general"))
        upserts = [params for sql, params in fake_db if "INSERT INTO provider_status" in sql]
        claude_rows = [p for p in upserts if p[0] == "claude"]
        assert claude_rows and "plan limit" in claude_rows[0]
        assert "sensitive prompt text" not in " ".join(str(p) for p in claude_rows[0])

    def test_run_write_failure_never_fails_the_route(self, providers, privacy, broken_db):
        res = route(make_req("hi", task="echo"))
        assert res.ok and res.level == 0

    def test_provider_failure_to_record_never_fails_the_route(
        self, providers, privacy, broken_db
    ):
        res = route(make_req(task="general"))
        assert res.ok and res.model == "claude_cli"

    def test_no_db_configured_writes_nothing_and_works(self, providers, privacy):
        res = route(make_req("hi", task="echo"))
        assert res.ok


# --------------------------------------------------------------- quota usage

class TestQuotaAccounting:
    def test_usage_is_counted_per_successful_call(self, providers, privacy):
        route(make_req(task="general"))
        route(make_req(task="general"))
        assert quota.get_store().get_usage("claude", quota.today()).requests == 2
        assert quota.get_store().get_usage("codex", quota.today()).requests == 0

    def test_a_failed_provider_is_not_counted(self, providers, privacy):
        set_provider(
            providers, "claude", error=ProviderError(ProviderState.QUOTA_EXHAUSTED, "limit")
        )
        route(make_req(task="general"))
        usage = quota.get_store()
        assert usage.get_usage("claude", quota.today()).requests == 0
        assert usage.get_usage("codex", quota.today()).requests == 1

    def test_counting_is_observability_only_and_never_blocks_a_route(
        self, providers, privacy, broken_db
    ):
        """Plan allowances live with the subscription, not in this counter —
        a dead counter must not stop work the subscription still allows."""
        res = route(make_req(task="general"))
        assert res.ok and res.model == "claude_cli"


class TestQuotaStoreLocalMode:
    def test_accumulates_per_provider_and_day(self):
        store = quota.QuotaStore()
        day = quota.today()
        assert store.get_usage("claude", day).requests == 0
        store.increment("claude", day, requests=2, units=30)
        store.increment("claude", day, requests=1)
        store.increment("gemini", day, requests=5)
        usage = store.get_usage("claude", day)
        assert (usage.requests, usage.units) == (3, 30)
        assert store.get_usage("gemini", day).requests == 5

    def test_days_are_accounted_separately(self):
        from datetime import timedelta

        store = quota.QuotaStore()
        today = quota.today()
        store.increment("claude", today, requests=2)
        store.increment("claude", today - timedelta(days=1), requests=7)
        assert store.get_usage("claude", today).requests == 2

    def test_db_mode_upsert_and_read(self, fake_db):
        store = quota.QuotaStore()
        day = quota.today()
        store.increment("gemini", day, requests=1)
        assert store.get_usage("gemini", day).requests == 0  # stub fetchone → None row
        sqls = [sql for sql, _ in fake_db]
        assert any("INSERT INTO quota_usage" in s and "ON CONFLICT" in s for s in sqls)
        assert any("SELECT requests, units FROM quota_usage" in s for s in sqls)

    def test_db_mode_failure_raises_quota_store_error(self, broken_db):
        store = quota.QuotaStore()
        with pytest.raises(quota.QuotaStoreError):
            store.get_usage("gemini", quota.today())
        with pytest.raises(quota.QuotaStoreError):
            store.increment("gemini", quota.today())

    def test_singleton_is_process_wide_until_reset(self):
        quota.get_store().increment("claude", quota.today(), requests=4)
        assert quota.get_store().get_usage("claude", quota.today()).requests == 4
        quota.reset_store()
        assert quota.get_store().get_usage("claude", quota.today()).requests == 0


# ------------------------------------------------- provider env scrubbing

class TestScrubbedEnv:
    def test_every_metered_credential_var_is_removed(self, monkeypatch):
        for var in base.SCRUBBED_ENV_VARS:
            monkeypatch.setenv(var, "would-bill-money")
        env = base.scrubbed_env()
        assert [v for v in base.SCRUBBED_ENV_VARS if v in env] == []

    def test_subscription_oauth_token_is_deliberately_kept(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "setup-token-value")
        assert base.scrubbed_env()["CLAUDE_CODE_OAUTH_TOKEN"] == "setup-token-value"

    def test_extra_env_is_merged(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "metered")
        env = base.scrubbed_env({"GOOGLE_GENAI_USE_GCA": "true"})
        assert env["GOOGLE_GENAI_USE_GCA"] == "true"
        assert "GEMINI_API_KEY" not in env

    def test_the_metered_list_covers_every_known_billing_switch(self):
        for var in (
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "CLAUDE_CODE_USE_BEDROCK",
            "CLAUDE_CODE_USE_VERTEX",
            "OPENAI_API_KEY",
            "GEMINI_API_KEY",
            "GOOGLE_API_KEY",
            "GOOGLE_APPLICATION_CREDENTIALS",
            "GOOGLE_GENAI_USE_VERTEXAI",
        ):
            assert var in base.SCRUBBED_ENV_VARS


# --------------------------------------------- provider state classification

class TestClaudeProviderState:
    def test_missing_binary_is_unavailable(self, monkeypatch, cli_home):
        monkeypatch.setattr(base, "which", lambda binary: None)
        status = claude_cli.probe()
        assert status.state is ProviderState.UNAVAILABLE

    def test_api_key_helper_is_a_billing_risk(self, cli_home):
        write_json(cli_home / ".claude" / "settings.json", {"apiKeyHelper": "/bin/get-key"})
        status = claude_cli.probe()
        assert status.state is ProviderState.BILLING_RISK
        assert "apiKeyHelper" in status.detail

    def test_unreadable_settings_file_is_a_billing_risk(self, cli_home):
        path = cli_home / ".claude" / "settings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        assert claude_cli.probe().state is ProviderState.BILLING_RISK

    def test_anthropic_profile_is_a_billing_risk(self, cli_home):
        profile = claude_cli._anthropic_profile_dir() / "active_config"
        profile.parent.mkdir(parents=True, exist_ok=True)
        profile.write_text("default", encoding="utf-8")
        status = claude_cli.probe()
        assert status.state is ProviderState.BILLING_RISK

    def test_oauth_token_is_the_headless_subscription_mode(self, monkeypatch, cli_home):
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat-stub")
        status = claude_cli.probe()
        assert status.state is ProviderState.AVAILABLE_SUBSCRIPTION
        assert status.auth_mode == "subscription_oauth_token"

    def test_no_credentials_requires_login(self, cli_home):
        status = claude_cli.probe()
        assert status.state is ProviderState.AUTH_REQUIRED
        assert "login" in status.detail

    def test_non_claude_ai_credentials_are_a_billing_risk(self, cli_home):
        write_json(cli_home / ".claude" / ".credentials.json", {"consoleOauth": {"x": 1}})
        status = claude_cli.probe()
        assert status.state is ProviderState.BILLING_RISK

    def test_claude_ai_oauth_is_the_subscription_mode(self, cli_home):
        write_json(cli_home / ".claude" / ".credentials.json", {"claudeAiOauth": {"x": 1}})
        status = claude_cli.probe()
        assert status.state is ProviderState.AVAILABLE_SUBSCRIPTION
        assert status.auth_mode == "subscription_oauth"

    def test_config_dir_override_is_honoured(self, monkeypatch, cli_home, tmp_path):
        other = tmp_path / "elsewhere"
        write_json(other / ".credentials.json", {"claudeAiOauth": {"x": 1}})
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(other))
        assert claude_cli.probe().state is ProviderState.AVAILABLE_SUBSCRIPTION


class TestClaudeGenerate:
    @pytest.fixture(autouse=True)
    def logged_in(self, cli_home):
        write_json(cli_home / ".claude" / ".credentials.json", {"claudeAiOauth": {"x": 1}})
        return cli_home

    def test_parses_json_output_and_usage(self, monkeypatch):
        payload = {"result": "the answer", "usage": {"input_tokens": 11, "output_tokens": 22}}
        runner = install_cli(monkeypatch, ok(json.dumps(payload)))
        res = claude_cli.generate("q", max_tokens=32, timeout=9)
        assert res.text == "the answer"
        assert (res.prompt_tokens, res.completion_tokens) == (11, 22)
        assert res.auth_mode == "subscription_oauth"
        argv, kwargs = runner.calls[0]
        assert argv[1:] == ["-p", "--output-format", "json"]
        assert kwargs["stdin_text"] == "q"
        assert kwargs["timeout"] == 9

    def test_model_flag_added_when_configured(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_CLI_MODEL", "some-model")
        reset_settings_cache()
        runner = install_cli(monkeypatch, ok(json.dumps({"result": "x"})))
        claude_cli.generate("q")
        assert runner.calls[0][0][-2:] == ["--model", "some-model"]

    def test_plain_stdout_is_accepted(self, monkeypatch):
        install_cli(monkeypatch, ok("just text"))
        assert claude_cli.generate("q").text == "just text"

    def test_empty_output_is_unavailable(self, monkeypatch):
        install_cli(monkeypatch, ok("   "))
        with pytest.raises(ProviderUnavailable):
            claude_cli.generate("q")

    def test_is_error_payload_is_classified(self, monkeypatch):
        install_cli(monkeypatch, ok(json.dumps({"is_error": True, "result": "usage limit reached"})))
        with pytest.raises(ProviderError) as ei:
            claude_cli.generate("q")
        assert ei.value.state is ProviderState.QUOTA_EXHAUSTED

    def test_plan_limit_maps_to_quota_exhausted(self, monkeypatch):
        install_cli(monkeypatch, fail(stderr="Claude usage limit reached; resets 5pm"))
        with pytest.raises(ProviderError) as ei:
            claude_cli.generate("q")
        assert ei.value.state is ProviderState.QUOTA_EXHAUSTED

    def test_login_message_maps_to_auth_required(self, monkeypatch):
        install_cli(monkeypatch, fail(stderr="Please run /login to continue"))
        with pytest.raises(ProviderError) as ei:
            claude_cli.generate("q")
        assert ei.value.state is ProviderState.AUTH_REQUIRED

    def test_timeout_is_unavailable_not_an_error_state(self, monkeypatch):
        install_cli(monkeypatch, fail(rc=-1, stderr="", timed_out=True))
        with pytest.raises(ProviderUnavailable) as ei:
            claude_cli.generate("q")
        assert "timed out" in str(ei.value)

    def test_unknown_exit_code_is_unavailable(self, monkeypatch):
        install_cli(monkeypatch, fail(rc=7, stderr="segfault"))
        with pytest.raises(ProviderUnavailable):
            claude_cli.generate("q")

    def test_billing_risk_refuses_to_run_the_cli(self, monkeypatch, cli_home):
        write_json(cli_home / ".claude" / "settings.json", {"apiKeyHelper": "/bin/get-key"})
        runner = install_cli(monkeypatch, ok("never"))
        with pytest.raises(ProviderError) as ei:
            claude_cli.generate("q")
        assert ei.value.state is ProviderState.BILLING_RISK
        assert runner.calls == []  # not one subprocess when money could move


class TestCodexProviderState:
    def test_missing_binary_is_unavailable(self, monkeypatch, cli_home):
        monkeypatch.setattr(base, "which", lambda binary: None)
        assert codex_cli.probe().state is ProviderState.UNAVAILABLE

    def test_api_key_in_auth_json_is_a_billing_risk_without_running_the_cli(
        self, monkeypatch, cli_home
    ):
        write_json(cli_home / ".codex" / "auth.json", {"OPENAI_API_KEY": "sk-stub"})
        runner = install_cli(monkeypatch, ok("never"))
        status = codex_cli.probe()
        assert status.state is ProviderState.BILLING_RISK
        assert runner.calls == []

    def test_unreadable_auth_json_is_a_billing_risk(self, cli_home):
        path = cli_home / ".codex" / "auth.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{broken", encoding="utf-8")
        assert codex_cli.probe().state is ProviderState.BILLING_RISK

    def test_chatgpt_sign_in_is_the_subscription_mode(self, monkeypatch, cli_home):
        install_cli(monkeypatch, ok("Logged in using ChatGPT"))
        status = codex_cli.probe()
        assert status.state is ProviderState.AVAILABLE_SUBSCRIPTION
        assert status.auth_mode == "chatgpt_subscription"

    def test_login_status_reporting_api_key_is_a_billing_risk(self, monkeypatch, cli_home):
        install_cli(monkeypatch, ok("Authenticated with API key"))
        assert codex_cli.probe().state is ProviderState.BILLING_RISK

    def test_not_logged_in_requires_auth(self, monkeypatch, cli_home):
        install_cli(monkeypatch, fail(stderr="not logged in"))
        status = codex_cli.probe()
        assert status.state is ProviderState.AUTH_REQUIRED
        assert "codex login" in status.detail


class TestCodexGenerate:
    @pytest.fixture(autouse=True)
    def logged_in(self, cli_home):
        return cli_home

    def test_runs_exec_with_the_prompt(self, monkeypatch):
        runner = install_cli(monkeypatch, ok("Logged in using ChatGPT"), ok("codex says hi"))
        res = codex_cli.generate("do the thing", timeout=12)
        assert res.text == "codex says hi"
        assert res.auth_mode == "chatgpt_subscription"
        argv, kwargs = runner.calls[1]
        assert argv[1:] == ["exec", "--skip-git-repo-check", "do the thing"]
        assert kwargs["timeout"] == 12

    def test_web_search_adds_the_search_flag_before_exec(self, monkeypatch):
        runner = install_cli(monkeypatch, ok("Logged in using ChatGPT"), ok("answer"))
        codex_cli.generate("q", web_search=True)
        argv, _ = runner.calls[1]
        assert argv[1] == "--search"
        assert argv[2] == "exec"

    def test_quota_marker_maps_to_quota_exhausted(self, monkeypatch):
        runner = install_cli(monkeypatch, ok("Logged in using ChatGPT"))
        runner.results = [ok("Logged in using ChatGPT"), fail(stderr="You reached your limit")]
        with pytest.raises(ProviderError) as ei:
            codex_cli.generate("q")
        assert ei.value.state is ProviderState.QUOTA_EXHAUSTED

    def test_unauthorized_maps_to_auth_required(self, monkeypatch):
        runner = install_cli(monkeypatch)
        runner.results = [ok("Logged in using ChatGPT"), fail(stderr="401 unauthorized")]
        with pytest.raises(ProviderError) as ei:
            codex_cli.generate("q")
        assert ei.value.state is ProviderState.AUTH_REQUIRED

    def test_empty_output_is_unavailable(self, monkeypatch):
        runner = install_cli(monkeypatch)
        runner.results = [ok("Logged in using ChatGPT"), ok("  ")]
        with pytest.raises(ProviderUnavailable):
            codex_cli.generate("q")


class TestGoogleProviderState:
    def test_missing_binary_is_unavailable(self, monkeypatch, cli_home):
        monkeypatch.setattr(base, "which", lambda binary: None)
        assert google_cli.probe().state is ProviderState.UNAVAILABLE

    def test_no_oauth_creds_requires_auth(self, cli_home):
        status = google_cli.probe()
        assert status.state is ProviderState.AUTH_REQUIRED
        assert "Google" in status.detail

    def test_cached_oauth_creds_are_the_subscription_mode(self, cli_home):
        write_json(cli_home / ".gemini" / "oauth_creds.json", {"token": "stub"})
        status = google_cli.probe()
        assert status.state is ProviderState.AVAILABLE_SUBSCRIPTION
        assert status.auth_mode == "google_oauth"


class TestGoogleGenerate:
    @pytest.fixture(autouse=True)
    def logged_in(self, cli_home):
        write_json(cli_home / ".gemini" / "oauth_creds.json", {"token": "stub"})
        return cli_home

    def test_forces_the_google_account_selector(self, monkeypatch):
        runner = install_cli(monkeypatch, ok("gemini says hi"))
        res = google_cli.generate("q")
        assert res.text == "gemini says hi"
        argv, kwargs = runner.calls[0]
        assert argv[1:] == ["-p", "q"]
        assert kwargs["extra_env"] == {"GOOGLE_GENAI_USE_GCA": "true"}

    def test_a_demand_for_a_metered_key_is_unavailable_never_api_key_auth(self, monkeypatch):
        install_cli(monkeypatch, fail(stderr="Please set an auth method or GEMINI_API_KEY"))
        with pytest.raises(ProviderUnavailable) as ei:
            google_cli.generate("q")
        assert "metered" in str(ei.value)

    def test_quota_marker_maps_to_quota_exhausted(self, monkeypatch):
        install_cli(monkeypatch, fail(stderr="429 RESOURCE_EXHAUSTED"))
        with pytest.raises(ProviderError) as ei:
            google_cli.generate("q")
        assert ei.value.state is ProviderState.QUOTA_EXHAUSTED

    def test_auth_marker_maps_to_auth_required(self, monkeypatch):
        install_cli(monkeypatch, fail(stderr="oauth credentials rejected"))
        with pytest.raises(ProviderError) as ei:
            google_cli.generate("q")
        assert ei.value.state is ProviderState.AUTH_REQUIRED

    def test_empty_output_is_unavailable(self, monkeypatch):
        install_cli(monkeypatch, ok(""))
        with pytest.raises(ProviderUnavailable):
            google_cli.generate("q")

    def test_auth_required_probe_refuses_to_run_the_cli(self, monkeypatch, cli_home):
        (cli_home / ".gemini" / "oauth_creds.json").unlink()
        runner = install_cli(monkeypatch, ok("never"))
        with pytest.raises(ProviderError) as ei:
            google_cli.generate("q")
        assert ei.value.state is ProviderState.AUTH_REQUIRED
        assert runner.calls == []
