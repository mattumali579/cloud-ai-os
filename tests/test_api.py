"""Agent API tests (Agent 2). No live DB, no network.

`cloudos.db` (Agent 4, built in parallel) is replaced with a fake in-memory
module whose cursor dispatches on the store's SQL. The router (Agent 8) is
replaced with a fake `cloudos.router` module.

Run: python -m pytest tests/test_api.py -q
"""
from __future__ import annotations

import hashlib
import hmac
import json
import sys
import types
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

import cloudos
from cloudos import config
from cloudos.contracts import CloudOSError, ErrorCode, RouteResult

TOKEN = "test-token-abc123"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


# ------------------------------------------------------------------ fake db

class FakeDB:
    """In-memory jobs/agent_runs/quota_usage store behind a psycopg-shaped API."""

    def __init__(self) -> None:
        self.jobs: dict[str, dict] = {}
        self.runs: list[dict] = []
        self.quota: list[dict] = []
        self.healthy = True
        self.raise_on_conn = False
        self.migrate_calls = 0


class FakeCursor:
    def __init__(self, db: FakeDB) -> None:
        self.db = db
        self._rows: list[dict] = []

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *exc) -> None:
        return None

    def execute(self, sql: str, params: tuple = ()) -> None:
        nsql = " ".join(sql.split()).lower()
        params = list(params)
        if nsql.startswith("insert into jobs"):
            type_, payload_json, priority, run_at, max_attempts = params
            now = datetime.now(timezone.utc)
            row = {
                "id": uuid.uuid4(),
                "type": type_,
                "status": "queued",
                "priority": priority,
                "payload": json.loads(payload_json),
                "result": None,
                "error_code": None,
                "error": None,
                "attempts": 0,
                "max_attempts": max_attempts,
                "run_at": run_at or now,
                "created_at": now,
                "started_at": None,
                "finished_at": None,
                "locked_by": None,
                "locked_at": None,
            }
            self.db.jobs[str(row["id"])] = row
            self._rows = [row]
        elif nsql.startswith("update jobs"):
            row = self.db.jobs.get(str(params[0]))
            if row is not None and row["status"] == "queued":
                row["status"] = "cancelled"
                row["finished_at"] = datetime.now(timezone.utc)
                self._rows = [row]
            else:
                self._rows = []
        elif "from jobs where id = %s" in nsql:
            row = self.db.jobs.get(str(params[0]))
            self._rows = [row] if row is not None else []
        elif "from jobs" in nsql:
            limit = params.pop()  # LIMIT is always the last parameter
            rows = list(reversed(list(self.db.jobs.values())))  # created_at DESC
            if "status = %s" in nsql:
                want = params.pop(0)
                rows = [r for r in rows if r["status"] == want]
            if "type = %s" in nsql:
                want = params.pop(0)
                rows = [r for r in rows if r["type"] == want]
            self._rows = rows[: int(limit)]
        elif "from agent_runs" in nsql:
            self._rows = self.db.runs[: int(params[0])]
        elif "from quota_usage" in nsql:
            self._rows = list(self.db.quota)
        else:  # pragma: no cover — a store SQL change must update this fake
            raise AssertionError(f"fake db got unexpected SQL: {sql}")

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeConn:
    def __init__(self, db: FakeDB) -> None:
        self.db = db

    def cursor(self) -> FakeCursor:
        return FakeCursor(self.db)

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None


def make_fake_db_module(state: FakeDB) -> types.ModuleType:
    mod = types.ModuleType("cloudos.db")

    @contextmanager
    def get_conn():
        if state.raise_on_conn:
            raise ConnectionError("connection refused")
        yield FakeConn(state)

    def healthcheck() -> bool:
        if not state.healthy:
            raise ConnectionError("db down")
        return True

    def migrate(conn=None) -> list[str]:
        state.migrate_calls += 1
        return []

    mod.get_conn = get_conn
    mod.healthcheck = healthcheck
    mod.migrate = migrate
    mod.reset_pool = lambda: None
    mod._state = state
    return mod


# ------------------------------------------------------------------ fixtures

@pytest.fixture()
def fake_db(monkeypatch) -> FakeDB:
    state = FakeDB()
    mod = make_fake_db_module(state)
    monkeypatch.setitem(sys.modules, "cloudos.db", mod)
    monkeypatch.setattr(cloudos, "db", mod, raising=False)
    return state


@pytest.fixture()
def client(monkeypatch, fake_db):
    monkeypatch.setenv("AGENT_API_TOKEN", TOKEN)
    config.reset_settings_cache()
    from cloudos.api.app import app

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    config.reset_settings_cache()


