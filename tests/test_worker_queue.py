"""Queue operation tests (Agent 3) — fake connections, no live DB."""
from __future__ import annotations

import json

import pytest

from cloudos.contracts import ErrorCode
from cloudos.worker import queue

from test_worker_fakes import FakeConn, executed_sql, job_row


# -- claim_next -----------------------------------------------------------

def test_claim_next_empty_queue_returns_none_and_rolls_back():
    conn = FakeConn(results=[None])
    assert queue.claim_next(conn, "inst-a") is None
    assert conn.rollbacks == 1
    assert conn.commits == 0
    assert len(conn.executed) == 1


def test_claim_sql_shape():
    conn = FakeConn(results=[None])
    queue.claim_next(conn, "inst-a")
    sql = executed_sql(conn)[0]
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "status = 'queued'" in sql
    assert "run_at <= now()" in sql
    assert "ORDER BY priority, run_at" in sql
    assert "LIMIT 1" in sql


def test_claim_next_locks_and_returns_running_row():
    queued = job_row()
    running = job_row(status="running", locked_by="inst-a")
    conn = FakeConn(results=[queued, running])
    job = queue.claim_next(conn, "inst-a")
    assert job is running
    lock_sql, lock_params = conn.executed[1]
    assert "status = 'running'" in lock_sql
    assert "locked_by = %s" in lock_sql
    assert "started_at = now()" in lock_sql
    assert lock_params == ("inst-a", queued["id"])
    assert conn.commits == 1


# -- complete -------------------------------------------------------------

def test_complete_marks_succeeded_with_json_result():
    conn = FakeConn()
    queue.complete(conn, "job-1", {"answer": 42})
    sql, params = conn.executed[0]
    assert "status = 'succeeded'" in sql
    assert "finished_at = now()" in sql
    assert "locked_by = NULL" in sql
    assert json.loads(params[0]) == {"answer": 42}
    assert params[1] == "job-1"
    assert conn.commits == 1


# -- fail: retry/backoff math --------------------------------------------

def test_fail_first_attempt_requeues_with_2_minute_backoff():
    conn = FakeConn()
    status = queue.fail(conn, job_row(attempts=0, max_attempts=3), ErrorCode.INTERNAL_ERROR, "boom")
    assert status == "queued"
    sql, params = conn.executed[0]
    assert "status = 'queued'" in sql
    assert "make_interval(mins => %s)" in sql
    attempts, code, error, minutes, job_id = params
    assert attempts == 1
    assert code == "INTERNAL_ERROR"
    assert error == "boom"
    assert minutes == 2  # 2**1
    assert conn.commits == 1


def test_fail_backoff_doubles_each_attempt():
    conn = FakeConn()
    queue.fail(conn, job_row(attempts=1, max_attempts=5), "DEPENDENCY_UNAVAILABLE", "down")
    _, params = conn.executed[0]
    assert params[0] == 2  # attempts incremented
    assert params[3] == 4  # 2**2 minutes

    conn2 = FakeConn()
    queue.fail(conn2, job_row(attempts=2, max_attempts=5), "DEPENDENCY_UNAVAILABLE", "down")
    _, params2 = conn2.executed[0]
    assert params2[0] == 3
    assert params2[3] == 8  # 2**3 minutes


def test_fail_exhausted_attempts_marks_failed():
    conn = FakeConn()
    status = queue.fail(conn, job_row(attempts=2, max_attempts=3), ErrorCode.INTERNAL_ERROR, "boom")
    assert status == "failed"
    sql, params = conn.executed[0]
    assert "status = 'failed'" in sql
    assert "finished_at = now()" in sql
    assert "make_interval" not in sql  # no backoff — terminal
    assert params[0] == 3  # attempts persisted
    assert conn.commits == 1


def test_backoff_minutes_math():
    assert queue.backoff_minutes(1) == 2
    assert queue.backoff_minutes(2) == 4
    assert queue.backoff_minutes(3) == 8
    assert queue.backoff_minutes(5) == 32


# -- block: fail closed, NO retry ----------------------------------------

def test_block_sets_blocked_without_retry():
    conn = FakeConn()
    queue.block(conn, "job-9", ErrorCode.QUOTA_EXHAUSTED, "daily budget spent")
    assert len(conn.executed) == 1
    sql, params = conn.executed[0]
    assert "status = 'blocked'" in sql
    assert "make_interval" not in sql  # never rescheduled
    assert "attempts" not in sql       # attempts untouched — this is terminal
    assert params == ("QUOTA_EXHAUSTED", "daily budget spent", "job-9")
    assert conn.commits == 1


def test_block_accepts_string_code():
    conn = FakeConn()
    queue.block(conn, "job-9", "SECRET_DETECTED", "pattern: aws_key")
    _, params = conn.executed[0]
    assert params[0] == "SECRET_DETECTED"


# -- enqueue --------------------------------------------------------------

def test_enqueue_defaults():
    created = job_row(type="noop")
    conn = FakeConn(results=[created])
    job = queue.enqueue(conn, "noop", {"a": 1})
    assert job is created
    sql, params = conn.executed[0]
    assert "INSERT INTO jobs" in sql
    assert "COALESCE(%s, now())" in sql
    jtype, payload, priority, run_at, max_attempts = params
    assert jtype == "noop"
    assert json.loads(payload) == {"a": 1}
    assert priority == 100
    assert run_at is None
    assert max_attempts == 3
    assert conn.commits == 1


def test_enqueue_explicit_run_at_and_priority():
    from datetime import datetime, timezone

    when = datetime(2026, 8, 29, 3, 0, tzinfo=timezone.utc)
    conn = FakeConn(results=[job_row(type="retention.prune")])
    queue.enqueue(conn, "retention.prune", None, priority=200, run_at=when, max_attempts=1)
    _, params = conn.executed[0]
    assert json.loads(params[1]) == {}
    assert params[2] == 200
    assert params[3] == when
    assert params[4] == 1


# -- recover_stale --------------------------------------------------------

def test_recover_stale_only_touches_own_running_jobs():
    conn = FakeConn(results=[2])  # rowcount 2
    count = queue.recover_stale(conn, "inst-a")
    assert count == 2
    sql, params = conn.executed[0]
    assert "status = 'running' AND locked_by = %s" in sql
    assert "status = 'queued'" in sql
    assert params == ("inst-a",)
    assert conn.commits == 1
