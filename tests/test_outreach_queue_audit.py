"""`outreach_sender.py queue-audit`: how many prepared first emails would pass every send-time check.

No database, no network: the connection is a fake that answers the exact queries the real guard
(guard.check_fail_closed), the real duplicate-business check (sender._duplicate_in_queue) and the real
copy QA (copy.qa) make. It refuses anything that is not a SELECT, so a write would fail the test.
"""
from __future__ import annotations

import json
from contextlib import contextmanager

import pytest

import outreach_sender
from cloudos.outreach import copy as cw
from cloudos.outreach import sender

ADDR = "123 Main St, Baton Rouge, LA 70801"


class Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class ReadOnlyConn:
    """`leads` = one dict per queued first touch. Every statement is recorded; a non-SELECT raises."""

    def __init__(self, leads):
        self.leads = {l["company_id"]: l for l in leads}
        self.sql: list[str] = []
        self.commits = self.rollbacks = 0

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.sql.append(flat)
        assert flat.upper().startswith("SELECT"), f"the audit wrote to the database: {flat[:60]}"
        if flat.startswith("SELECT * FROM outreach_queue WHERE state = 'queued' AND step = 0"):
            return Result([l["row"] for l in self.leads.values()])
        if "outreach_send_check" in flat:
            email, kind, cid = params
            lead = self.leads[cid]
            assert (email, kind) == (lead["row"]["recipient"], "cold")
            if isinstance(lead["guard"], Exception):
                raise lead["guard"]
            return Result([{"r": lead["guard"]}])
        if flat.startswith("SELECT 1 FROM outreach_queue"):
            return Result([{"?column?": 1}] if self.leads[params[4]].get("dup_queue") else [])
        if "outreach_history" in flat:
            return Result([{"?column?": 1}] if self.leads[params["c"]].get("dup_history") else [])
        if flat.startswith("SELECT 1 FROM contacts WHERE company_id"):
            assert "email_status IN ('validated','published')" in flat
            lead = self.leads[params[0]]
            assert params[1] == lead["row"]["recipient"]
            return Result([{"?column?": 1}] if lead.get("contact_ok", True) else [])
        if flat.startswith("SELECT * FROM companies WHERE company_id"):
            return Result([{"company_name": self.leads[params[0]]["name"]}])
        raise AssertionError(f"unexpected query: {flat[:80]}")

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def lead(n: int, *, guard=None, body=None, subject=None, email=None, **kw) -> dict:
    name = f"Secret Roofing {n} LLC"
    e = cw.first_touch({"company_name": name, "industry": "roofing", "city": "Tulsa", "personalization": {}},
                       sender_name="Sam Sender", postal_address=ADDR)
    assert cw.qa(e, postal_address=ADDR, company_name=name) == []
    cid = f"00000000-0000-0000-0000-{n:012d}"
    row = {"queue_id": n, "company_id": cid, "step": 0, "recipient": email or f"owner{n}@secretroofing{n}.test",
           "subject": e.subject if subject is None else subject, "body": body(e.body) if body else e.body,
           "copy_variant": e.variant, "state": "queued"}
    return {"company_id": cid, "name": name, "row": row,
            "guard": {"allowed": True, "reasons": []} if guard is None else guard, **kw}


def refused(*reasons):
    return {"allowed": False, "reasons": list(reasons)}


@pytest.fixture(autouse=True)
def _postal(monkeypatch):
    monkeypatch.setenv("SENDER_POSTAL_ADDRESS", ADDR)


def mixed_queue() -> list[dict]:
    return [
        lead(1),                                                             # passes
        lead(2, email="someone2@gmail.com"),                                 # passes (free-mail: no domain lookup)
        lead(3, guard=refused("email_suppressed")),                          # unsubscribed / suppressed
        lead(4, guard=refused("bounced", "do_not_contact")),                 # two guard reasons, one row
        lead(5, guard=refused("already_contacted")),
        lead(6, guard=RuntimeError("db down for owner6@secretroofing6.test")),   # broken guard = refusal
        lead(7, guard={"allowed": "yes"}),                                   # not a literal True = refusal
        lead(8, dup_queue=True),                                             # same business already queued
        lead(9, dup_history=True),                                           # same business emailed before
        lead(10, contact_ok=False),                                          # address no longer validated/published
        lead(11, body=lambda b: b.replace("I help", "Our AI agent helps")),  # copy QA: wording
        lead(12, body=lambda b: b.replace(ADDR, "")),                        # copy QA: no postal address
        lead(13, body=lambda b: b.replace('"stop"', "no")),                  # copy QA: no opt-out
        lead(14, subject="Hi {name}"),                                       # copy QA: placeholder
        lead(15, subject="  "),                                              # copy QA: empty subject
    ]


def test_counts_passing_rows_and_every_failure_category():
    conn = ReadOnlyConn(mixed_queue())
    out = sender.audit_queue(conn)
    assert (out["audited"], out["passing"], out["failing"]) == (15, 2, 13)
    assert out["failures"] == {"guard": 3, "guard_error": 2, "duplicate_business": 2, "contact_not_eligible": 1, "qa": 5}
    assert sum(out["failures"].values()) == out["failing"]
    assert out["guard_reasons"] == {"email_suppressed": 1, "bounced": 1, "do_not_contact": 1, "already_contacted": 1}
    assert out["qa_problems"] == {"mentions the technology": 1, "no postal address": 1, "no opt-out line": 1,
                                  "unfilled placeholder": 1, "empty subject": 1}
    assert out["postal_address_set"] is True


