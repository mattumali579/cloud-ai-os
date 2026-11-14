"""Fail-closed pause for prospect cold SMTP and google_reviews_maps_299 replenish.

No database and no network. The checked-in config stays paused. Resume is both
``campaign.enabled: true`` and ``OUTREACH_PROSPECT_SEND_ENABLED`` set to
1, true, yes, or on.
"""
from __future__ import annotations

import copy
import inspect
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest

import outreach_sender
from cloudos.outreach import review_campaign, sender
from cloudos.outreach.transport import SendResult


class Mailbox:
    def __init__(self):
        self.sent = []

    def send(self, msg):
        self.sent.append(msg)
        return SendResult(True, "sent", "250 ok")


class ReleaseConn:
    def __init__(self):
        self.sql: list[str] = []

    def execute(self, sql, params=None):
        self.sql.append(" ".join(sql.split()))
        return self

    def commit(self):
        self.sql.append("COMMIT")


def _item():
    return {
        "queue_id": 7,
        "state": "claimed",
        "step": 0,
        "attempts": 2,
        "recipient": "owner@shop.test",
        "subject": "Google reviews for Acme",
        "body": "original copy stays",
        "company_id": "cid",
        "message_id_header": "mid@test",
        "thread_root": None,
        "copy_variant": "claude-review-v1",
    }


def _pacing():
    return {
        "timezone": "America/Chicago",
        "send_days": [0, 1, 2, 3, 4, 5, 6],
        "window_start": "00:00",
        "window_end": "23:59",
        "min_gap_seconds": 0,
        "max_gap_seconds": 0,
        "cycle_limit": 30,
        "max_attempts": 3,
        "daily_limit": 100,
    }


def _paused_cfg():
    return {
        "campaign": {"id": review_campaign.CAMPAIGN, "enabled": False},
        "sender": {"from_name_default": "Matt Umali"},
        "pacing": _pacing(),
    }


def _resumed_cfg():
    cfg = _paused_cfg()
    cfg["campaign"]["enabled"] = True
    return cfg


def test_checked_in_campaign_is_paused(monkeypatch):
    monkeypatch.delenv("OUTREACH_PROSPECT_SEND_ENABLED", raising=False)
    cfg = sender.load_config()
    assert cfg["campaign"]["id"] == "google_reviews_maps_299"
    assert cfg["campaign"]["enabled"] is False
    assert sender.prospect_send_enabled(cfg) is False
    assert sender.prospect_send_enabled() is False
    yml = (sender.ROOT / ".github" / "workflows" / "outreach-send.yml").read_text(encoding="utf-8")
    assert "OUTREACH_PROSPECT_SEND_ENABLED: ${{ vars.OUTREACH_PROSPECT_SEND_ENABLED }}" in yml


@pytest.mark.parametrize("flag", ["", "false", "0", "no", "off", "truee"])
def test_env_flag_must_be_an_explicit_truthy_value(monkeypatch, flag):
    monkeypatch.setenv("OUTREACH_PROSPECT_SEND_ENABLED", flag)
    assert sender.prospect_send_enabled(_resumed_cfg()) is False


@pytest.mark.parametrize("flag", ["1", "true", "TRUE", " yes ", "on"])
def test_both_switches_resume_prospect_sends(monkeypatch, flag):
    monkeypatch.setenv("OUTREACH_PROSPECT_SEND_ENABLED", flag)
    assert sender.prospect_send_enabled(_resumed_cfg()) is True
    assert sender.prospect_send_enabled(_paused_cfg()) is False


def test_enabled_config_without_the_env_flag_stays_paused(monkeypatch):
    monkeypatch.delenv("OUTREACH_PROSPECT_SEND_ENABLED", raising=False)
    assert sender.prospect_send_enabled(_resumed_cfg()) is False


def test_send_item_releases_the_row_and_does_not_smtp_while_paused(monkeypatch):
    monkeypatch.delenv("OUTREACH_PROSPECT_SEND_ENABLED", raising=False)
    item = _item()
    conn, mb = ReleaseConn(), Mailbox()
    assert sender.send_item(conn, item, mb, _paused_cfg(), from_email="matt@fitnesshubb.test") == "paused"
    assert mb.sent == []
    assert item["body"] == "original copy stays"
    written = " ".join(conn.sql)
    assert "state = 'queued'" in written
    assert "cancelled" not in written.lower()
    assert "DELETE" not in written.upper()


def test_send_item_still_refuses_when_only_one_switch_is_on(monkeypatch):
    item = _item()
    mb = Mailbox()
    monkeypatch.setenv("OUTREACH_PROSPECT_SEND_ENABLED", "true")
    assert sender.send_item(ReleaseConn(), item, mb, _paused_cfg(), from_email="a@b.test") == "paused"
    monkeypatch.delenv("OUTREACH_PROSPECT_SEND_ENABLED", raising=False)
    assert sender.send_item(ReleaseConn(), item, mb, _resumed_cfg(), from_email="a@b.test") == "paused"
    assert mb.sent == []


