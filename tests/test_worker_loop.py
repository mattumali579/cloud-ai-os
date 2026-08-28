"""Poll-loop tests (Agent 3): fail-closed dispatch, daily prune scheduling,
loop lifecycle. Fake DB module + fake connections — no live DB/network."""
from __future__ import annotations

import signal
from datetime import datetime, timezone

import pytest

from cloudos.contracts import CloudOSError, ErrorCode, FAIL_CLOSED_CODES
from cloudos.worker import loop as loop_mod
from cloudos.worker.loop import Worker, next_prune_run_at

from test_worker_fakes import FakeConn, executed_sql, install_fake_db, job_row


@pytest.fixture
def worker():
    return Worker(instance_name="inst-test", poll_interval=0)


@pytest.fixture
def notify_calls(monkeypatch):
    calls = []

    def fake_notify(conn, severity, code, message, meta=None):
        calls.append({"severity": severity, "code": code, "message": message, "meta": meta})

    monkeypatch.setattr(loop_mod, "notify_owner", fake_notify)
    return calls


def _dispatch(monkeypatch, fn):
    monkeypatch.setattr(loop_mod.handlers, "dispatch", fn)


# -- process_one: success -------------------------------------------------

def test_success_completes_job(worker, monkeypatch, notify_calls):
    _dispatch(monkeypatch, lambda job: {"echo": True})
    conn = FakeConn()
    status = worker.process_one(conn, job_row(id="j1"))
    assert status == "succeeded"
    assert any("status = 'succeeded'" in sql for sql in executed_sql(conn))
    assert notify_calls == []


# -- process_one: fail-closed codes → block, event, notify — NO retry -----

@pytest.mark.parametrize("code", sorted(FAIL_CLOSED_CODES, key=lambda c: c.value))
def test_fail_closed_codes_block_not_retry(worker, monkeypatch, notify_calls, code):
    def boom(job):
        raise CloudOSError(code, f"fail closed: {code.value}")

    _dispatch(monkeypatch, boom)
    conn = FakeConn()
    status = worker.process_one(conn, job_row(id="j2", type="ai.generate"))
    assert status == "blocked"
    sqls = executed_sql(conn)
    # persisted: blocked, never requeued, attempts untouched
    assert any("status = 'blocked'" in sql for sql in sqls)
    assert not any("status = 'queued'" in sql and "UPDATE" in sql for sql in sqls)
    assert not any("make_interval" in sql for sql in sqls)
    # logged: events row with the error code
    event = next((p for s, p in conn.executed if "INSERT INTO events" in s), None)
    assert event is not None
    assert event[2] == code.value
    # notified: owner alert with the same code
    assert len(notify_calls) == 1
    assert notify_calls[0]["severity"] == "critical"
    assert notify_calls[0]["code"] is code


def test_secret_detected_never_leaks_secret_material(worker, monkeypatch, notify_calls):
    def boom(job):
        raise CloudOSError(
            ErrorCode.SECRET_DETECTED,
            "secret detected in outbound payload",
            {"pattern_name": "aws_access_key", "hash": "abc123"},
        )

    _dispatch(monkeypatch, boom)
    conn = FakeConn()
    worker.process_one(conn, job_row(id="j3", type="ai.generate"))
    # only safe metadata flows into event/notification params
    flat = repr(conn.executed) + repr(notify_calls)
    assert "aws_access_key" in flat  # pattern name is fine
    assert "AKIA" not in flat        # never raw secret shapes


# -- process_one: retryable paths ----------------------------------------

def test_non_fail_closed_cloudos_error_retries(worker, monkeypatch, notify_calls):
    def boom(job):
        raise CloudOSError(ErrorCode.DEPENDENCY_UNAVAILABLE, "db hiccup")

    _dispatch(monkeypatch, boom)
    conn = FakeConn()
    status = worker.process_one(conn, job_row(id="j4", attempts=0, max_attempts=3))
    assert status == "queued"  # back in the queue with backoff
    sqls = executed_sql(conn)
    assert any("make_interval(mins => %s)" in sql for sql in sqls)
    assert not any("status = 'blocked'" in sql for sql in sqls)
    assert notify_calls == []


