"""Router tests (Agent 8) — no real network, no real DB, no real privacy module.

* Providers: httpx.MockTransport injected via the module-level ``_transport`` hooks.
* Privacy: ``cloudos.privacy`` is shadowed in sys.modules with a stub — these
  tests never depend on Agent 10's internals (contracts §16).
* DB: ``cloudos.db`` is shadowed with a stub when a DB path is under test;
  otherwise DATABASE_URL is empty (local/no-db quota mode).
"""
from __future__ import annotations

import json
import sys
import types
from contextlib import contextmanager

import httpx
import pytest

from cloudos.config import REPO_ROOT, reset_settings_cache
from cloudos.contracts import (
    CloudOSError,
    ErrorCode,
    GateDecision,
    PrivacyLabel,
    RouteRequest,
    SecretHit,
    sha256_hash,
)
from cloudos.router import levels, quota, route
from cloudos.router.providers import gemini as gemini_provider
from cloudos.router.providers import workers_ai as workers_ai_provider

ALLOWLIST_PATH = str(REPO_ROOT / "config" / "free_model_allowlist.json")
with open(ALLOWLIST_PATH, encoding="utf-8") as _fh:
    _ALLOW = json.load(_fh)
WA_FIRST = _ALLOW["workers_ai"][0]
WA_SECOND = _ALLOW["workers_ai"][1]
GEM_FIRST = _ALLOW["gemini"][0]
GEM_SECOND = _ALLOW["gemini"][1]


# ------------------------------------------------------------------- fixtures

@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    """Blank provider/db config, fresh settings cache + quota store, no transports.

    Vars are set to "" (not deleted) so a developer .env can never leak in via
    load_dotenv(override=False).
    """
    for var in (
        "CF_ACCOUNT_ID",
        "CF_WORKERS_AI_TOKEN",
        "GEMINI_API_KEY",
        "DATABASE_URL",
        "ALLOW_PAID_AI",
        "WORKERS_AI_DAILY_BUDGET",
        "GEMINI_DAILY_REQUEST_BUDGET",
        "NOTIFY_WEBHOOK_URL",
    ):
        monkeypatch.setenv(var, "")
    monkeypatch.setenv("FREE_MODEL_ALLOWLIST_PATH", ALLOWLIST_PATH)
    reset_settings_cache()
    quota.reset_store()
    monkeypatch.setattr(workers_ai_provider, "_transport", None)
    monkeypatch.setattr(gemini_provider, "_transport", None)
    yield
    reset_settings_cache()
    quota.reset_store()


class PrivacyStub:
    def __init__(self):
        self.calls: list[tuple[str, PrivacyLabel]] = []
        self.decision = GateDecision(allowed=True, reason="ok")

    def gate(self, content, label):
        self.calls.append((content, label))
        return self.decision


@pytest.fixture
def privacy(monkeypatch):
    """Shadow cloudos.privacy with a stub gate (never Agent 10's real module)."""
    stub = PrivacyStub()
    mod = types.ModuleType("cloudos.privacy")
    mod.gate = stub.gate
    monkeypatch.setitem(sys.modules, "cloudos.privacy", mod)
    return stub


class FakeCursor:
    def __init__(self, executed):
        self._executed = executed

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        self._executed.append((sql, params))

    def fetchone(self):
        return None  # no existing quota_usage row


class FakeConn:
    def __init__(self, executed):
        self._executed = executed

    def cursor(self):
        return FakeCursor(self._executed)

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

    @contextmanager
    def get_conn():
        yield FakeConn(executed)

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


def configure_cf(monkeypatch):
    monkeypatch.setenv("CF_ACCOUNT_ID", "acct-123")
    monkeypatch.setenv("CF_WORKERS_AI_TOKEN", "cf-token")
    reset_settings_cache()


