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
    """Stands in for Hostinger. `in_sent_after` = what the Sent folder says once the copy is filed.
    `lag` = how many looks after filing still answer `while_lagging` before Sent catches up."""

    def __init__(self, result: SendResult, in_sent_after, lag: int = 0, while_lagging=False):
        self.result, self.in_sent_after = result, in_sent_after
        self.lag, self.while_lagging = lag, while_lagging
        self.sent, self.filed, self.looked_for = [], [], []
        self.auto_files_sent = None

    def send(self, msg):
        self.sent.append(msg)
        return self.result

    def file_in_sent(self, msg):
        self.filed.append(msg)

    def in_sent(self, message_id):
        self.looked_for.append(message_id)
        if len(self.looked_for) == 1:
            return False
        return self.while_lagging if len(self.looked_for) - 1 <= self.lag else self.in_sent_after


@pytest.fixture
def run(monkeypatch, capsys):
    cfg = sender.load_config()
    monkeypatch.setenv(cfg["sender"]["from_email_env"], FROM)
    monkeypatch.setenv(cfg["agentmail"]["owner_env"], OWNER)
    conn = FakeConn()
    waits: list[float] = []
    monkeypatch.setattr(outreach_sender, "_sleep", waits.append)   # no real waiting in tests

    @contextmanager
    def get_conn():
        yield conn

    monkeypatch.setattr(outreach_sender.db, "get_conn", get_conn)

    def _run(mailbox):
        monkeypatch.setattr(outreach_sender, "from_env", lambda cfg: mailbox)
        code = outreach_sender.main(["selftest"])
        return code, conn, capsys.readouterr().out

    _run.waits = waits
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
    # one email only; Sent was re-checked the bounded number of times (plus the one look before filing)
    assert len(mb.sent) == 1 and len(mb.filed) == 1
    assert len(mb.looked_for) == 1 + outreach_sender.SELFTEST_SENT_CHECKS
    assert len(set(mb.looked_for)) == 1
    assert run.waits == [outreach_sender.SELFTEST_SENT_WAIT_S] * (outreach_sender.SELFTEST_SENT_CHECKS - 1)
    _safe(out, mb)


@pytest.mark.parametrize("while_lagging", [False, None])
def test_sent_copy_showing_up_late_passes_without_a_second_send(run, while_lagging):
    mb = FakeMailbox(SendResult(True, "sent", "250 ok"), in_sent_after=True, lag=3, while_lagging=while_lagging)
    code, conn, out = run(mb)
    assert code == 0
    assert len(mb.sent) == 1 and len(mb.filed) == 1          # re-checks only: no second email, no second copy
    mid = mb.sent[0]["Message-ID"].strip("<>").lower()
    assert mb.looked_for == [mid] * 5                        # before filing, 3 lagging looks, then found
    assert run.waits == [outreach_sender.SELFTEST_SENT_WAIT_S] * 3
    saved = _saved(conn)
    assert len(saved) == 1 and saved[0]["ok"] is True and saved[0]["in_sent"] is True
    assert saved[0]["message_id"] == mid
    assert json.loads(out)["ok"] is True
    _safe(out, mb)


def test_recheck_budget_is_short_enough_for_a_ci_job():
    assert outreach_sender.SELFTEST_SENT_CHECKS >= 2
    assert (outreach_sender.SELFTEST_SENT_CHECKS - 1) * outreach_sender.SELFTEST_SENT_WAIT_S <= 60


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
    assert run.waits == []                                   # found on the first look: no waiting
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


# ------------------------------------------------- `outreach_sender.py agentmail-selftest`
KEY = "am_test_key_do_not_print"


