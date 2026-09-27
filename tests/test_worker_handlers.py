"""Handler dispatch tests (Agent 3) — fake modules for router/second_brain/db."""
from __future__ import annotations

import pytest

from cloudos.config import reset_settings_cache
from cloudos.contracts import CloudOSError, ErrorCode, PrivacyLabel, RouteRequest, RouteResult
from cloudos.worker import handlers

from test_worker_fakes import FakeConn, executed_sql, install_fake_db, install_fake_module


@pytest.fixture(autouse=True)
def _fresh_settings():
    reset_settings_cache()
    yield
    reset_settings_cache()


# -- dispatch / registry --------------------------------------------------

def test_registry_has_all_contract_types():
    assert set(handlers.HANDLERS) == {
        "noop", "task.run", "ai.generate", "sb.reindex", "retention.prune", "notify.flush", "job.agent.run"
    }


def test_noop_echoes_payload():
    assert handlers.dispatch({"type": "noop", "payload": {"x": 1}}) == {"x": 1}


def test_dispatch_parses_json_string_payload():
    assert handlers.dispatch({"type": "noop", "payload": '{"a": 2}'}) == {"a": 2}


def test_dispatch_none_payload_becomes_empty_dict():
    assert handlers.dispatch({"type": "noop", "payload": None}) == {}


def test_dispatch_unknown_type_raises_validation_error():
    with pytest.raises(CloudOSError) as excinfo:
        handlers.dispatch({"type": "does.not.exist", "payload": {}})
    assert excinfo.value.code is ErrorCode.VALIDATION_ERROR


def test_dispatch_invalid_json_payload_raises_validation_error():
    with pytest.raises(CloudOSError) as excinfo:
        handlers.dispatch({"type": "noop", "payload": "{not json"})
    assert excinfo.value.code is ErrorCode.VALIDATION_ERROR


def test_dispatch_non_object_payload_raises_validation_error():
    with pytest.raises(CloudOSError) as excinfo:
        handlers.dispatch({"type": "noop", "payload": "[1, 2]"})
    assert excinfo.value.code is ErrorCode.VALIDATION_ERROR


# -- ai.generate ----------------------------------------------------------

def _fake_router(monkeypatch, result: RouteResult):
    calls: list[RouteRequest] = []

    def route(req: RouteRequest) -> RouteResult:
        calls.append(req)
        return result

    install_fake_module(monkeypatch, "cloudos.router", route=route)
    return calls


def test_ai_generate_builds_route_request_and_returns_result_dict(monkeypatch):
    result = RouteResult(ok=True, text="hi", model="m1", level=1,
                         prompt_tokens=3, completion_tokens=4)
    calls = _fake_router(monkeypatch, result)
    out = handlers.dispatch({
        "type": "ai.generate",
        "payload": {"prompt": "hello", "task": "summarize", "privacy_label": "public",
                    "max_tokens": 99, "model_hint": "m1", "meta": {"k": "v"}},
    })
    assert out == result.to_dict()
    assert out["cost_usd"] == 0.0
    (req,) = calls
    assert isinstance(req, RouteRequest)
    assert req.prompt == "hello"
    assert req.task == "summarize"
    assert req.privacy_label is PrivacyLabel.PUBLIC
    assert req.max_tokens == 99
    assert req.model_hint == "m1"
    assert req.meta == {"k": "v"}


def test_ai_generate_defaults_internal_label(monkeypatch):
    calls = _fake_router(monkeypatch, RouteResult(ok=True, text="t"))
    handlers.dispatch({"type": "ai.generate", "payload": {"prompt": "p"}})
    assert calls[0].privacy_label is PrivacyLabel.INTERNAL
    assert calls[0].max_tokens == 1024


def test_ai_generate_not_ok_raises_with_router_error_code(monkeypatch):
    _fake_router(monkeypatch, RouteResult(ok=False, error_code=ErrorCode.QUOTA_EXHAUSTED,
                                          error="gemini budget spent"))
    with pytest.raises(CloudOSError) as excinfo:
        handlers.dispatch({"type": "ai.generate", "payload": {"prompt": "p"}})
    assert excinfo.value.code is ErrorCode.QUOTA_EXHAUSTED
    assert "gemini budget spent" in excinfo.value.message


def test_ai_generate_missing_prompt_raises_validation_error(monkeypatch):
    calls = _fake_router(monkeypatch, RouteResult(ok=True))
    with pytest.raises(CloudOSError) as excinfo:
        handlers.dispatch({"type": "ai.generate", "payload": {}})
    assert excinfo.value.code is ErrorCode.VALIDATION_ERROR
    assert calls == []  # router never called