def test_send_item_resumes_smtp_when_both_switches_are_on(monkeypatch):
    monkeypatch.setenv("OUTREACH_PROSPECT_SEND_ENABLED", "true")
    monkeypatch.setattr(sender, "_refusal", lambda *a, **k: None)
    monkeypatch.setattr(sender, "_file_copy", lambda *a, **k: None)
    monkeypatch.setattr(sender, "record_sent", lambda *a, **k: "mid")

    class NoWrite:
        def execute(self, *a, **k):
            raise AssertionError("resumed send cancelled or rewrote the row before SMTP")

        def commit(self):
            raise AssertionError("commit")

    mb = Mailbox()
    item = _item()
    assert sender.send_item(NoWrite(), item, mb, _resumed_cfg(), from_email="matt@fitnesshubb.test") == "sent"
    assert len(mb.sent) == 1
    assert mb.sent[0]["To"] == "owner@shop.test"
    assert item["body"] == "original copy stays"


def test_run_does_not_claim_or_send_while_paused(monkeypatch):
    monkeypatch.delenv("OUTREACH_PROSPECT_SEND_ENABLED", raising=False)

    def claim(*_a, **_k):
        raise AssertionError("claimed a queue row while paused")

    monkeypatch.setattr(sender, "claim", claim)
    mb = Mailbox()
    stats = sender.run(object(), mb, _paused_cfg(), from_email="matt@fitnesshubb.test", minutes=1, sleep=lambda _s: None)
    assert stats["paused"] is True and stats["sent"] == 0 and stats["stopped"] == sender.PAUSE_REASON
    assert mb.sent == []


def test_run_sends_again_when_resumed(monkeypatch):
    monkeypatch.setenv("OUTREACH_PROSPECT_SEND_ENABLED", "true")
    queued = _item()
    left = [queued]

    def claim(_conn, _who, *, followups=True):
        return left.pop(0) if left else None

    monkeypatch.setattr(sender, "claim", claim)
    monkeypatch.setattr(sender, "daily_cap", lambda *_a, **_k: 10)
    monkeypatch.setattr(sender, "sent_last_24h", lambda _conn: 0)
    monkeypatch.setattr(sender, "_refusal", lambda *_a, **_k: None)
    monkeypatch.setattr(sender, "_file_copy", lambda *_a, **_k: None)
    monkeypatch.setattr(sender, "record_sent", lambda *_a, **_k: "mid")
    mb = Mailbox()
    stats = sender.run(object(), mb, _resumed_cfg(), from_email="matt@fitnesshubb.test", minutes=1, sleep=lambda _s: None)
    assert stats["sent"] == 1 and stats.get("paused") is not True
    assert len(mb.sent) == 1 and mb.sent[0]["To"] == queued["recipient"]


def test_plan_does_not_replenish_any_campaign_while_paused(monkeypatch):
    monkeypatch.delenv("OUTREACH_PROSPECT_SEND_ENABLED", raising=False)
    called = []
    monkeypatch.setattr(review_campaign, "queue_approved", lambda *_a, **_k: called.append(1))

    class Conn:
        def execute(self, *_a, **_k):
            raise AssertionError("planner wrote while paused")

    for campaign_id in (review_campaign.CAMPAIGN, "meta_ads_appointments"):
        out = sender.plan(Conn(), {"campaign": {"id": campaign_id, "enabled": False}})
        assert out == {"queued": 0, "blocked": {}, "paused": True, "stopped": sender.PAUSE_REASON}
    assert called == []


def test_plan_replenishes_the_legacy_campaign_when_resumed(monkeypatch):
    monkeypatch.setenv("OUTREACH_PROSPECT_SEND_ENABLED", "true")

    def queue_approved(_conn, *, postal_address, limit=None, cfg=None):
        assert cfg["campaign"]["enabled"] is True
        assert cfg["campaign"]["id"] == review_campaign.CAMPAIGN
        return {"queued": 3, "blocked": 0}

    monkeypatch.setattr(review_campaign, "queue_approved", queue_approved)
    out = sender.plan(object(), _resumed_cfg())
    assert out["queued"] == 3 and not out.get("paused")


def test_reply_sweep_does_not_consult_the_pause():
    assert "prospect_send_enabled" not in inspect.getsource(sender.sweep)


def test_campaign_selftest_command_exits_without_opening_the_mailbox(monkeypatch):
    monkeypatch.delenv("OUTREACH_PROSPECT_SEND_ENABLED", raising=False)

    @contextmanager
    def get_conn():
        yield object()

    def from_env(_cfg):
        raise AssertionError("paused campaign self-test opened the mailbox")

    monkeypatch.setattr(outreach_sender.db, "get_conn", get_conn)
    monkeypatch.setattr(outreach_sender, "from_env", from_env)
    assert outreach_sender.main(["campaign-selftest"]) == 1


