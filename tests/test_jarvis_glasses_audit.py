import cloudos.glasses.audit as audit_module
from cloudos.glasses.audit import build_activity_record, enqueue_activity_event


def test_activity_record_is_sanitized_and_correlated():
    record = build_activity_record(
        "event-1",
        "result_verified",
        conversation_id="conv-1",
        request_id="req-1",
        job_id="job-1",
        tool_call_id="tool-1",
        command="open private-file.txt",
        result="private output",
        text="private transcript",
        token="secret",
    )

    assert record is not None
    assert record["outcome"] == "succeeded"
    assert record["details"] == {
        "conversation_id": "conv-1",
        "request_id": "req-1",
        "job_id": "job-1",
        "tool_call_id": "tool-1",
        "event_id": "event-1",
    }
    encoded = str(record)
    assert "private" not in encoded
    assert "secret" not in encoded


def test_failure_has_stable_fingerprint_without_sensitive_text():
    first = build_activity_record(
        "event-1",
        "speech_failed",
        error=True,
        error_type="TimeoutError",
        detail="contains secret one",
    )
    second = build_activity_record(
        "event-2",
        "speech_failed",
        error=True,
        error_type="TimeoutError",
        detail="contains secret two",
    )

    assert first is not None and second is not None
    assert first["fingerprint"] == second["fingerprint"]
    assert first["severity"] == "error"
    assert "detail" not in first["details"]


def test_enqueue_rate_limits_identical_fingerprints(monkeypatch):
    audit_module._recent_fingerprints.clear()
    monkeypatch.setenv("JARVIS_AUDIT_DATABASE_URL", "postgresql://example/db")
    monkeypatch.setattr(audit_module, "_ensure_worker", lambda: None)

    first = enqueue_activity_event(
        "event-1",
        "private_transcript_paused",
        error=True,
        error_type="VisorError",
    )
    second = enqueue_activity_event(
        "event-2",
        "private_transcript_paused",
        error=True,
        error_type="VisorError",
    )

    assert first is True
    assert second is False
    while not audit_module._queue.empty():
        audit_module._queue.get_nowait()
        audit_module._queue.task_done()
    audit_module._recent_fingerprints.clear()