def assert_error_shape(body: dict, code: str) -> None:
    assert set(body.keys()) == {"error"}
    err = body["error"]
    assert err["code"] == code
    assert isinstance(err["message"], str) and err["message"]
    assert isinstance(err["details"], dict)


# ------------------------------------------------------------------ healthz

def test_healthz_no_auth_db_up(client, fake_db):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "db": True}


def test_healthz_db_down_still_200(client, fake_db):
    fake_db.healthy = False
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "db": False}


def test_healthz_db_module_missing_still_200(client, monkeypatch):
    monkeypatch.setitem(sys.modules, "cloudos.db", None)  # forces ImportError
    monkeypatch.delattr(cloudos, "db", raising=False)
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "db": False}


def test_startup_runs_migrate(client, fake_db):
    assert fake_db.migrate_calls == 1


# ------------------------------------------------------------------ auth

@pytest.mark.parametrize(
    "path,method",
    [
        ("/v1/jobs", "post"),
        ("/v1/jobs", "get"),
        ("/v1/jobs/00000000-0000-0000-0000-000000000000", "get"),
        ("/v1/jobs/00000000-0000-0000-0000-000000000000/cancel", "post"),
        ("/v1/agent/invoke", "post"),
        ("/v1/runs", "get"),
        ("/v1/quota", "get"),
    ],
)
def test_missing_token_is_401(client, path, method):
    r = getattr(client, method)(path)
    assert r.status_code == 401
    assert_error_shape(r.json(), "VALIDATION_ERROR")