def test_campaign_selftest_refuses_before_any_write(monkeypatch):
    monkeypatch.delenv("OUTREACH_PROSPECT_SEND_ENABLED", raising=False)

    class Boom:
        def execute(self, *_a, **_k):
            raise AssertionError("campaign self-test wrote while paused")

    mb = Mailbox()
    out = outreach_sender.campaign_selftest(Boom(), sender.load_config(), mb, "owner@example.test")
    assert out["ok"] is False and out["paused"] is True and mb.sent == []


def test_campaign_selftest_passes_the_pause_when_resumed(monkeypatch):
    monkeypatch.setenv("OUTREACH_PROSPECT_SEND_ENABLED", "true")
    cfg = copy.deepcopy(sender.load_config())
    cfg["campaign"]["enabled"] = True

    class Conn:
        def execute(self, *_a, **_k):
            raise RuntimeError("past the pause")

    with pytest.raises(RuntimeError, match="past the pause"):
        outreach_sender.campaign_selftest(Conn(), cfg, Mailbox(), "owner@example.test")


def _cycle_fakes(monkeypatch, calls):
    class Conn:
        def execute(self, sql, params=None):
            return self

        def fetchone(self):
            return {"run_id": 1, "value": {"ok": True}, "updated_at": datetime.now(timezone.utc)}

        def commit(self):
            pass

        def rollback(self):
            pass

    @contextmanager
    def get_conn():
        yield Conn()

    class MB:
        password = "x"

        def __init__(self):
            self.sent = []

        def check_login(self):
            calls.append("login")

        def close(self):
            calls.append("close")

        def send(self, msg):
            self.sent.append(msg)
            raise AssertionError("cycle SMTP")

    mb = MB()
    monkeypatch.setattr(outreach_sender.db, "get_conn", get_conn)
    monkeypatch.setattr(outreach_sender, "from_env", lambda cfg: mb)
    monkeypatch.setattr(outreach_sender.sender, "sweep", lambda *_a, **_k: calls.append("sweep") or {"cancelled": 1, "rows": []})
    monkeypatch.setattr(outreach_sender.sender, "plan", lambda *_a, **_k: calls.append("plan") or {"queued": 0, "paused": True})
    monkeypatch.setattr(outreach_sender.sender, "recover", lambda *_a, **_k: calls.append("recover") or {"ambiguous": 0})
    monkeypatch.setattr(outreach_sender.replies, "poll", lambda *_a, **_k: calls.append("replies") or {"skipped_errors": 0})
    monkeypatch.setattr(outreach_sender, "selftest", lambda *_a, **_k: calls.append("selftest") or {"ok": True})
    monkeypatch.setattr(outreach_sender.sender, "run", lambda *_a, **_k: calls.append("run") or {"sent": 1, "ambiguous": 0, "auth_failed": False})
    monkeypatch.setattr(outreach_sender.status, "funnel", lambda *_a, **_k: {"blocking": []})
    monkeypatch.setattr(outreach_sender.status, "planner_state", lambda *_a, **_k: {})
    monkeypatch.setattr(outreach_sender.status, "events", lambda *_a, **_k: {})
    monkeypatch.setattr(outreach_sender.status, "digests", lambda *_a, **_k: {})
    monkeypatch.setattr(outreach_sender.agentmail, "flush", lambda *_a, **_k: {})
    monkeypatch.setattr(outreach_sender.notify, "flush", lambda *_a, **_k: None)
    return mb


def test_cycle_keeps_reply_handling_and_skips_smtp_while_paused(monkeypatch):
    monkeypatch.delenv("OUTREACH_PROSPECT_SEND_ENABLED", raising=False)
    calls: list[str] = []
    mb = _cycle_fakes(monkeypatch, calls)
    out = outreach_sender.cycle(1)
    assert calls.count("sweep") == 2
    assert "replies" in calls and "recover" in calls and "plan" in calls and "login" in calls
    assert "selftest" not in calls and "run" not in calls
    assert out["send"]["paused"] is True and out["send"]["sent"] == 0
    assert mb.sent == []


def test_cycle_sends_again_when_resumed(monkeypatch):
    monkeypatch.setenv("OUTREACH_PROSPECT_SEND_ENABLED", "true")
    real = copy.deepcopy(sender.load_config())
    real["campaign"]["enabled"] = True
    monkeypatch.setattr(outreach_sender.sender, "load_config", lambda path=sender.CONFIG: real)
    calls: list[str] = []
    _cycle_fakes(monkeypatch, calls)
    out = outreach_sender.cycle(1)
    assert "replies" in calls and "run" in calls
    assert out["send"]["sent"] == 1 and out["send"].get("paused") is not True
