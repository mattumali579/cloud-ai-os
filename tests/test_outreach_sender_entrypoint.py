"""`outreach_sender.py selftest` from the command line. No database, no network: the mailbox and the
connection are fakes; nothing leaves the machine.

The self-test passes only when Hostinger accepted the email AND that same Message-ID is found in Sent.
"""
from __future__ import annotations

import json
from contextlib import contextmanager

import pytest

import outreach_sender
from cloudos.outreach import sender
from cloudos.outreach.transport import SendResult

FROM = "matt@fitnesshubb.test"
OWNER = "owner@example.test"


class FakeConn:
    def __init__(self):
        self.writes: list[tuple] = []

    def execute(self, sql, params=None):
        self.writes.append(params)
        return self

    def commit(self):
        pass


class FakeMailbox:
    """Stands in for Hostinger. `in_sent_after` = what the Sent folder says once the copy is filed."""

    def __init__(self, result: SendResult, in_sent_after):
        self.result, self.in_sent_after = result, in_sent_after
        self.sent, self.filed, self.looked_for = [], [], []
        self.auto_files_sent = None

    def send(self, msg):
        self.sent.append(msg)
        return self.result

    def file_in_sent(self, msg):
        self.filed.append(msg)

    def in_sent(self, message_id):
        self.looked_for.append(message_id)
        return False if len(self.looked_for) == 1 else self.in_sent_after


@pytest.fixture
def run(monkeypatch, capsys):
    cfg = sender.load_config()
    monkeypatch.setenv(cfg["sender"]["from_email_env"], FROM)
    monkeypatch.setenv(cfg["agentmail"]["owner_env"], OWNER)
    conn = FakeConn()

    @contextmanager
    def get_conn():
        yield conn

    monkeypatch.setattr(outreach_sender.db, "get_conn", get_conn)

    def _run(mailbox):
        monkeypatch.setattr(outreach_sender, "from_env", lambda cfg: mailbox)
        code = outreach_sender.main(["selftest"])
        return code, conn, capsys.readouterr().out

    return _run


def _saved(conn):
    return [json.loads(p[1]) for p in conn.writes if p and p[0] == "selftest"]


def _safe(out: str, mailbox) -> None:
    assert FROM not in out and OWNER not in out
    for mid in mailbox.looked_for:
        assert mid not in out


def test_smtp_rejection_fails_and_saves_nothing(run):
    mb = FakeMailbox(SendResult(False, "permanent", f"550 mailbox {OWNER} rejected"), in_sent_after=True)
    code, conn, out = run(mb)
    assert code != 0
    assert _saved(conn) == []
    assert mb.filed == [] and mb.looked_for == []
    assert json.loads(out)["ok"] is False and json.loads(out)["accepted"] is False
    _safe(out, mb)


@pytest.mark.parametrize("in_sent_after", [False, None])
def test_accepted_but_not_in_sent_fails_and_saves_nothing(run, in_sent_after):
    mb = FakeMailbox(SendResult(True, "sent", "250 ok"), in_sent_after=in_sent_after)
    code, conn, out = run(mb)
    assert code != 0
    assert _saved(conn) == []
    shown = json.loads(out)
    assert shown["accepted"] is True and shown["in_sent"] is False and shown["ok"] is False
    _safe(out, mb)


def test_accepted_and_in_sent_passes_and_saves_ok(run):
    mb = FakeMailbox(SendResult(True, "sent", "250 ok"), in_sent_after=True)
    code, conn, out = run(mb)
    assert code == 0
    assert len(mb.sent) == 1 and mb.sent[0]["To"] == OWNER
    saved = _saved(conn)
    assert len(saved) == 1
    assert saved[0]["ok"] is True and saved[0]["accepted"] is True and saved[0]["in_sent"] is True
    # the Message-ID that was sent is the one looked up in Sent and the one saved
    mid = mb.sent[0]["Message-ID"].strip("<>").lower()
    assert set(mb.looked_for) == {mid} and saved[0]["message_id"] == mid
    assert json.loads(out)["ok"] is True
    _safe(out, mb)


def test_self_test_goes_to_the_mailbox_itself_when_no_owner_address(run, monkeypatch):
    monkeypatch.delenv(sender.load_config()["agentmail"]["owner_env"])
    mb = FakeMailbox(SendResult(True, "sent", "250 ok"), in_sent_after=True)
    code, _, out = run(mb)
    assert code == 0 and mb.sent[0]["To"] == FROM
    _safe(out, mb)


def test_missing_credentials_still_exits_nonzero(run):
    code, conn, out = run(None)
    assert code == 3
    assert _saved(conn) == []
    assert out.startswith("BLOCKED")