class FakeNoticeConn:
    """Stands in for the `notifications` table: one row per dedupe_key, delivery written back by UPDATE."""

    def __init__(self):
        self.rows: dict[str, dict] = {}
        self._row = None

    def execute(self, sql, params=None):
        self._row = None
        if sql.startswith("INSERT INTO notifications"):
            key = params[4]
            if key not in self.rows:
                self.rows[key] = {"id": len(self.rows) + 1, "code": params[1], "meta": json.loads(params[3]),
                                  "delivered": False, "receipt": None, "attempts": 0}
                self._row = {"id": self.rows[key]["id"]}
        elif sql.startswith("UPDATE notifications"):
            row = next(r for r in self.rows.values() if r["id"] == params[3])
            row.update(delivered=params[0], receipt=params[2], attempts=row["attempts"] + 1)
        return self

    def fetchone(self):
        return self._row

    def commit(self):
        pass


@pytest.fixture
def am(monkeypatch, capsys):
    cfg = sender.load_config()
    monkeypatch.setenv(cfg["agentmail"]["owner_env"], OWNER)
    monkeypatch.setenv(cfg["agentmail"]["api_env"], KEY)
    conn = FakeNoticeConn()
    posts: list[tuple] = []

    @contextmanager
    def get_conn():
        yield conn

    monkeypatch.setattr(outreach_sender.db, "get_conn", get_conn)

    def _run(status_code=200):
        class Resp:
            def json(self):
                return {"message_id": "am-msg-1"}

        Resp.status_code = status_code

        def fake_post(url, json=None, timeout=None, headers=None):
            posts.append((url, json, headers))
            return Resp()

        monkeypatch.setattr(outreach_sender.agentmail.httpx, "post", fake_post)   # nothing leaves the machine
        code = outreach_sender.main(["agentmail-selftest"])
        out = capsys.readouterr().out
        assert KEY not in out and OWNER not in out and FROM not in out
        return code, json.loads(out)

    _run.conn, _run.posts, _run.cfg = conn, posts, cfg
    return _run


def test_agentmail_selftest_accepted_exits_zero_and_records_delivery(am):
    code, shown = am(200)
    assert code == 0 and shown["ok"] is True and shown["delivered"] is True
    # one message, from the configured manager inbox, to the owner only, through the existing AgentMail path
    assert len(am.posts) == 1
    url, payload, headers = am.posts[0]
    assert url.endswith(f"/inboxes/{am.cfg['agentmail']['inboxes']['manager']}/messages/send")
    assert payload["to"] == [OWNER] and headers["Authorization"] == f"Bearer {KEY}"
    (key, row), = am.conn.rows.items()
    assert key.startswith("am:selftest:") and row["code"] == "agentmail.manager"
    assert row["delivered"] is True and row["receipt"].startswith("agentmail am-msg-1") and row["attempts"] == 1


def test_agentmail_selftest_missing_key_exits_nonzero_and_queues_nothing(am, monkeypatch):
    monkeypatch.delenv(am.cfg["agentmail"]["api_env"])
    code, shown = am(200)
    assert code == 3 and shown["ok"] is False and "blocked" in shown
    assert am.posts == [] and am.conn.rows == {}


def test_agentmail_selftest_provider_rejection_exits_nonzero_and_is_not_delivered(am):
    code, shown = am(403)
    assert code == 1 and shown["ok"] is False and shown["delivered"] is False
    assert len(am.posts) == 1
    (row,) = am.conn.rows.values()
    assert row["delivered"] is False and row["receipt"] == "agentmail HTTP 403"


def test_agentmail_selftest_twice_sends_once_and_second_run_is_not_a_success(am):
    assert am(200)[0] == 0
    code, shown = am(200)
    assert code == 1 and shown == {"ok": False, "duplicate": True}
    assert len(am.posts) == 1 and len(am.conn.rows) == 1          # no second email, no second row


def test_workflow_has_a_separate_agentmail_selftest_mode():
    yml = (outreach_sender.ROOT / ".github" / "workflows" / "outreach-send.yml").read_text(encoding="utf-8")
    assert "options: [cycle, status, selftest, agentmail-selftest]" in yml
    assert "            agentmail-selftest) python outreach_sender.py agentmail-selftest ;;" in yml
    assert "            selftest) python outreach_sender.py selftest ;;" in yml
