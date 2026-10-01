"""Credential-status blockers, with no database: a fake connection answers every count
with 0 and every other lookup with nothing, so only the environment decides the result."""
from __future__ import annotations

import pytest

from cloudos.outreach import status

HARD = "Hostinger mailbox password not saved yet - emails are written and waiting, none can go out"


class _Rows:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row

    def fetchall(self):
        return []


class FakeConn:
    def execute(self, sql, params=None):
        return _Rows({"n": 0} if "count(" in sql else None)


def _run(monkeypatch, *, github_actions, email="", password=""):
    """-> (Hostinger blockers, the planner's decision) for one environment."""
    monkeypatch.setenv("HOSTINGER_EMAIL", email)
    monkeypatch.setenv("HOSTINGER_EMAIL_PASSWORD", password)
    if github_actions is None:
        monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    else:
        monkeypatch.setenv("GITHUB_ACTIONS", github_actions)
    conn = FakeConn()
    f = status.funnel(conn, cap=15)
    decision = status.planner_state(conn, f)["next_action"]["decision"]
    return [b for b in f["blocking"] if b.startswith("Hostinger")], decision


@pytest.mark.parametrize("github_actions", [None, "", "false", "False"])
def test_hostinger_blocker_is_hidden_outside_github_actions(monkeypatch, github_actions):
    hard, decision = _run(monkeypatch, github_actions=github_actions)
    assert hard == []
    assert decision != "human_review"


@pytest.mark.parametrize("email,password", [("", ""), ("hi@brightreach.test", ""), ("", "pw")])
def test_hostinger_blocker_stays_hard_in_github_actions_when_a_credential_is_missing(monkeypatch, email, password):
    hard, decision = _run(monkeypatch, github_actions="true", email=email, password=password)
    assert hard == [HARD]
    assert decision == "human_review"


def test_hostinger_blocker_clears_in_github_actions_with_both_credentials(monkeypatch):
    hard, decision = _run(monkeypatch, github_actions="true", email="hi@brightreach.test", password="pw")
    assert hard == []
    assert decision != "human_review"


# ------------------------------------------------- manager reply alerts (no database)
class _ReplyRows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class ReplyConn:
    """Hands events() its reply rows and records the query it asked for."""

    def __init__(self, rows):
        self.rows, self.sql = rows, []

    def execute(self, sql, params=None):
        self.sql.append(sql)
        return _ReplyRows(self.rows)


def _reply_row(label="INTERESTED", **draft):
    return {"mid": "m-1", "classification": label, "interpretation": "They want details.",
            "recommended_action": "Answer their question.", "company_name": "Example Plumbing Co",
            "sender": "owner@example.test", "body": "Sounds good, tell me more.", "cid": "c-1",
            "draft_subject": None, "draft_body": None, **draft}


def _alerts(monkeypatch, rows):
    """-> (manager notices, every notice, the SQL events() ran), with delivery faked out."""
    sent = []

    def fake_notify(conn, cfg, **kw):
        sent.append(kw)
        return {"created": True}

    monkeypatch.setattr(status.agentmail, "notify", fake_notify)
    conn = ReplyConn(rows)
    status.events(conn, {}, discord="discord-fallback")
    return [s for s in sent if s["role"] == "manager"], sent, conn.sql[0]


def test_manager_alert_includes_the_pending_draft_and_still_requires_approval(monkeypatch):
    row = _reply_row(draft_subject="Re: your website", draft_body="Hi Sam,\n\nHappy to walk you through it.")
    manager, sent, sql = _alerts(monkeypatch, [row])
    assert len(manager) == 1
    text = manager[0]["text"]
    assert "Subject: Re: your website" in text
    assert "Hi Sam, Happy to walk you through it." in text
    assert "approval is required" in text and "NOT sent" in text
    assert "No safe draft" not in text
    # dedupe key, severity and the Discord fallback are untouched; the follow-up notice still goes out
    assert manager[0]["dedupe_key"] == "reply:m-1" and manager[0]["severity"] == "high"
    assert manager[0]["discord"] == "discord-fallback"
    assert [s["dedupe_key"] for s in sent if s["role"] == "followup"] == ["seq:m-1"]
    # only a pending draft written for this company and this exact reply is ever picked up
    assert "company_id = m.company_id AND based_on_message_id = m.message_id" in sql
    assert "state IN ('awaiting_approval','approved')" in sql


@pytest.mark.parametrize("label", ["INTERESTED", "NEEDS_REVIEW", "MEETING_REQUEST", "OBJECTION"])
@pytest.mark.parametrize("draft", [{}, {"draft_subject": "Re: hello", "draft_body": "   "}])
def test_manager_alert_without_a_pending_draft_says_it_must_be_written_by_hand(monkeypatch, label, draft):
    manager, _, _ = _alerts(monkeypatch, [_reply_row(label, **draft)])
    text = manager[0]["text"]
    assert "No safe draft was prepared" in text and "written by hand" in text
    assert "Suggested reply" not in text and "in the drafts" not in text


def test_manager_alert_keeps_a_long_draft_short(monkeypatch):
    manager, _, _ = _alerts(monkeypatch, [_reply_row(draft_subject="S" * 400, draft_body="word " * 1000)])
    note = manager[0]["text"].split("Automatic follow-ups to them are stopped.\n\n")[1]
    assert len(note) < 900 and note.endswith("...")
