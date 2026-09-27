"""Draft creation: the step that was missing between an employee reply and a send."""
from __future__ import annotations

import json

import pytest

from cloudos.contracts import CloudOSError
from cloudos import email_outbox


FENCE = "```"


@pytest.fixture(autouse=True)
def outbox(tmp_path, monkeypatch):
    monkeypatch.setenv("EMAIL_OUTBOX_PATH", str(tmp_path))
    from cloudos import config

    config.get_settings.cache_clear() if hasattr(config.get_settings, "cache_clear") else None
    yield tmp_path


def _reply(rows) -> str:
    return f"Campaign plan first.\n\n{FENCE}json\n{json.dumps({'messages': rows})}\n{FENCE}\n\nDONE"


def test_build_from_employee_reply_creates_previewable_draft(outbox):
    rows = [{"to": "owner@example.test", "subject": "Hello", "text": "Body copy."}]
    messages = email_outbox.extract_messages(_reply(rows))
    result = email_outbox.create_draft("camp-1", messages)

    assert result["count"] == 1
    assert len(result["fingerprint"]) == 12
    stored = json.loads((outbox / "pending" / "camp-1.json").read_text(encoding="utf-8"))
    assert stored["messages"][0]["to"] == "owner@example.test"


def test_fingerprint_changes_when_the_copy_changes(outbox):
    first = email_outbox.create_draft(
        "camp-2", [{"to": "a@example.test", "subject": "S", "text": "One"}]
    )
    second = email_outbox.create_draft(
        "camp-2", [{"to": "a@example.test", "subject": "S", "text": "Two"}]
    )
    assert first["fingerprint"] != second["fingerprint"], "edited copy must invalidate the old code"


def test_recipient_cap_is_enforced(outbox):
    rows = [{"to": f"p{i}@example.test", "subject": "S", "text": "T"} for i in range(11)]
    with pytest.raises(CloudOSError):
        email_outbox.create_draft("camp-3", rows)
    assert not (outbox / "pending" / "camp-3.json").exists(), "rejected draft must not be left behind"


def test_bad_recipient_does_not_leave_a_draft_on_disk(outbox):
    with pytest.raises(CloudOSError):
        email_outbox.create_draft("camp-4", [{"to": "not-an-email", "subject": "S", "text": "T"}])
    assert not (outbox / "pending" / "camp-4.json").exists()


def test_header_injection_is_rejected(outbox):
    with pytest.raises(CloudOSError):
        email_outbox.create_draft(
            "camp-5",
            [{"to": "a@example.test", "subject": "S\nBcc: sneak@example.test", "text": "T"}],
        )


def test_reply_without_a_block_is_refused():
    with pytest.raises(CloudOSError):
        email_outbox.extract_messages("Here is some advice about emails. DONE")


def test_send_refuses_when_sending_is_disabled(outbox, monkeypatch):
    monkeypatch.setenv("EMAIL_SEND_ENABLED", "false")
    from cloudos import config

    if hasattr(config.get_settings, "cache_clear"):
        config.get_settings.cache_clear()
    result = email_outbox.create_draft(
        "camp-6", [{"to": "a@example.test", "subject": "S", "text": "T"}]
    )
    with pytest.raises(CloudOSError):
        email_outbox.send("camp-6", result["fingerprint"])


def test_send_refuses_a_wrong_confirmation_code(outbox):
    email_outbox.create_draft("camp-7", [{"to": "a@example.test", "subject": "S", "text": "T"}])
    with pytest.raises(CloudOSError):
        email_outbox.send("camp-7", "000000000000")


def test_multiline_body_with_raw_newlines_is_recovered():
    """Real email copy spans lines; a model often writes them literally."""
    broken = (
        FENCE + "json\n"
        '{"messages": [{"to": "a@example.test", "subject": "Hi",\n'
        '  "text": "Line one\n'
        "\n"
        'Line two"}]}\n' + FENCE
    )
    rows = email_outbox.extract_messages(broken)
    assert rows[0]["text"].startswith("Line one")
    assert "Line two" in rows[0]["text"]


def test_repair_does_not_invent_valid_json_from_garbage():
    with pytest.raises(CloudOSError):
        email_outbox.extract_messages(FENCE + "json\n{this is not json at all\n" + FENCE)