def test_validation_error_is_not_fail_closed(worker, monkeypatch, notify_calls):
    def boom(job):
        raise CloudOSError(ErrorCode.VALIDATION_ERROR, "unknown job type")

    _dispatch(monkeypatch, boom)
    conn = FakeConn()
    status = worker.process_one(conn, job_row(id="j5", attempts=2, max_attempts=3))
    assert status == "failed"  # attempts exhausted
    assert notify_calls == []


def test_generic_exception_fails_with_internal_error(worker, monkeypatch, notify_calls):
    def boom(job):
        raise RuntimeError("handler crashed")

    _dispatch(monkeypatch, boom)
    conn = FakeConn()
    status = worker.process_one(conn, job_row(id="j6", attempts=0, max_attempts=3))
    assert status == "queued"
    fail_params = next(p for s, p in conn.executed if "status = 'queued'" in s)
    assert fail_params[1] == "INTERNAL_ERROR"
    assert "handler crashed" in fail_params[2]


# -- daily retention scheduling ------------------------------------------

def test_next_prune_run_at_math():
    d = datetime(2026, 8, 28, 2, 59, tzinfo=timezone.utc)
    assert next_prune_run_at(d) == datetime(2026, 8, 28, 3, 0, tzinfo=timezone.utc)
    d = datetime(2026, 8, 28, 3, 0, tzinfo=timezone.utc)  # exactly 03:00 → tomorrow
    assert next_prune_run_at(d) == datetime(2026, 8, 29, 3, 0, tzinfo=timezone.utc)
    d = datetime(2026, 8, 28, 15, 30, tzinfo=timezone.utc)
    assert next_prune_run_at(d) == datetime(2026, 8, 29, 3, 0, tzinfo=timezone.utc)


def test_ensure_daily_prune_enqueues_when_none_pending(worker):
    now = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
    conn = FakeConn(results=[{"n": 0}, job_row(type="retention.prune", id="p1")])
    assert worker.ensure_daily_prune(conn, now=now) is True
    insert_sql, params = next((s, p) for s, p in conn.executed if "INSERT INTO jobs" in s)
    assert params[0] == "retention.prune"
    assert params[3] == datetime(2026, 8, 29, 3, 0, tzinfo=timezone.utc)  # next 03:00 UTC


def test_ensure_daily_prune_runs_check_once_per_day(worker):
    now = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
    conn = FakeConn(results=[{"n": 0}, job_row(type="retention.prune")])
    worker.ensure_daily_prune(conn, now=now)
    executed_before = len(conn.executed)
    # same loop-day: memoized, zero SQL — this is NOT a keepalive
    assert worker.ensure_daily_prune(conn, now=now) is False
    assert len(conn.executed) == executed_before
    # next day: checks again
    tomorrow = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)
    conn.results = [{"n": 0}, job_row(type="retention.prune")]
    assert worker.ensure_daily_prune(conn, now=tomorrow) is True


def test_ensure_daily_prune_skips_when_already_scheduled(worker):
    conn = FakeConn(results=[{"n": 1}])
    assert worker.ensure_daily_prune(conn) is False
    assert not any("INSERT INTO jobs" in sql for sql in executed_sql(conn))


# -- run_forever lifecycle ------------------------------------------------

def _idle_result_fn(sql, params):
    if "FROM jobs WHERE type = %s" in sql:
        return {"n": 1}  # prune already scheduled
    return None  # empty queue / zero rowcount everywhere else