def test_audit_agrees_with_what_the_sender_itself_would_decide():
    """Same rows through send_item()'s own refusal step: the audit passes exactly the rows it would send."""
    leads = mixed_queue()
    conn = ReadOnlyConn(leads)
    sendable = [l["row"]["queue_id"] for l in leads if sender._refusal(conn, l["row"]) is None]
    assert sendable == [1, 2, 10]          # 10 is refused only by the contact check the audit adds
    assert sender.audit_queue(ReadOnlyConn(leads))["passing"] == len(sendable) - 1


def test_send_item_cancels_through_the_same_refusal_step(monkeypatch):
    class Mailbox:
        sent = 0

        def send(self, msg):
            self.sent += 1

    class WriteConn(ReadOnlyConn):
        def __init__(self, leads):
            super().__init__(leads)
            self.updates: list[tuple] = []

        def execute(self, sql, params=None):
            if sql.lstrip().upper().startswith("UPDATE"):
                self.updates.append(params)
                return Result([])
            return super().execute(sql, params)

    for n, kw, reason in ((3, {"guard": refused("email_suppressed")}, "guard: email_suppressed"),
                          (8, {"dup_queue": True}, "same business as a company already emailed or queued"),
                          (13, {"body": lambda b: b.replace('"stop"', "no")}, "qa: no opt-out line")):
        item = lead(n, **kw)
        conn, mb = WriteConn([item]), Mailbox()
        assert sender.send_item(conn, item["row"], mb, {}, from_email="x@y.test") == "cancelled"
        assert conn.updates == [("cancelled", reason, n)] and mb.sent == 0


def test_missing_postal_address_fails_every_row_like_the_sender_would(monkeypatch):
    monkeypatch.delenv("SENDER_POSTAL_ADDRESS")
    out = sender.audit_queue(ReadOnlyConn([lead(1), lead(2)]))
    assert (out["passing"], out["failures"], out["postal_address_set"]) == (0, {"qa": 2}, False)


def test_empty_queue():
    out = sender.audit_queue(ReadOnlyConn([]))
    assert (out["audited"], out["passing"], out["failing"], out["failures"]) == (0, 0, 0, {})


def test_audit_only_reads():
    conn = ReadOnlyConn(mixed_queue())
    sender.audit_queue(conn)
    assert conn.commits == 0
    assert conn.sql and all(s.upper().startswith("SELECT") for s in conn.sql)
    assert not any(w in s.upper() for s in conn.sql for w in ("UPDATE ", "INSERT ", "DELETE ", "FOR UPDATE"))


def test_unexpected_guard_reason_text_is_never_echoed():
    out = sender.audit_queue(ReadOnlyConn([lead(1, guard=refused("blocked owner1@secretroofing1.test"))]))
    assert out["guard_reasons"] == {"other": 1}


def _cli(monkeypatch, capsys, leads, argv):
    conn = ReadOnlyConn(leads)

    @contextmanager
    def get_conn():
        yield conn

    monkeypatch.setattr(outreach_sender.db, "get_conn", get_conn)
    code = outreach_sender.main(argv)
    return code, conn, capsys.readouterr().out


def test_command_prints_aggregates_only(monkeypatch, capsys):
    leads = mixed_queue()
    code, conn, out = _cli(monkeypatch, capsys, leads, ["queue-audit"])
    data = json.loads(out)
    assert code == 1 and data["passing"] == 2 and data["target"] == 100 and data["meets_target"] is False
    assert set(data) == {"audited", "passing", "failing", "failures", "guard_reasons", "qa_problems",
                         "postal_address_set", "target", "meets_target"}
    for group in ("failures", "guard_reasons", "qa_problems"):
        assert all(type(v) is int for v in data[group].values())
    low = out.lower()
    for l in leads:
        r = l["row"]
        for secret in (r["recipient"], r["recipient"].split("@")[1], l["name"], l["company_id"], r["body"]):
            assert secret.lower() not in low
        if r["subject"].strip():
            assert r["subject"].lower() not in low
    for banned in ("@", "secret roofing", ADDR.lower(), "ai agent", "sam sender", "{name}"):
        assert banned not in low
    assert conn.commits == 0 and all(s.upper().startswith("SELECT") for s in conn.sql)


def test_command_exits_zero_only_when_the_target_is_met(monkeypatch, capsys):
    code, _, out = _cli(monkeypatch, capsys, [lead(n) for n in range(1, 101)], ["queue-audit"])
    assert code == 0 and json.loads(out)["passing"] == 100 and json.loads(out)["meets_target"] is True
    code, _, out = _cli(monkeypatch, capsys, [lead(n) for n in range(1, 100)], ["queue-audit"])
    assert code == 1 and json.loads(out)["passing"] == 99
    code, _, out = _cli(monkeypatch, capsys, [lead(1), lead(2)], ["queue-audit", "--min", "2"])
    assert code == 0 and json.loads(out)["target"] == 2
