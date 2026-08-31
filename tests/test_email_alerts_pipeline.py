"""Tests for the parts around the filter: parsing, dedup state, delivery.

No network. The IMAP and Discord boundaries are exercised through their pure
helpers and a stubbed transport, so a broken payload shape or a dedup
regression is caught here rather than by a missing phone notification.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from cloudos.email_alerts import mailbox, notifier
from cloudos.email_alerts.filter import classify
from cloudos.email_alerts.main import load_state, save_state


# --------------------------------------------------------------- parsing ---

def test_strip_html_removes_markup_and_scripts():
    raw = "<div>Homework <script>alert(1)</script><b>5</b> is&nbsp;due</div>"
    assert mailbox._strip_html(raw) == "Homework 5 is due"


def test_decode_handles_encoded_subject():
    encoded = "=?utf-8?B?SG9tZXdvcmsgNSBpcyBkdWU=?="
    assert mailbox._decode(encoded) == "Homework 5 is due"


def test_decode_survives_garbage():
    assert mailbox._decode(None) == ""
    assert "x" in mailbox._decode("x")


def test_parse_date_returns_utc():
    parsed = mailbox._parse_date("Sat, 30 Aug 2026 15:22:06 -0500")
    assert parsed is not None
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timedelta(0)


def test_parse_date_on_junk_is_none():
    assert mailbox._parse_date("not a date") is None


def test_body_snippet_prefers_plain_text():
    import email

    raw = (
        "Content-Type: multipart/alternative; boundary=b\r\n\r\n"
        "--b\r\nContent-Type: text/plain\r\n\r\nPlain body wins\r\n"
        "--b\r\nContent-Type: text/html\r\n\r\n<p>HTML loses</p>\r\n--b--\r\n"
    )
    msg = email.message_from_string(raw, policy=email.policy.default)
    assert "Plain body wins" in mailbox._body_snippet(msg)


def test_body_snippet_falls_back_to_html():
    import email

    raw = "Content-Type: text/html\r\n\r\n<p>Only <b>HTML</b> here</p>"
    msg = email.message_from_string(raw, policy=email.policy.default)
    assert mailbox._body_snippet(msg) == "Only HTML here"


# ----------------------------------------------------------- dedup state ---

def test_state_round_trips(tmp_path):
    path = tmp_path / "seen.json"
    now = datetime.now(timezone.utc).isoformat()
    save_state(path, {"seen": {"<a@x>": now}})
    assert load_state(path)["seen"] == {"<a@x>": now}


def test_state_prunes_entries_older_than_the_window(tmp_path):
    path = tmp_path / "seen.json"
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    fresh = datetime.now(timezone.utc).isoformat()
    save_state(path, {"seen": {"<old@x>": old, "<new@x>": fresh}})
    seen = load_state(path)["seen"]
    assert "<old@x>" not in seen
    assert "<new@x>" in seen


def test_corrupt_state_file_does_not_crash(tmp_path):
    path = tmp_path / "seen.json"
    path.write_text("{ this is not json", encoding="utf-8")
    assert load_state(path) == {"seen": {}}


def test_missing_state_file_starts_empty(tmp_path):
    assert load_state(tmp_path / "nope.json") == {"seen": {}}


# -------------------------------------------------------------- delivery ---

def test_relay_is_preferred_over_direct_webhook(monkeypatch):
    monkeypatch.setenv("CLOUDOS_NOTIFY_URL", "https://worker.example/notify")
    monkeypatch.setenv("CLOUDOS_NOTIFY_TOKEN", "tok")
    url, headers, kind = notifier.resolve_destination("https://discord.example/hook")
    assert kind == "worker-relay"
    assert url == "https://worker.example/notify"
    assert headers["Authorization"] == "Bearer tok"


def test_direct_webhook_used_when_relay_incomplete(monkeypatch):
    monkeypatch.setenv("CLOUDOS_NOTIFY_URL", "https://worker.example/notify")
    monkeypatch.delenv("CLOUDOS_NOTIFY_TOKEN", raising=False)
    url, headers, kind = notifier.resolve_destination("https://discord.example/hook")
    assert kind == "direct-webhook"
    assert url == "https://discord.example/hook"
    assert "Authorization" not in headers


def test_no_destination_at_all(monkeypatch):
    monkeypatch.delenv("CLOUDOS_NOTIFY_URL", raising=False)
    monkeypatch.delenv("CLOUDOS_NOTIFY_TOKEN", raising=False)
    assert notifier.resolve_destination("")[0] == ""


def _sample_message():
    return mailbox.Message(
        uid="1",
        message_id="<m@x>",
        subject="Nathan Lott paid you $35.00",
        sender="venmo@venmo.com",
        received=datetime.now(timezone.utc),
        snippet="Nathan Lott paid you $35.00. Money credited to your Venmo account.",
        headers={},
    )


def test_alert_payload_shape(monkeypatch):
    captured = {}

    def fake_post(url, payload):
        captured["payload"] = payload
        return 204

    monkeypatch.setattr(notifier, "_post", fake_post)
    message = _sample_message()
    result = classify(message.subject, message.snippet, message.sender, {}, ())
    assert notifier.send_email_alert("https://discord.example/hook", message, result)

    embed = captured["payload"]["embeds"][0]
    assert "Nathan Lott paid you" in embed["title"]
    assert embed["color"] == notifier.COLOR[result.urgency]
    names = [f["name"] for f in embed["fields"]]
    assert names == ["From", "Why you're seeing this"]
    assert "money" in embed["footer"]["text"]
    # Discord rejects an embed title over 256 chars.
    assert len(embed["title"]) <= 256


def test_delivery_failure_is_swallowed(monkeypatch):
    def boom(url, payload):
        raise OSError("network down")

    monkeypatch.setattr(notifier, "_post", boom)
    message = _sample_message()
    result = classify(message.subject, message.snippet, message.sender, {}, ())
    # Must return False, not raise: one dead webhook cannot abort the whole run.
    assert notifier.send_email_alert("https://discord.example/hook", message, result) is False


def test_long_subject_is_truncated_for_discord(monkeypatch):
    captured = {}
    monkeypatch.setattr(notifier, "_post",
                        lambda url, payload: captured.setdefault("p", payload) and 204 or 204)
    message = _sample_message()
    message.subject = "A" * 900
    result = classify("payment received", "payment received", "bank@x.example", {}, ())
    notifier.send_email_alert("https://discord.example/hook", message, result)
    assert len(captured["p"]["embeds"][0]["title"]) <= 256