def test_ai_generate_bad_privacy_label_raises_validation_error(monkeypatch):
    _fake_router(monkeypatch, RouteResult(ok=True))
    with pytest.raises(CloudOSError) as excinfo:
        handlers.dispatch({"type": "ai.generate",
                           "payload": {"prompt": "p", "privacy_label": "ultra"}})
    assert excinfo.value.code is ErrorCode.VALIDATION_ERROR


# -- sb.reindex -----------------------------------------------------------

def test_sb_reindex_calls_second_brain_with_conn(monkeypatch):
    conn = FakeConn()
    install_fake_db(monkeypatch, conn)
    seen = []

    def reindex(db_conn):
        seen.append(db_conn)
        return {"scanned": 4, "indexed": 2, "skipped": 2}

    install_fake_module(monkeypatch, "cloudos.second_brain", reindex=reindex)
    out = handlers.dispatch({"type": "sb.reindex", "payload": {}})
    assert out == {"scanned": 4, "indexed": 2, "skipped": 2}
    assert seen == [conn]


# -- retention.prune ------------------------------------------------------

def test_retention_prune_handler_forwards_dry_run(monkeypatch):
    from cloudos.worker import retention

    captured = []

    def fake_prune(dry_run=None, conn=None):
        captured.append(dry_run)
        return {"events": {"selected": 0, "deleted": 0, "failures": 0}}

    monkeypatch.setattr(retention, "prune", fake_prune)
    handlers.dispatch({"type": "retention.prune", "payload": {"dry_run": True}})
    handlers.dispatch({"type": "retention.prune", "payload": {}})
    handlers.dispatch({"type": "retention.prune", "payload": {"dry_run": "yes"}})  # non-bool ignored
    assert captured == [True, None, None]


# -- notify.flush ---------------------------------------------------------

class _Resp:
    def __init__(self, status_code: int):
        self.status_code = status_code


def test_notify_flush_delivers_and_marks_rows(monkeypatch):
    monkeypatch.setenv("NOTIFY_WEBHOOK_URL", "https://n8n.example/webhook")
    reset_settings_cache()
    rows = [
        {"id": 1, "severity": "critical", "code": "QUOTA_EXHAUSTED", "message": "m1", "meta": {}},
        {"id": 2, "severity": "warning", "code": "LIMIT_REACHED", "message": "m2",
         "meta": '{"a": 1}'},
    ]
    conn = FakeConn(results=[rows, None, None])
    install_fake_db(monkeypatch, conn)

    posts = []

    def fake_post(url, json=None, timeout=None):
        posts.append((url, json))
        return _Resp(200)

    monkeypatch.setattr("httpx.post", fake_post)
    out = handlers.dispatch({"type": "notify.flush", "payload": {}})
    assert out == {"attempted": 2, "delivered": 2, "pending": 0}
    assert len(posts) == 2
    assert posts[0][0] == "https://n8n.example/webhook"
    assert posts[1][1]["meta"] == {"a": 1}  # string meta decoded
    updates = [sql for sql in executed_sql(conn) if "SET delivered = true" in sql]
    assert len(updates) == 2


def test_notify_flush_counts_webhook_failures(monkeypatch):
    monkeypatch.setenv("NOTIFY_WEBHOOK_URL", "https://n8n.example/webhook")
    reset_settings_cache()
    rows = [{"id": 1, "severity": "critical", "code": "X", "message": "m", "meta": {}}]
    conn = FakeConn(results=[rows])
    install_fake_db(monkeypatch, conn)
    monkeypatch.setattr("httpx.post", lambda *a, **k: _Resp(500))
    out = handlers.dispatch({"type": "notify.flush", "payload": {}})
    assert out == {"attempted": 1, "delivered": 0, "pending": 1}
    assert not any("SET delivered = true" in sql for sql in executed_sql(conn))


def test_notify_flush_without_webhook_url_makes_no_network_calls(monkeypatch):
    monkeypatch.setenv("NOTIFY_WEBHOOK_URL", "")  # empty ⇒ notifications table only
    reset_settings_cache()
    rows = [{"id": 1, "severity": "critical", "code": "X", "message": "m", "meta": {}}]
    conn = FakeConn(results=[rows])
    install_fake_db(monkeypatch, conn)

    def explode(*a, **k):  # any network call is a test failure
        raise AssertionError("httpx.post must not be called without a webhook URL")

    monkeypatch.setattr("httpx.post", explode)
    out = handlers.dispatch({"type": "notify.flush", "payload": {}})
    assert out == {"attempted": 0, "delivered": 0, "pending": 1}
