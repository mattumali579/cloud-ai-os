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
    fingerprint = email_outbox.preview("launch")["fingerprint"]

    result = email_outbox.send("launch", fingerprint)

    assert result["sent_count"] == 2
    assert sent == ["person0@example.com", "person1@example.com"]
    with pytest.raises(CloudOSError):
        email_outbox.send("launch", fingerprint)
    reset_settings_cache()
