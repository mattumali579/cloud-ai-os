"""Tests for the parts around the filter: parsing, dedup state, delivery.

No network. The IMAP and Discord boundaries are exercised through their pure
helpers and a stubbed transport, so a broken payload shape or a dedup
regression is caught here rather than by a missing phone notification.
"""
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


# ------------------------------------------------- end-to-end run() loop ---

def _msg(uid, mid, subject, sender, snippet):
    return mailbox.Message(
        uid=uid, message_id=mid, subject=subject, sender=sender,
        received=datetime.now(timezone.utc), snippet=snippet, headers={},
    )


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """run() with a fake mailbox and a recording Discord transport."""
    from cloudos.email_alerts import main as main_mod

    sent = []
    monkeypatch.setattr(notifier, "_post",
                        lambda url, payload: (sent.append(payload), 204)[1])
    monkeypatch.setattr(main_mod, "STATE_PATH", tmp_path / "seen.json")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.example/hook")
    monkeypatch.delenv("CLOUDOS_NOTIFY_URL", raising=False)
    monkeypatch.delenv("CLOUDOS_NOTIFY_TOKEN", raising=False)
    monkeypatch.setenv("EMAIL_ADDRESS", "matt.umali579@gmail.com")
    monkeypatch.setenv("EMAIL_APP_PASSWORD", "not-a-real-password")
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.setattr("sys.argv", ["email-alerts"])
    return main_mod, sent


INBOX = [
    _msg("1", "<pay@x>", "Nathan Lott paid you $35.00", "venmo@venmo.com",
         "Nathan Lott paid you $35.00. Money credited to your Venmo account."),
    _msg("2", "<news@x>", "The Best Books of September",
         "barnesandnoble@e.barnesandnoble.com", "Fiction, nonfiction, YA."),
]


def test_run_alerts_once_and_never_again(wired, monkeypatch):
    main_mod, sent = wired
    monkeypatch.setattr("cloudos.email_alerts.mailbox.fetch_recent",
                        lambda *a, **k: list(INBOX))

    assert main_mod.run() == 0
    assert len(sent) == 1, "only the payment should have alerted"
    assert "Nathan Lott" in sent[0]["embeds"][0]["title"]

    # Same mailbox, second run: the dedup file must suppress a repeat.
    assert main_mod.run() == 0
    assert len(sent) == 1, "the same email was sent twice"


def test_run_reports_a_dead_mailbox_instead_of_going_quiet(wired, monkeypatch):
    main_mod, sent = wired

    def boom(*a, **k):
        raise OSError("[AUTHENTICATIONFAILED] Invalid credentials")

    monkeypatch.setattr("cloudos.email_alerts.mailbox.fetch_recent", boom)
    # Exit code 1 is what makes the workflow step fail and fire the Discord
    # "watcher failed" notice. Returning 0 here would be a silent outage.
    assert main_mod.run() == 1


def test_dry_run_sends_nothing(wired, monkeypatch):
    main_mod, sent = wired
    monkeypatch.setattr("cloudos.email_alerts.mailbox.fetch_recent",
                        lambda *a, **k: list(INBOX))
    monkeypatch.setattr("sys.argv", ["email-alerts", "--dry-run"])
    assert main_mod.run() == 0
    assert sent == []


def test_missing_credentials_fail_loudly(wired, monkeypatch):
    main_mod, _sent = wired
    monkeypatch.setenv("EMAIL_APP_PASSWORD", "")
    assert main_mod.run() == 1


class TestDeliveryReceipt:
    """The receipt is the only durable evidence that Discord created a message.

    It must never be the reason a delivered alert is reported as failed, so
    every malformed shape has to degrade to an empty string rather than raise.
    """

    def test_relay_receipt_yields_discord_message_id(self):
        from cloudos.email_alerts.notifier import _receipt_detail

        body = b'{"sent": true, "status": 200, "discord_message_id": "141"}'
        assert _receipt_detail(body) == " discord_message_id=141"

    def test_direct_webhook_receipt_uses_discord_id_field(self):
        from cloudos.email_alerts.notifier import _receipt_detail

        assert _receipt_detail(b'{"id": "992", "type": 0}') == " discord_message_id=992"

    @pytest.mark.parametrize(
        "body",
        [
            b"",                        # 204 No Content
            b"not json at all",         # HTML error page
            b"[1, 2, 3]",               # JSON, but not an object
            b"{}",                      # object with no id
            b'{"discord_message_id": null}',
            b"\xff\xfe\x00garbage",     # undecodable bytes
        ],
    )
    def test_unusable_receipts_degrade_to_empty_string(self, body):
        from cloudos.email_alerts.notifier import _receipt_detail

        assert _receipt_detail(body) == ""