def test_run_forever_migrates_once_and_polls(worker, monkeypatch):
    conn = FakeConn(result_fn=_idle_result_fn)
    fake_db = install_fake_db(monkeypatch, conn)
    worker.run_forever(max_iterations=3)
    assert fake_db.calls["migrate"] == 1
    claims = [sql for sql in executed_sql(conn) if "FOR UPDATE SKIP LOCKED" in sql]
    assert len(claims) == 3


def test_run_forever_survives_migrate_failure(worker, monkeypatch):
    conn = FakeConn(result_fn=_idle_result_fn)
    install_fake_db(monkeypatch, conn, migrate_exc=RuntimeError("db down"))
    worker.run_forever(max_iterations=1)  # must not raise
    assert any("FOR UPDATE SKIP LOCKED" in sql for sql in executed_sql(conn))


def test_run_forever_survives_iteration_exceptions(worker, monkeypatch):
    conn = FakeConn(result_fn=_idle_result_fn)
    fake_db = install_fake_db(monkeypatch, conn)
    calls = {"n": 0}

    def flaky(c, instance):
        calls["n"] += 1
        raise RuntimeError("connection dropped")

    monkeypatch.setattr(loop_mod.queue, "claim_next", flaky)
    worker.run_forever(max_iterations=3)  # must not raise; keeps polling
    assert calls["n"] == 3


def test_run_forever_processes_claimed_job(worker, monkeypatch):
    queued = job_row(id="j7")
    running = job_row(id="j7", status="running")
    state = {"claimed": False}

    def result_fn(sql, params):
        if "FROM jobs WHERE type = %s" in sql:
            return {"n": 1}
        if "FOR UPDATE SKIP LOCKED" in sql and not state["claimed"]:
            state["claimed"] = True
            return queued
        if "SET status = 'running'" in sql:
            return running
        return None

    conn = FakeConn(result_fn=result_fn)
    install_fake_db(monkeypatch, conn)
    _dispatch(monkeypatch, lambda job: {"done": True})
    worker.run_forever(max_iterations=2)
    assert any("status = 'succeeded'" in sql for sql in executed_sql(conn))


def test_run_forever_recovers_stale_jobs_at_startup(worker, monkeypatch):
    def result_fn(sql, params):
        if "status = 'running' AND locked_by = %s" in sql:
            return 2  # two stale jobs requeued
        if "FROM jobs WHERE type = %s" in sql:
            return {"n": 1}
        return None

    conn = FakeConn(result_fn=result_fn)
    install_fake_db(monkeypatch, conn)
    worker.run_forever(max_iterations=1)
    recover_sql, recover_params = next(
        (s, p) for s, p in conn.executed if "status = 'running' AND locked_by = %s" in s
    )
    assert recover_params == ("inst-test",)
    event = next((p for s, p in conn.executed if "INSERT INTO events" in s), None)
    assert event is not None and event[2] == "STALE_JOBS_REQUEUED"


def test_stop_event_prevents_iterations(worker, monkeypatch):
    conn = FakeConn(result_fn=_idle_result_fn)
    install_fake_db(monkeypatch, conn)
    worker.request_stop()
    worker.run_forever(max_iterations=10)
    assert not any("FOR UPDATE SKIP LOCKED" in sql for sql in executed_sql(conn))


def test_keyboard_interrupt_shuts_down_gracefully(worker, monkeypatch):
    conn = FakeConn(result_fn=_idle_result_fn)
    install_fake_db(monkeypatch, conn)

    def interrupt(c, instance):
        raise KeyboardInterrupt

    monkeypatch.setattr(loop_mod.queue, "claim_next", interrupt)
    worker.run_forever(max_iterations=5)  # must swallow and return, not raise


def test_signal_handler_sets_stop_event(worker):
    original = signal.getsignal(signal.SIGINT)
    try:
        worker._install_signal_handlers()
        handler = signal.getsignal(signal.SIGINT)
        assert callable(handler)
        handler(signal.SIGINT, None)
        assert worker.stop_event.is_set()
    finally:
        signal.signal(signal.SIGINT, original)
