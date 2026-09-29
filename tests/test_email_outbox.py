import json
from pathlib import Path

import pytest

from cloudos import email_outbox
from cloudos.config import reset_settings_cache
from cloudos.contracts import CloudOSError, ErrorCode


def _write_draft(root: Path, draft_id: str, count: int = 2) -> None:
    pending = root / "pending"
    pending.mkdir(parents=True)
    rows = [
        {"to": f"person{i}@example.com", "subject": f"Hello {i}", "text": f"Body {i}"}
        for i in range(count)
    ]
    (pending / f"{draft_id}.json").write_text(
        json.dumps({"messages": rows}), encoding="utf-8"
    )


def test_preview_returns_recipients_and_stable_fingerprint(monkeypatch, tmp_path):
    _write_draft(tmp_path, "launch", 2)
    monkeypatch.setenv("EMAIL_OUTBOX_PATH", str(tmp_path))
    reset_settings_cache()

    result = email_outbox.preview("launch")

    assert result["count"] == 2
    assert len(result["fingerprint"]) == 12
    assert result["messages"][0]["to"] == "person0@example.com"
    reset_settings_cache()


def test_more_than_ten_recipients_is_rejected(monkeypatch, tmp_path):
    _write_draft(tmp_path, "too-many", 11)
    monkeypatch.setenv("EMAIL_OUTBOX_PATH", str(tmp_path))
    reset_settings_cache()
    with pytest.raises(CloudOSError) as error:
        email_outbox.preview("too-many")
    assert error.value.code is ErrorCode.VALIDATION_ERROR
    reset_settings_cache()


def test_send_needs_matching_preview_fingerprint(monkeypatch, tmp_path):
    _write_draft(tmp_path, "launch", 1)
    monkeypatch.setenv("EMAIL_OUTBOX_PATH", str(tmp_path))
    monkeypatch.setenv("EMAIL_SEND_ENABLED", "true")
    reset_settings_cache()
    with pytest.raises(CloudOSError) as error:
        email_outbox.send("launch", "000000000000")
    assert error.value.code is ErrorCode.VALIDATION_ERROR
    reset_settings_cache()


def test_send_uses_hostinger_smtp_and_cannot_repeat(monkeypatch, tmp_path):
    _write_draft(tmp_path, "launch", 2)
    monkeypatch.setenv("EMAIL_OUTBOX_PATH", str(tmp_path))
    monkeypatch.setenv("EMAIL_SEND_ENABLED", "true")
    monkeypatch.setenv("HOSTINGER_SMTP_USERNAME", "sender@example.com")
    monkeypatch.setenv("HOSTINGER_SMTP_PASSWORD", "secret-password")
    # Pin the port: the transport is chosen from it, so leaving it to whatever
    # the machine's .env happens to say made this test depend on the machine.
    monkeypatch.setenv("HOSTINGER_SMTP_PORT", "465")
    reset_settings_cache()
    sent = []

    class FakeSMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def login(self, username, password):
            assert username == "sender@example.com"
            assert password == "secret-password"

        def send_message(self, message):
            sent.append(message["To"])

    monkeypatch.setattr(email_outbox.smtplib, "SMTP_SSL", FakeSMTP)
    # the outreach guard needs the leads database; here it clears everyone (guard tests are below)
    monkeypatch.setattr(email_outbox, "_guard", lambda email, kind: {"allowed": True, "reasons": []})
    fingerprint = email_outbox.preview("launch")["fingerprint"]

    result = email_outbox.send("launch", fingerprint)

    assert result["sent_count"] == 2
    assert sent == ["person0@example.com", "person1@example.com"]
    with pytest.raises(CloudOSError):
        email_outbox.send("launch", fingerprint)
    reset_settings_cache()


# ------------------------------------------------ outreach send guard (regression 2026-09-29)
def _smtp_env(monkeypatch, tmp_path, count=2, rows=None):
    pending = tmp_path / "pending"
    pending.mkdir(parents=True)
    rows = rows or [{"to": f"person{i}@example.com", "subject": f"Hello {i}", "text": f"Body {i}"} for i in range(count)]
    (pending / "g.json").write_text(json.dumps({"messages": rows}), encoding="utf-8")
    monkeypatch.setenv("EMAIL_OUTBOX_PATH", str(tmp_path))
    monkeypatch.setenv("EMAIL_SEND_ENABLED", "true")
    monkeypatch.setenv("HOSTINGER_SMTP_USERNAME", "sender@example.com")
    monkeypatch.setenv("HOSTINGER_SMTP_PASSWORD", "secret-password")
    monkeypatch.setenv("HOSTINGER_SMTP_PORT", "465")
    monkeypatch.setenv("EMAIL_FROM_ADDRESS", "owner@example.org")
    monkeypatch.delenv("EMAIL_OWNER_ALIASES", raising=False)
    monkeypatch.delenv("EMAIL_ADDRESS", raising=False)
    reset_settings_cache()
    sent: list[str] = []
    opened: list[bool] = []

    class FakeSMTP:
        def __init__(self, *args, **kwargs):
            opened.append(True)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def login(self, username, password):
            pass

        def send_message(self, message):
            sent.append(message["To"])

    monkeypatch.setattr(email_outbox.smtplib, "SMTP_SSL", FakeSMTP)
    return sent, opened


