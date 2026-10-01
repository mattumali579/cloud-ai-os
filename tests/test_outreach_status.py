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