def test_wrong_token_is_401(client):
    r = client.get("/v1/jobs", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401
    assert_error_shape(r.json(), "VALIDATION_ERROR")


def test_unconfigured_token_rejects_everything(client, monkeypatch):
    monkeypatch.setenv("AGENT_API_TOKEN", "")
    config.reset_settings_cache()
    r = client.get("/v1/jobs", headers={"Authorization": "Bearer "})
    assert r.status_code == 401


# ------------------------------------------------------------------ jobs

def test_create_job(client):
    r = client.post(
        "/v1/jobs",
        headers=AUTH,
        json={"type": "ai.generate", "payload": {"prompt": "hi"}, "priority": 5},
    )
    assert r.status_code == 201
    job = r.json()
    assert job["type"] == "ai.generate"
    assert job["status"] == "queued"
    assert job["priority"] == 5
    assert job["payload"] == {"prompt": "hi"}
    assert job["attempts"] == 0
    assert job["max_attempts"] == 3
    uuid.UUID(job["id"])  # id serialized as a UUID string
    datetime.fromisoformat(job["created_at"])  # timestamps ISO 8601


def test_create_job_missing_type_400(client):
    r = client.post("/v1/jobs", headers=AUTH, json={"payload": {}})
    assert r.status_code == 400
    assert_error_shape(r.json(), "VALIDATION_ERROR")


def test_create_job_unknown_field_400(client):
    r = client.post("/v1/jobs", headers=AUTH, json={"type": "noop", "bogus": 1})
    assert r.status_code == 400
    assert_error_shape(r.json(), "VALIDATION_ERROR")


def test_get_job_roundtrip(client):
    created = client.post("/v1/jobs", headers=AUTH, json={"type": "noop"}).json()
    r = client.get(f"/v1/jobs/{created['id']}", headers=AUTH)
    assert r.status_code == 200
    assert r.json() == created


def test_get_job_bad_uuid_400(client):
    r = client.get("/v1/jobs/not-a-uuid", headers=AUTH)
    assert r.status_code == 400
    assert_error_shape(r.json(), "VALIDATION_ERROR")


def test_get_job_missing_404(client):
    r = client.get(f"/v1/jobs/{uuid.uuid4()}", headers=AUTH)
    assert r.status_code == 404
    assert_error_shape(r.json(), "VALIDATION_ERROR")


def test_list_jobs_filters_and_limit(client):
    client.post("/v1/jobs", headers=AUTH, json={"type": "noop"})
    client.post("/v1/jobs", headers=AUTH, json={"type": "sb.reindex"})
    third = client.post("/v1/jobs", headers=AUTH, json={"type": "noop"}).json()
    client.post(f"/v1/jobs/{third['id']}/cancel", headers=AUTH)

    all_jobs = client.get("/v1/jobs", headers=AUTH).json()["jobs"]
    assert len(all_jobs) == 3
    assert all_jobs[0]["id"] == third["id"]  # newest first

    queued_noop = client.get(
        "/v1/jobs", headers=AUTH, params={"status": "queued", "type": "noop"}
    ).json()["jobs"]
    assert len(queued_noop) == 1
    assert queued_noop[0]["type"] == "noop"
    assert queued_noop[0]["status"] == "queued"

    limited = client.get("/v1/jobs", headers=AUTH, params={"limit": 2}).json()["jobs"]
    assert len(limited) == 2


def test_list_jobs_bad_status_400(client):
    r = client.get("/v1/jobs", headers=AUTH, params={"status": "sideways"})
    assert r.status_code == 400
    assert_error_shape(r.json(), "VALIDATION_ERROR")


def test_cancel_only_queued(client):
    job = client.post("/v1/jobs", headers=AUTH, json={"type": "noop"}).json()

    r = client.post(f"/v1/jobs/{job['id']}/cancel", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"
    assert r.json()["finished_at"] is not None

    r2 = client.post(f"/v1/jobs/{job['id']}/cancel", headers=AUTH)  # already cancelled
    assert r2.status_code == 400
    assert_error_shape(r2.json(), "VALIDATION_ERROR")

    r3 = client.post(f"/v1/jobs/{uuid.uuid4()}/cancel", headers=AUTH)
    assert r3.status_code == 404


def test_jobs_503_when_db_unreachable(client, fake_db):
    fake_db.raise_on_conn = True
    r = client.post("/v1/jobs", headers=AUTH, json={"type": "noop"})
    assert r.status_code == 503
    assert_error_shape(r.json(), "DEPENDENCY_UNAVAILABLE")
    # error must carry metadata only — never a connection string / secret
    assert "connection refused" not in json.dumps(r.json())


# ------------------------------------------------------------------ webhook

def _signed(body: bytes) -> dict:
    sig = hmac.new(TOKEN.encode(), body, hashlib.sha256).hexdigest()
    return {"X-Signature": sig, "Content-Type": "application/json"}


def test_webhook_hmac_accept(client):
    raw = json.dumps({"event": "form.submitted", "data": {"email": "x@y.z"}}).encode()
    r = client.post("/v1/webhooks/n8n", content=raw, headers=_signed(raw))
    assert r.status_code == 201
    job = r.json()
    assert job["type"] == "webhook.form.submitted"
    assert job["payload"] == {"email": "x@y.z"}
    assert job["status"] == "queued"


def test_webhook_hmac_sha256_prefix_accept(client):
    raw = json.dumps({"event": "ping", "data": {}}).encode()
    headers = _signed(raw)
    headers["X-Signature"] = "sha256=" + headers["X-Signature"]
    r = client.post("/v1/webhooks/n8n", content=raw, headers=headers)
    assert r.status_code == 201


def test_webhook_hmac_reject_tampered_body(client):
    raw = json.dumps({"event": "ping", "data": {}}).encode()
    headers = _signed(raw)
    r = client.post("/v1/webhooks/n8n", content=raw + b" ", headers=headers)
    assert r.status_code == 401
    assert_error_shape(r.json(), "VALIDATION_ERROR")


def test_webhook_no_auth_reject(client):
    raw = json.dumps({"event": "ping", "data": {}}).encode()
    r = client.post("/v1/webhooks/n8n", content=raw, headers={"Content-Type": "application/json"})
    assert r.status_code == 401


def test_webhook_bearer_accept(client):
    r = client.post("/v1/webhooks/n8n", headers=AUTH, json={"event": "deploy.done"})
    assert r.status_code == 201
    assert r.json()["type"] == "webhook.deploy.done"
    assert r.json()["payload"] == {}


def test_webhook_malformed_json_400(client):
    raw = b"{not json"
    r = client.post("/v1/webhooks/n8n", content=raw, headers=_signed(raw))
    assert r.status_code == 400
    assert_error_shape(r.json(), "VALIDATION_ERROR")


def test_webhook_missing_event_400(client):
    raw = json.dumps({"data": {"a": 1}}).encode()
    r = client.post("/v1/webhooks/n8n", content=raw, headers=_signed(raw))
    assert r.status_code == 400
    assert_error_shape(r.json(), "VALIDATION_ERROR")


# ------------------------------------------------------------------ invoke

@pytest.fixture()
def fake_router(monkeypatch):
    mod = types.ModuleType("cloudos.router")
    mod.calls = []

    def route(req):
        mod.calls.append(req)
        if isinstance(getattr(mod, "raises", None), Exception):
            raise mod.raises
        return RouteResult(
            ok=True, text="hello", model="@cf/test-model", level=1,
            prompt_tokens=3, completion_tokens=2,
        )

    mod.route = route
    monkeypatch.setitem(sys.modules, "cloudos.router", mod)
    monkeypatch.setattr(cloudos, "router", mod, raising=False)
    return mod


def test_invoke_builds_route_request(client, fake_router):
    r = client.post(
        "/v1/agent/invoke",
        headers=AUTH,
        json={"prompt": "hi", "task": "summarize", "privacy_label": "public", "max_tokens": 64},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["text"] == "hello"
    assert body["level"] == 1
    assert body["cost_usd"] == 0.0
    (req,) = fake_router.calls
    assert req.prompt == "hi"
    assert req.task == "summarize"
    assert req.privacy_label.value == "public"
    assert req.max_tokens == 64
    assert req.model_hint is None


def test_invoke_bad_privacy_label_400(client, fake_router):
    r = client.post("/v1/agent/invoke", headers=AUTH, json={"prompt": "hi", "privacy_label": "loud"})
    assert r.status_code == 400
    assert_error_shape(r.json(), "VALIDATION_ERROR")
    assert fake_router.calls == []


def test_invoke_router_missing_503(client, monkeypatch):
    monkeypatch.setitem(sys.modules, "cloudos.router", None)  # forces ImportError
    monkeypatch.delattr(cloudos, "router", raising=False)
    r = client.post("/v1/agent/invoke", headers=AUTH, json={"prompt": "hi"})
    assert r.status_code == 503
    assert_error_shape(r.json(), "DEPENDENCY_UNAVAILABLE")


# ------------------------------------------------------------------ error mapping

@pytest.mark.parametrize(
    "code,status",
    [
        (ErrorCode.VALIDATION_ERROR, 400),
        (ErrorCode.PAID_DISABLED, 403),
        (ErrorCode.MODEL_NOT_ALLOWED, 403),
        (ErrorCode.PRIVACY_BLOCKED, 403),
        (ErrorCode.SECRET_DETECTED, 403),
        (ErrorCode.QUOTA_EXHAUSTED, 429),
        (ErrorCode.LIMIT_REACHED, 429),
        (ErrorCode.DEPENDENCY_UNAVAILABLE, 503),
        (ErrorCode.INTERNAL_ERROR, 500),
    ],
)
def test_cloudos_error_http_mapping(client, fake_router, code, status):
    fake_router.raises = CloudOSError(code, "boom", {"hint": "test"})
    r = client.post("/v1/agent/invoke", headers=AUTH, json={"prompt": "hi"})
    assert r.status_code == status
    body = r.json()
    assert_error_shape(body, code.value)
    assert body["error"]["details"] == {"hint": "test"}


# ------------------------------------------------------------------ runs & quota

def test_runs_compact_and_serialized(client, fake_db):
    fake_db.runs = [
        {
            "run_id": uuid.uuid4(),
            "job_id": None,
            "agent": "router",
            "workflow": "adhoc",
            "model": "@cf/test-model",
            "level": 1,
            "started_at": datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc),
            "finished_at": datetime(2026, 8, 28, 12, 0, 1, tzinfo=timezone.utc),
            "status": "succeeded",
            "prompt_tokens": 10,
            "completion_tokens": 4,
            "cost_usd": Decimal("0"),
            "error_code": None,
            "input_hash": "a" * 64,
            "output_hash": "b" * 64,
        }
    ]
    r = client.get("/v1/runs", headers=AUTH, params={"limit": 10})
    assert r.status_code == 200
    (run,) = r.json()["runs"]
    assert run["cost_usd"] == 0.0
    assert run["started_at"] == "2026-08-28T12:00:00+00:00"
    uuid.UUID(run["run_id"])
    assert "prompt" not in run and "meta" not in run  # compact rows only


def test_quota_budgets_and_fail_closed(client, fake_db):
    today = date.today()
    fake_db.quota = [
        {"provider": "gemini", "day": today, "requests": 200, "units": 0},
        {"provider": "workers_ai", "day": today, "requests": 5, "units": 100},
    ]
    r = client.get("/v1/quota", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["budgets"] == {"workers_ai": 9000, "gemini": 200}
    assert body["fail_closed"] == {"workers_ai": False, "gemini": True}
    assert len(body["usage"]) == 2
    assert body["usage"][0]["day"] == today.isoformat()


def test_quota_empty_usage_not_fail_closed(client, fake_db):
    r = client.get("/v1/quota", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["fail_closed"] == {"workers_ai": False, "gemini": False}