def test_guard_refusal_sends_nothing(monkeypatch, tmp_path):
    sent, opened = _smtp_env(monkeypatch, tmp_path)
    calls = []

    def guard(email, kind):
        calls.append((email, kind))
        return {"allowed": email != "person1@example.com", "reasons": ["email_suppressed"]}

    monkeypatch.setattr(email_outbox, "_guard", guard)
    fp = email_outbox.preview("g")["fingerprint"]
    with pytest.raises(CloudOSError) as err:
        email_outbox.send("g", fp)
    assert "email_suppressed" in str(err.value) and sent == [] and opened == []   # SMTP never even opened
    assert ("person1@example.com", "cold") in calls
    assert not (tmp_path / "sent").exists()            # not burned: can be sent once the list is fixed
    reset_settings_cache()


def test_guard_database_unreachable_fails_closed(monkeypatch, tmp_path):
    """The real guard with no leads database configured must refuse, never wave the send through."""
    from cloudos import db

    sent, opened = _smtp_env(monkeypatch, tmp_path, count=1)

    def unreachable():
        raise OSError("connection refused")      # never a real database from a unit test

    monkeypatch.setattr(db, "get_conn", unreachable)
    fp = email_outbox.preview("g")["fingerprint"]
    with pytest.raises(CloudOSError) as err:
        email_outbox.send("g", fp)
    assert "guard_unreachable" in str(err.value) and sent == [] and opened == []
    reset_settings_cache()


def test_guard_error_answer_is_a_refusal(monkeypatch):
    from cloudos.conversations import guard

    class Boom:
        def __enter__(self):
            raise OSError("db down")

        def __exit__(self, *a):
            return False

    assert guard.outreach_allowed("a@example.com", "cold", connect=Boom)["allowed"] is False

    class Conn:
        def execute(self, *a, **k):
            raise RuntimeError("function missing")

        def rollback(self):
            pass

    r = guard.check_fail_closed(Conn(), "a@example.com", "cold")
    assert r["allowed"] is False and r["reasons"] == ["guard_error"]
    monkeypatch.setattr(guard.store, "send_check", lambda *a, **k: {"allowed": "yes"})   # not a literal True
    assert guard.check_fail_closed(Conn(), "a@example.com", "cold")["allowed"] is False


def test_owner_own_address_skips_the_guard(monkeypatch, tmp_path):
    rows = [{"to": "owner@example.org", "subject": "Alert", "text": "Your daily alert"}]
    sent, _ = _smtp_env(monkeypatch, tmp_path, rows=rows)

    def guard(email, kind):
        raise AssertionError("the owner's own mail must not need the leads database")

    monkeypatch.setattr(email_outbox, "_guard", guard)
    fp = email_outbox.preview("g")["fingerprint"]
    assert email_outbox.send("g", fp)["sent_count"] == 1 and sent == ["owner@example.org"]
    reset_settings_cache()


def test_guard_rechecks_each_recipient_right_before_sending(monkeypatch, tmp_path):
    sent, _ = _smtp_env(monkeypatch, tmp_path)
    seen: dict = {}

    def guard(email, kind):
        seen[email] = seen.get(email, 0) + 1
        # person1 unsubscribes between the pre-flight check and its own send
        return {"allowed": not (email == "person1@example.com" and seen[email] > 1), "reasons": ["email_suppressed"]}

    monkeypatch.setattr(email_outbox, "_guard", guard)
    fp = email_outbox.preview("g")["fingerprint"]
    with pytest.raises(CloudOSError):
        email_outbox.send("g", fp)
    assert sent == ["person0@example.com"]
    receipt = json.loads(next((tmp_path / "sent").glob("*.json")).read_text(encoding="utf-8"))
    assert receipt["status"] == "stopped_by_guard" and receipt["sent"] == ["person0@example.com"]
    reset_settings_cache()


def test_draft_kind_is_validated_and_default_keeps_old_fingerprints(monkeypatch, tmp_path):
    _write_draft(tmp_path, "k", 1)
    monkeypatch.setenv("EMAIL_OUTBOX_PATH", str(tmp_path))
    reset_settings_cache()
    before = email_outbox.preview("k")["fingerprint"]
    path = tmp_path / "pending" / "k.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["messages"][0]["kind"] = "cold"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert email_outbox.preview("k")["fingerprint"] == before
    data["messages"][0]["kind"] = "followup"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert email_outbox.preview("k")["fingerprint"] != before
    data["messages"][0]["kind"] = "blast"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(CloudOSError):
        email_outbox.preview("k")
    reset_settings_cache()