def configure_gemini(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gm-key")
    reset_settings_cache()


def cf_transport(calls, status=200, text="cf-answer"):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if status != 200:
            return httpx.Response(status, json={"success": False, "errors": [{"code": status}]})
        return httpx.Response(
            200,
            json={
                "success": True,
                "result": {
                    "response": text,
                    "usage": {"prompt_tokens": 5, "completion_tokens": 7},
                },
            },
        )

    return httpx.MockTransport(handler)


def gemini_transport(calls, status=200, text="gemini-answer"):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if status != 200:
            return httpx.Response(status, json={"error": {"code": status}})
        return httpx.Response(
            200,
            json={
                "candidates": [{"content": {"parts": [{"text": text}]}}],
                "usageMetadata": {"promptTokenCount": 4, "candidatesTokenCount": 6},
            },
        )

    return httpx.MockTransport(handler)


def install_cf(monkeypatch, **kw):
    calls: list[httpx.Request] = []
    monkeypatch.setattr(workers_ai_provider, "_transport", cf_transport(calls, **kw))
    return calls


def install_gemini(monkeypatch, **kw):
    calls: list[httpx.Request] = []
    monkeypatch.setattr(gemini_provider, "_transport", gemini_transport(calls, **kw))
    return calls


# ------------------------------------------------------------------------- L0

class TestL0Deterministic:
    def test_echo(self):
        res = route(make_req("hello world", task="echo"))
        assert res.ok is True
        assert res.level == 0
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

    def test_l0_needs_no_provider_config_and_no_network(self):
        # no transports installed — any HTTP attempt would crash
        res = route(make_req("x", task="echo"))
        assert res.ok and res.level == 0

    def test_unknown_task_escalates_and_unconfigured_fails(self):
        with pytest.raises(CloudOSError) as ei:
            route(make_req(task="general"))
        assert ei.value.code is ErrorCode.DEPENDENCY_UNAVAILABLE
        assert "no free AI provider configured" in ei.value.message

    def test_registry_is_extensible(self):
        levels.register_deterministic_task("shout", lambda req: req.prompt.upper())
        try:
            res = route(make_req("hi", task="shout"))
            assert res.level == 0 and res.text == "HI"
        finally:
            del levels.DETERMINISTIC_TASKS["shout"]


# ---------------------------------------------------------------- level order

class TestLevelOrder:
    def test_l1_preferred_over_l2(self, monkeypatch, privacy):
        configure_cf(monkeypatch)
        configure_gemini(monkeypatch)
        cf_calls = install_cf(monkeypatch)
        gm_calls = install_gemini(monkeypatch)
        res = route(make_req(task="general"))
        assert res.ok and res.level == 1
        assert res.model == WA_FIRST
        assert res.text == "cf-answer"
        assert (res.prompt_tokens, res.completion_tokens) == (5, 7)
        assert len(cf_calls) == 1
        assert gm_calls == []
        assert privacy.calls == []  # gate guards L2 only

    def test_l1_error_escalates_to_l2(self, monkeypatch, privacy):
        configure_cf(monkeypatch)
        configure_gemini(monkeypatch)
        cf_calls = install_cf(monkeypatch, status=500)
        gm_calls = install_gemini(monkeypatch)
        res = route(make_req(task="general"))
        assert res.level == 2
        assert res.model == GEM_FIRST
        assert res.text == "gemini-answer"
        assert (res.prompt_tokens, res.completion_tokens) == (4, 6)
        assert len(cf_calls) == 1 and len(gm_calls) == 1
        assert len(privacy.calls) == 1

    def test_unconfigured_l1_is_skipped(self, monkeypatch, privacy):
        configure_gemini(monkeypatch)  # no CF creds
        gm_calls = install_gemini(monkeypatch)
        res = route(make_req(task="general"))
        assert res.level == 2
        assert len(gm_calls) == 1

    def test_unconfigured_l2_is_skipped(self, monkeypatch, privacy):
        configure_cf(monkeypatch)  # no Gemini key
        cf_calls = install_cf(monkeypatch)
        res = route(make_req(task="general"))
        assert res.level == 1
        assert len(cf_calls) == 1
        assert privacy.calls == []

    def test_all_providers_error_dependency_unavailable(self, monkeypatch, privacy):
        configure_cf(monkeypatch)
        configure_gemini(monkeypatch)
        install_cf(monkeypatch, status=503)
        install_gemini(monkeypatch, status=500)
        with pytest.raises(CloudOSError) as ei:
            route(make_req(task="general"))
        assert ei.value.code is ErrorCode.DEPENDENCY_UNAVAILABLE
        assert "failed" in ei.value.message

    def test_l1_request_shape_and_model_hint(self, monkeypatch):
        configure_cf(monkeypatch)
        cf_calls = install_cf(monkeypatch)
        res = route(make_req("ping", task="general", model_hint=WA_SECOND, max_tokens=64))
        assert res.model == WA_SECOND
        request = cf_calls[0]
        assert f"/accounts/acct-123/ai/run/{WA_SECOND}" in str(request.url)
        assert request.headers["authorization"] == "Bearer cf-token"
        body = json.loads(request.content.decode("utf-8"))
        assert body == {
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 64,
        }

    def test_l2_model_hint_selects_gemini_model(self, monkeypatch, privacy):
        configure_gemini(monkeypatch)
        gm_calls = install_gemini(monkeypatch)
        res = route(make_req(task="general", model_hint=GEM_SECOND))
        assert res.level == 2 and res.model == GEM_SECOND
        assert f"models/{GEM_SECOND}:generateContent" in str(gm_calls[0].url)


# ----------------------------------------------------- zero-cost enforcement

class TestZeroCostEnforcement:
    def test_paid_level_disabled_even_if_env_allows(self, monkeypatch):
        monkeypatch.setenv("ALLOW_PAID_AI", "true")  # deliberately ignored
        reset_settings_cache()
        configure_cf(monkeypatch)
        cf_calls = install_cf(monkeypatch)
        with pytest.raises(CloudOSError) as ei:
            route(make_req(task="general", meta={"level": 3}))
        assert ei.value.code is ErrorCode.PAID_DISABLED
        assert cf_calls == []

    def test_force_paid_meta_disabled(self):
        with pytest.raises(CloudOSError) as ei:
            route(make_req(task="general", meta={"force_paid": True}))
        assert ei.value.code is ErrorCode.PAID_DISABLED

    def test_model_not_allowed_on_bad_hint_no_network(self, monkeypatch):
        configure_cf(monkeypatch)
        configure_gemini(monkeypatch)
        cf_calls = install_cf(monkeypatch)
        gm_calls = install_gemini(monkeypatch)
        with pytest.raises(CloudOSError) as ei:
            route(make_req(task="general", model_hint="gpt-4o"))
        assert ei.value.code is ErrorCode.MODEL_NOT_ALLOWED
        assert ei.value.details["model_hint"] == "gpt-4o"
        assert cf_calls == [] and gm_calls == []

    def test_paid_disabled_flag_is_constant(self):
        assert levels.PAID_AI_PERMANENTLY_DISABLED is True

    def test_cost_usd_always_zero(self, monkeypatch, privacy):
        # L0
        assert route(make_req(task="echo")).cost_usd == 0.0
        # L1
        configure_cf(monkeypatch)
        install_cf(monkeypatch)
        assert route(make_req(task="general")).cost_usd == 0.0
        # L2
        monkeypatch.setenv("CF_ACCOUNT_ID", "")
        monkeypatch.setenv("CF_WORKERS_AI_TOKEN", "")
        configure_gemini(monkeypatch)
        install_gemini(monkeypatch)
        assert route(make_req(task="general")).cost_usd == 0.0


# -------------------------------------------------------------------- budgets

class TestBudgetsFailClosed:
    def test_workers_ai_budget_exhausted_no_call_made(self, monkeypatch):
        configure_cf(monkeypatch)
        monkeypatch.setenv("WORKERS_AI_DAILY_BUDGET", "10")
        reset_settings_cache()
        quota.get_store().increment("workers_ai", quota.today(), requests=10)
        cf_calls = install_cf(monkeypatch)
        with pytest.raises(CloudOSError) as ei:
            route(make_req(task="general"))
        assert ei.value.code is ErrorCode.QUOTA_EXHAUSTED
        assert "workers_ai" in ei.value.details["providers"]
        assert cf_calls == []  # fail closed: no network call

    def test_gemini_budget_exhausted_no_call_made(self, monkeypatch, privacy):
        configure_gemini(monkeypatch)
        monkeypatch.setenv("GEMINI_DAILY_REQUEST_BUDGET", "5")
        reset_settings_cache()
        quota.get_store().increment("gemini", quota.today(), requests=5)
        gm_calls = install_gemini(monkeypatch)
        with pytest.raises(CloudOSError) as ei:
            route(make_req(task="general"))
        assert ei.value.code is ErrorCode.QUOTA_EXHAUSTED
        assert "gemini" in ei.value.details["providers"]
        assert gm_calls == []

    def test_l1_over_budget_escalates_to_l2(self, monkeypatch, privacy):
        configure_cf(monkeypatch)
        configure_gemini(monkeypatch)
        monkeypatch.setenv("WORKERS_AI_DAILY_BUDGET", "1")
        reset_settings_cache()
        quota.get_store().increment("workers_ai", quota.today(), requests=1)
        cf_calls = install_cf(monkeypatch)
        gm_calls = install_gemini(monkeypatch)
        res = route(make_req(task="general"))
        assert res.level == 2
        assert cf_calls == [] and len(gm_calls) == 1

    def test_budget_boundary_last_request_allowed(self, monkeypatch):
        configure_cf(monkeypatch)
        monkeypatch.setenv("WORKERS_AI_DAILY_BUDGET", "2")
        reset_settings_cache()
        quota.get_store().increment("workers_ai", quota.today(), requests=1)
        cf_calls = install_cf(monkeypatch)
        assert route(make_req(task="general")).ok  # usage 1 < 2 → allowed
        with pytest.raises(CloudOSError) as ei:
            route(make_req(task="general"))  # usage 2 >= 2 → closed
        assert ei.value.code is ErrorCode.QUOTA_EXHAUSTED
        assert len(cf_calls) == 1

    def test_quota_increments_per_successful_call(self, monkeypatch):
        configure_cf(monkeypatch)
        install_cf(monkeypatch)
        route(make_req(task="general"))
        route(make_req(task="general"))
        assert quota.get_store().get_usage("workers_ai", quota.today()).requests == 2

    def test_quota_store_failure_treated_as_over_budget(self, monkeypatch, broken_db):
        configure_cf(monkeypatch)
        cf_calls = install_cf(monkeypatch)
        with pytest.raises(CloudOSError) as ei:
            route(make_req(task="general"))
        assert ei.value.code is ErrorCode.QUOTA_EXHAUSTED
        assert cf_calls == []  # fail closed, no call


# -------------------------------------------------------------------- privacy

class TestPrivacyGate:
    def test_privacy_blocked_propagates_no_call(self, monkeypatch, privacy):
        configure_gemini(monkeypatch)
        privacy.decision = GateDecision(allowed=False, reason="label blocks external AI")
        gm_calls = install_gemini(monkeypatch)
        with pytest.raises(CloudOSError) as ei:
            route(make_req(task="general", privacy_label=PrivacyLabel.PERSONAL))
        assert ei.value.code is ErrorCode.PRIVACY_BLOCKED
        assert "label blocks external AI" in ei.value.message
        assert gm_calls == []

    def test_secret_detected_propagates_safe_metadata_only(self, monkeypatch, privacy):
        configure_gemini(monkeypatch)
        privacy.decision = GateDecision(
            allowed=False,
            reason="secret found",
            hits=[SecretHit("aws_access_key", (0, 20), "AKIA…")],
        )
        gm_calls = install_gemini(monkeypatch)
        with pytest.raises(CloudOSError) as ei:
            route(make_req("prompt with AKIAXXXXXXXXXXXXXXXX inside", task="general"))
        exc = ei.value
        assert exc.code is ErrorCode.SECRET_DETECTED
        assert exc.details["patterns"] == ["aws_access_key"]
        # never the secret (or even the redacted sample) in message/details
        blob = exc.message + json.dumps(exc.details)
        assert "AKIA" not in blob
        assert gm_calls == []

    def test_gate_sees_full_outbound_payload_and_label(self, monkeypatch, privacy):
        configure_gemini(monkeypatch)
        install_gemini(monkeypatch)
        route(make_req("the quick brown fox", task="general", privacy_label=PrivacyLabel.PUBLIC))
        assert len(privacy.calls) == 1
        content, label = privacy.calls[0]
        assert "the quick brown fox" in content  # full serialized payload
        assert "generationConfig" in content
        assert label is PrivacyLabel.PUBLIC

    def test_gate_runs_before_any_network_call(self, monkeypatch, privacy):
        configure_gemini(monkeypatch)
        order: list[str] = []
        real_gate = privacy.gate

        def tracking_gate(content, label):
            order.append("gate")
            return real_gate(content, label)

        sys.modules["cloudos.privacy"].gate = tracking_gate

        def handler(request):
            order.append("network")
            return httpx.Response(
                200,
                json={
                    "candidates": [{"content": {"parts": [{"text": "ok"}]}}],
                    "usageMetadata": {},
                },
            )

        monkeypatch.setattr(gemini_provider, "_transport", httpx.MockTransport(handler))
        route(make_req(task="general"))
        assert order == ["gate", "network"]

    def test_gate_unavailable_blocks_external_call(self, monkeypatch):
        configure_gemini(monkeypatch)
        # None in sys.modules ⇒ import raises — simulates a missing/broken privacy module
        monkeypatch.setitem(sys.modules, "cloudos.privacy", None)
        gm_calls = install_gemini(monkeypatch)
        with pytest.raises(CloudOSError) as ei:
            route(make_req(task="general"))
        assert ei.value.code is ErrorCode.DEPENDENCY_UNAVAILABLE
        assert gm_calls == []


# ----------------------------------------------------------------- agent_runs

class TestRunRecording:
    def test_success_writes_one_compact_row(self, monkeypatch, privacy, fake_db):
        configure_cf(monkeypatch)
        install_cf(monkeypatch)
        prompt = "please summarize my extremely private notes"
        res = route(make_req(prompt, task="general"))
        assert res.ok
        rows = [(sql, params) for sql, params in fake_db if "agent_runs" in sql]
        assert len(rows) == 1
        sql, params = rows[0]
        assert "'router'" in sql
        assert sha256_hash(prompt) in params  # input_hash present
        assert sha256_hash("cf-answer") in params  # output_hash present
        assert "succeeded" in params
        assert WA_FIRST in params
        # NEVER the prompt or output text
        joined = " ".join(str(p) for p in params)
        assert prompt not in joined
        assert "cf-answer" not in joined

    def test_failure_writes_row_with_error_code(self, monkeypatch, fake_db):
        # no providers configured → DEPENDENCY_UNAVAILABLE, but the row is still written
        with pytest.raises(CloudOSError):
            route(make_req(task="general"))
        rows = [(sql, params) for sql, params in fake_db if "agent_runs" in sql]
        assert len(rows) == 1
        _, params = rows[0]
        assert "failed" in params
        assert ErrorCode.DEPENDENCY_UNAVAILABLE.value in params

    def test_run_write_failure_never_fails_route(self, monkeypatch, broken_db):
        res = route(make_req("hi", task="echo"))  # L0 needs no quota, db still broken
        assert res.ok and res.level == 0

    def test_no_db_configured_writes_nothing_and_works(self):
        res = route(make_req("hi", task="echo"))
        assert res.ok  # DATABASE_URL empty in the isolate fixture — nothing to assert but success


# -------------------------------------------------------------- quota store

class TestQuotaStoreLocalMode:
    def test_accumulates_per_provider_and_day(self):
        store = quota.QuotaStore()
        day = quota.today()
        assert store.get_usage("workers_ai", day).requests == 0
        store.increment("workers_ai", day, requests=2, units=30)
        store.increment("workers_ai", day, requests=1)
        store.increment("gemini", day, requests=5)
        usage = store.get_usage("workers_ai", day)
        assert (usage.requests, usage.units) == (3, 30)
        assert store.get_usage("gemini", day).requests == 5

    def test_db_mode_upsert_and_read(self, monkeypatch, fake_db):
        store = quota.QuotaStore()
        day = quota.today()
        store.increment("gemini", day, requests=1)
        assert store.get_usage("gemini", day).requests == 0  # stub fetchone → None row
        sqls = [sql for sql, _ in fake_db]
        assert any("INSERT INTO quota_usage" in s and "ON CONFLICT" in s for s in sqls)
        assert any("SELECT requests, units FROM quota_usage" in s for s in sqls)

    def test_db_mode_failure_raises_quota_store_error(self, monkeypatch, broken_db):
        store = quota.QuotaStore()
        with pytest.raises(quota.QuotaStoreError):
            store.get_usage("gemini", quota.today())
        with pytest.raises(quota.QuotaStoreError):
            store.increment("gemini", quota.today())
