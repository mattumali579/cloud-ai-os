"""Hostinger sender, against a REAL Postgres (the same migrations production runs).

Needs CONVERSATIONS_TEST_DATABASE_URL pointing at a throwaway database (it is wiped):
    CONVERSATIONS_TEST_DATABASE_URL=postgresql://postgres:test@localhost:55432/brtest pytest tests/test_outreach_sender.py
Mail servers are fakes; nothing leaves the machine.
"""
from __future__ import annotations

import copy as _copy
import json
import os
import threading
import uuid
from datetime import date, datetime, timedelta, timezone

import psycopg
import pytest
from psycopg.rows import dict_row

from cloudos.conversations import guard, store
from cloudos.conversations.pipeline import process_inbound
from cloudos.outreach import agentmail, airtable_sync, replies, sender, status
from cloudos.outreach import copy as cw
from cloudos.outreach.transport import SendResult

URL = os.getenv("CONVERSATIONS_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="CONVERSATIONS_TEST_DATABASE_URL not set")
FROM = "matt@fitnesshubb.test"
ADDR = "123 Main St, Baton Rouge, LA 70801"
TABLES = ["airtable_mirror", "airtable_api_usage", "airtable_state", "outreach_sender_runs", "outreach_queue",
          "human_attention_queue", "outreach_drafts", "reply_analyses", "sales_facts", "email_suppressions",
          "company_status_transitions", "company_conversation_state", "notifications", "deals", "reply_poll_state",
          "outreach_messages", "outreach_history", "discovery_history", "contacts", "companies"]


@pytest.fixture(scope="module")
def schema():
    from cloudos import db
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        c.execute(db._SCHEMA_MIGRATIONS_DDL)
        c.execute("INSERT INTO schema_migrations (version) VALUES ('004_school_system')")
    with psycopg.connect(URL, row_factory=dict_row) as c:
        applied = db._migrate(c)
    assert applied[-1].startswith("009")
    yield


@pytest.fixture
def conn(schema, monkeypatch):
    monkeypatch.setenv("SENDER_POSTAL_ADDRESS", ADDR)
    monkeypatch.delenv("AGENTMAIL_API_KEY", raising=False)
    monkeypatch.delenv("HOSTINGER_DAILY_LIMIT", raising=False)
    with psycopg.connect(URL, row_factory=dict_row) as c:
        c.execute("TRUNCATE " + ", ".join(TABLES) + " RESTART IDENTITY CASCADE")
        c.commit()
        yield c


@pytest.fixture
def cfg():
    c = sender.load_config()
    c = _copy.deepcopy(c)
    c["pacing"].update(send_days=[0, 1, 2, 3, 4, 5, 6], window_start="00:00", window_end="23:59",
                       provider_daily_limit=1000, daily_limit=1000)
    return c


class FakeMailbox:
    """Stands in for Hostinger. `script` = results to hand back per send, default all accepted."""

    def __init__(self, script=None):
        self.script = list(script or [])
        self.sent: list = []
        self.sent_ids: set[str] = set()
        self.password = "x"
        self.auto_files_sent = False

    def check_login(self):
        return None

    def send(self, msg):
        r = self.script.pop(0) if self.script else SendResult(True, "sent", "250 ok")
        if r.ok:
            self.sent.append(msg)
        return r

    def file_in_sent(self, msg):
        self.sent_ids.add(str(msg["Message-ID"]).strip("<>").lower())
        return True

    def in_sent(self, mid):
        return mid in self.sent_ids

    def close(self):
        pass


def company(conn, name="ABC Roofing", domain="abcroofing.com", email="john@abcroofing.com", status_="outreach_ready",
            qual="HIGH", email_status="validated", facts=None, historical=False):
    cid = str(uuid.uuid4())
    conn.execute("INSERT INTO companies (company_id, company_name, normalized_name, domain, normalized_domain, website, "
                 "industry, city, state, discovery_source, qualification_status, outreach_status, personalization, "
                 "is_historical) VALUES (%s,%s,%s,%s,%s,%s,'roofing','Baton Rouge','LA','test',%s,%s,%s,%s)",
                 (cid, name, name.lower(), domain, domain, f"https://{domain}/", qual, status_,
                  json.dumps(facts or {"google_reviews": 88, "google_rating": 4.8}), historical))
    if email:
        conn.execute("INSERT INTO contacts (company_id, email, email_status) VALUES (%s,%s,%s)", (cid, email, email_status))
    conn.commit()
    return cid


def q(conn, cid, step=0):
    return conn.execute("SELECT * FROM outreach_queue WHERE company_id = %s AND step = %s", (cid, step)).fetchone()


def run(conn, cfg, mb, minutes=5):
    return sender.run(conn, mb, cfg, from_email=FROM, minutes=minutes, sleep=lambda s: None)


# --------------------------------------------------------------------- copy
def test_copy_never_pitches_technology_and_has_opt_out():
    row = {"company_name": "SMITH AUTOMOTIVE LLC", "industry": "auto_repair", "city": "Tulsa",
           "personalization": {"since_year": 1988}}
    e = cw.first_touch(row, sender_name="Sam Sender", postal_address=ADDR)
    assert cw.qa(e, postal_address=ADDR, company_name=row["company_name"]) == []
    assert "Smith Automotive" in e.body and "since 1988" in e.body
    bad = cw.Email(e.subject, e.body.replace("I help", "Our AI agent helps"), "x")
    assert any("technology" in p for p in cw.qa(bad, postal_address=ADDR))
    assert "no postal address" in cw.qa(e, postal_address="")
    assert "no opt-out line" in cw.qa(cw.Email(e.subject, e.body.replace('"stop"', "no"), "x"), postal_address=ADDR)
    assert "unfilled placeholder" in cw.qa(cw.Email("Hi {name}", e.body, "x"), postal_address=ADDR)


def test_copy_only_uses_facts_it_has():
    e = cw.first_touch({"company_name": "Glow Salon", "industry": "salon", "city": "", "personalization": {}},
                       sender_name="Sam Sender", postal_address=ADDR)
    assert "reviews" not in e.body and "since" not in e.body and e.variant == f"{cw.COPY_VERSION}-generic"


@pytest.mark.parametrize("facts,tag", [
    ({"since_year": 1988}, "since"), ({"free_estimate_offer": True}, "estimates"),
    ({"emergency_service": True}, "emergency"), ({"google_reviews": 140, "google_rating": 4.9}, "reviews"),
    ({}, "generic")])
def test_every_angle_is_one_offer_short_and_passes_qa(facts, tag):
    row = {"company_name": "Bayou Plumbing Co.", "industry": "plumbing", "city": "Metairie", "personalization": facts}
    e = cw.first_touch(row, sender_name="Sam Sender", postal_address=ADDR)
    assert e.variant == f"{cw.COPY_VERSION}-{tag}"
    assert cw.qa(e, postal_address=ADDR, company_name=row["company_name"]) == []
    assert len(e.body.replace(ADDR, "").split()) <= 110
    assert "three ways" not in e.body and e.body.count("?") == 1
    assert e.subject[0].isupper() and len(e.subject) <= 60


def test_refresh_rewrites_old_wording_only_before_any_send_attempt(conn, cfg):
    fresh = company(conn, "Fresh Roofing", "freshroof.com", "a@freshroof.com")
    tried = company(conn, "Tried Roofing", "triedroof.com", "a@triedroof.com")
    sender.plan(conn, cfg)
    conn.execute("UPDATE outreach_queue SET copy_variant = 'v1-reviews', body = 'old words' || body")
    conn.execute("UPDATE outreach_queue SET attempts = 1 WHERE company_id = %s", (tried,))
    conn.commit()
    out = sender.refresh_queued(conn, cfg)
    assert out == {"rewritten": 1, "qa_failed": 0}
    assert q(conn, fresh)["copy_variant"].startswith(cw.COPY_VERSION + "-")
    assert not q(conn, fresh)["body"].startswith("old words")
    assert q(conn, tried)["body"].startswith("old words")      # a started send keeps its exact text
    assert sender.refresh_queued(conn, cfg) == {"rewritten": 0, "qa_failed": 0}


# ---------------------------------------------------------------- ready gate
def test_ready_target_counts_ready_not_discovered(conn):
    from cloudos.leadgen.store import found_today
    company(conn, "Ready One", "ready1.com", "a@ready1.com")
    company(conn, "No Email", "noemail.com", None, status_="not_ready", qual="LOW")
    company(conn, "Rejected", "chain.com", "a@chain.com", status_="rejected", qual="REJECT")
    assert found_today(conn) == 1
    late = company(conn, "Recovered", "recovered.com", "a@recovered.com", status_="not_ready", qual="MEDIUM")
    assert found_today(conn) == 1
    conn.execute("UPDATE companies SET outreach_status = 'outreach_ready' WHERE company_id = %s", (late,))
    conn.commit()
    assert found_today(conn) == 2            # became ready today -> counts; stamped by the database
    stamp = conn.execute("SELECT ready_at FROM companies WHERE company_id = %s", (late,)).fetchone()["ready_at"]
    conn.execute("UPDATE companies SET outreach_status = 'handed_off' WHERE company_id = %s", (late,))
    conn.execute("UPDATE companies SET outreach_status = 'outreach_ready' WHERE company_id = %s", (late,))
    conn.commit()
    assert conn.execute("SELECT ready_at FROM companies WHERE company_id = %s", (late,)).fetchone()["ready_at"] == stamp


# ------------------------------------------------------------ plan / dedupe
def test_plan_queues_ready_with_copy_and_skips_blocked(conn, cfg):
    ok = company(conn)
    sup = company(conn, "Suppressed Co", "supp.com", "x@supp.com")
    store.suppress(conn, reason="unsubscribe", email="x@supp.com")
    dom = company(conn, "Domain Blocked", "blockeddom.com", "y@blockeddom.com")
    conn.execute("INSERT INTO email_suppressions (domain, reason) VALUES ('blockeddom.com','manual')")
    old = company(conn, "Emailed Before", "before.com", "z@before.com")
    conn.execute("INSERT INTO outreach_history (company_id, email, campaign, sent_at, status, provider, dedupe_key) "
                 "VALUES (%s,'z@before.com','old',now() - interval '30 days','sent','gmail','old-1')", (old,))
    company(conn, "No Email", "none.com", None, status_="not_ready", qual="LOW")
    conn.commit()
    out = sender.plan(conn, cfg)
    assert out["queued"] == 1 and q(conn, ok)["state"] == "queued"
    row = q(conn, ok)
    assert row["subject"] and ADDR in row["body"] and cw.qa(cw.Email(row["subject"], row["body"], ""), postal_address=ADDR) == []
    assert out["blocked"] == {"suppressed": 2, "already_contacted": 1}
    for cid in (sup, dom, old):
        assert q(conn, cid) is None
    assert conn.execute("SELECT outreach_status FROM companies WHERE company_id = %s", (old,)).fetchone()["outreach_status"] == "contacted"
    assert sender.plan(conn, cfg)["queued"] == 0          # idempotent: nothing new on a second run


def test_same_business_under_two_records_is_queued_once(conn, cfg):
    company(conn, "ABC Roofing", "abcroofing.com", "john@abcroofing.com")
    company(conn, "ABC Roofing LLC (dup)", "abcroofing-llc.com", "office@abcroofing.com")
    assert sender.plan(conn, cfg)["queued"] == 1
    assert conn.execute("SELECT count(*) n FROM outreach_queue").fetchone()["n"] == 1


def test_missing_postal_address_prepares_nothing_and_burns_no_leads(conn, cfg, monkeypatch):
    monkeypatch.setenv("SENDER_POSTAL_ADDRESS", "")
    cid = company(conn)
    out = sender.plan(conn, cfg)
    assert out["queued"] == 0 and out["qa_failed"] == 0 and q(conn, cid) is None
    monkeypatch.setenv("SENDER_POSTAL_ADDRESS", ADDR)
    assert sender.plan(conn, cfg)["queued"] == 1           # fixed setting -> the lead is still there


def test_business_name_with_a_banned_word_is_not_blocked(conn, cfg):
    cid = company(conn, "Automation Plumbing Agents", "autoplumb.com", "a@autoplumb.com")
    assert sender.plan(conn, cfg)["queued"] == 1 and q(conn, cid)["state"] == "queued"


def test_other_record_at_an_already_emailed_domain_is_skipped(conn, cfg):
    a = company(conn, "ABC Roofing", "abcroofing.com", "sales@abcroofing.com", status_="contacted")
    conn.execute("INSERT INTO outreach_history (company_id, email, campaign, sent_at, status, provider, dedupe_key) "
                 "VALUES (%s,'sales@abcroofing.com','old',now() - interval '9 days','sent','gmail','old-abc')", (a,))
    conn.commit()
    b = company(conn, "ABC Restoration", "abcrestoration.com", "info@abcroofing.com")
    out = sender.plan(conn, cfg)
    assert out["queued"] == 0 and out["blocked"].get("duplicate_business") == 1 and q(conn, b) is None
    free = company(conn, "Joe Handyman", "joehandy.com", "joe.handy@gmail.com")
    company(conn, "Other Gmail Biz", "othergm.com", "someone.else@gmail.com", status_="contacted")
    assert sender.plan(conn, cfg)["queued"] == 1 and q(conn, free)["state"] == "queued"   # gmail.com is not a business


# -------------------------------------------------------------------- send
def test_send_records_confirmed_send_and_schedules_followup(conn, cfg):
    cid = company(conn)
    sender.plan(conn, cfg)
    mb = FakeMailbox()
    st = run(conn, cfg, mb)
    assert st["sent"] == 1 and len(mb.sent) == 1
    row = q(conn, cid)
    assert row["state"] == "sent" and row["sent_at"] and row["message_id_header"]
    m = conn.execute("SELECT * FROM outreach_messages WHERE company_id = %s", (cid,)).fetchone()
    assert m["provider"] == "hostinger" and m["provider_message_id"] == row["message_id_header"] and m["body"] == row["body"]
    assert conn.execute("SELECT outreach_status, first_contacted_at FROM companies WHERE company_id = %s", (cid,)
                        ).fetchone()["outreach_status"] == "contacted"
    assert store.ensure_state(conn, cid, lock=False)["current_status"] == "emailed"
    fu = q(conn, cid, 1)
    assert fu["state"] == "queued" and fu["due_at"] > datetime.now(timezone.utc) + timedelta(days=1)
    assert fu["subject"].startswith("Re: ") and fu["thread_root"] == row["message_id_header"]
    sent = mb.sent[0]
    assert sent["List-Unsubscribe"] and sent["Message-ID"].strip("<>") == row["message_id_header"]
    # a second cycle sends nothing: the first touch is done and the follow-up is not due
    assert run(conn, cfg, mb)["sent"] == 0 and len(mb.sent) == 1
    assert guard.check(conn, "john@abcroofing.com", "cold", cid)["allowed"] is False


def test_followup_threads_under_first_touch_and_sequence_ends(conn, cfg):
    cid = company(conn)
    sender.plan(conn, cfg)
    mb = FakeMailbox()
    run(conn, cfg, mb)
    for step in (1, 2):
        conn.execute("UPDATE outreach_queue SET due_at = now() - interval '1 minute' WHERE company_id = %s AND step = %s",
                     (cid, step))
        conn.commit()
        assert run(conn, cfg, mb)["sent"] == 1
        assert mb.sent[-1]["In-Reply-To"].strip("<>") == q(conn, cid)["message_id_header"]
    assert q(conn, cid, 3) is None                         # 3 touches total, never more
    assert len(mb.sent) == 3


def test_two_workers_never_send_the_same_row(conn, cfg):
    for i in range(6):
        company(conn, f"Co {i}", f"co{i}.com", f"a@co{i}.com")
    sender.plan(conn, cfg)
    claimed: list[int] = []
    lock = threading.Lock()

    def worker():
        with psycopg.connect(URL, row_factory=dict_row) as c:
            while True:
                item = sender.claim(c, f"w{threading.get_ident()}")
                if not item:
                    return
                with lock:
                    claimed.append(item["queue_id"])

    ts = [threading.Thread(target=worker) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(claimed) == 6 and len(set(claimed)) == 6


def test_crash_recovery_never_resends(conn, cfg):
    a, b, c = company(conn, "A", "a.com", "x@a.com"), company(conn, "B", "b.com", "x@b.com"), company(conn, "C", "c.com", "x@c.com")
    sender.plan(conn, cfg)
    mb = FakeMailbox()
    # A: died before the SMTP step (no Message-ID saved)
    ia = sender.claim(conn, "dead")
    # B: SMTP accepted and filed in Sent, then the worker died before the database write
    ib = sender.claim(conn, "dead")
    conn.execute("UPDATE outreach_queue SET message_id_header = 'b-1@fitnesshubb.test' WHERE queue_id = %s", (ib["queue_id"],))
    mb.sent_ids.add("b-1@fitnesshubb.test")
    # C: Message-ID saved, worker died, nothing in Sent -> may or may not have gone out
    ic = sender.claim(conn, "dead")
    conn.execute("UPDATE outreach_queue SET message_id_header = 'c-1@fitnesshubb.test' WHERE queue_id = %s", (ic["queue_id"],))
    conn.execute("UPDATE outreach_queue SET claimed_at = now() - interval '1 hour' WHERE state = 'claimed'")
    conn.commit()
    got = {ia["company_id"]: "A", ib["company_id"]: "B", ic["company_id"]: "C"}
    out = sender.recover(conn, mb, cfg, from_email=FROM)
    assert out == {"released": 1, "proven_sent": 1, "ambiguous": 1, "unknown": 0}
    states = {got[r["company_id"]]: r["state"] for r in conn.execute("SELECT company_id, state FROM outreach_queue WHERE step = 0")}
    assert states == {"A": "queued", "B": "sent", "C": "ambiguous"}
    assert sender.recover(conn, mb, cfg, from_email=FROM) == {"released": 0, "proven_sent": 0, "ambiguous": 0, "unknown": 0}
    run(conn, cfg, mb)
    assert len(mb.sent) == 1 and str(mb.sent[0]["To"]) in {"x@a.com", "x@b.com", "x@c.com"}
    sent_to = {str(m["To"]) for m in mb.sent}
    assert sent_to == {[k for k, v in {"x@a.com": a, "x@b.com": b, "x@c.com": c}.items() if v == str(ia["company_id"])][0]}
    assert conn.execute("SELECT count(*) n FROM outreach_messages WHERE provider_message_id = 'b-1@fitnesshubb.test'"
                        ).fetchone()["n"] == 1


def test_interrupted_handover_is_ambiguous_not_retried(conn, cfg):
    cid = company(conn)
    sender.plan(conn, cfg)
    mb = FakeMailbox([SendResult(False, "unknown", "during data: timeout")])
    st = run(conn, cfg, mb)
    assert st["ambiguous"] == 1 and q(conn, cid)["state"] == "ambiguous"
    assert run(conn, cfg, mb)["sent"] == 0 and mb.sent == []


def test_transient_failure_retries_same_row_then_gives_up(conn, cfg):
    cid = company(conn)
    sender.plan(conn, cfg)
    mb = FakeMailbox([SendResult(False, "transient", "421 try later")] * 3)
    for _ in range(3):
        run(conn, cfg, mb)
        conn.execute("UPDATE outreach_queue SET due_at = now() - interval '1 minute' WHERE state = 'queued'")
        conn.commit()
    row = q(conn, cid)
    assert row["state"] == "failed" and row["attempts"] == 3
    assert conn.execute("SELECT count(*) n FROM outreach_queue").fetchone()["n"] == 1


def test_provider_limit_is_not_a_bounce_and_stops_the_run(conn, cfg):
    for i in range(4):
        company(conn, f"Co {i}", f"co{i}.com", f"a@co{i}.com")
    sender.plan(conn, cfg)
    mb = FakeMailbox([SendResult(False, "throttled", "550 5.4.5 Daily user sending quota exceeded")])
    st = run(conn, cfg, mb)
    assert st["throttled"] == 1 and "limit" in st["stopped"]
    assert conn.execute("SELECT count(*) n FROM email_suppressions").fetchone()["n"] == 0
    assert conn.execute("SELECT count(*) n FROM outreach_queue WHERE state = 'queued' AND attempts = 0").fetchone()["n"] == 4


def test_three_failures_in_a_row_pause_the_run(conn, cfg):
    for i in range(6):
        company(conn, f"Co {i}", f"co{i}.com", f"a@co{i}.com")
    sender.plan(conn, cfg)
    st = run(conn, cfg, FakeMailbox([SendResult(False, "permanent", "550 5.1.1 user unknown")] * 6))
    assert st["failed"] == 3 and "in a row" in st["stopped"]
    assert conn.execute("SELECT count(*) n FROM outreach_queue WHERE state = 'queued'").fetchone()["n"] == 3


def test_ambiguous_send_counts_as_contacted_for_every_sender(conn, cfg):
    cid = company(conn)
    sender.plan(conn, cfg)
    run(conn, cfg, FakeMailbox([SendResult(False, "unknown", "during data: timeout")]))
    assert conn.execute("SELECT first_contacted_at FROM companies WHERE company_id = %s", (cid,)).fetchone()["first_contacted_at"]
    assert guard.check(conn, "john@abcroofing.com", "cold", cid)["allowed"] is False


def test_no_followups_when_replies_could_not_be_read(conn, cfg):
    a, b = company(conn, "A", "a.com", "x@a.com"), company(conn, "B", "b.com", "x@b.com")
    sender.plan(conn, cfg, limit=1)
    mb = FakeMailbox()
    run(conn, cfg, mb)
    sender.plan(conn, cfg)
    conn.execute("UPDATE outreach_queue SET due_at = now() - interval '1 minute' WHERE step = 1")
    conn.commit()
    st = sender.run(conn, mb, cfg, from_email=FROM, minutes=5, sleep=lambda s: None, followups=False)
    assert st["sent"] == 1 and all(str(m["X-Leadgen-Outreach"]) == "hostinger-first" for m in mb.sent)
    assert conn.execute("SELECT state FROM outreach_queue WHERE step = 1").fetchone()["state"] == "queued"


def test_rejected_address_is_suppressed_for_good(conn, cfg):
    cid = company(conn)
    sender.plan(conn, cfg)
    run(conn, cfg, FakeMailbox([SendResult(False, "permanent", "550 5.1.1 user unknown")]))
    assert q(conn, cid)["state"] == "failed"
    assert conn.execute("SELECT reason FROM email_suppressions WHERE email = 'john@abcroofing.com'").fetchone()["reason"] == "hard_bounce"
    assert guard.check(conn, "john@abcroofing.com", "cold")["allowed"] is False


def test_bad_login_stops_run_and_loses_nothing(conn, cfg):
    cid = company(conn)
    sender.plan(conn, cfg)
    st = run(conn, cfg, FakeMailbox([SendResult(False, "auth", "535")]))
    assert st["auth_failed"] and q(conn, cid)["state"] == "queued" and q(conn, cid)["attempts"] == 0


def test_daily_cap_and_window(conn, cfg):
    for i in range(5):
        company(conn, f"Co {i}", f"co{i}.com", f"a@co{i}.com")
    sender.plan(conn, cfg)
    cfg["pacing"]["daily_limit"] = 2
    mb = FakeMailbox()
    assert run(conn, cfg, mb)["sent"] == 2 and len(mb.sent) == 2
    assert run(conn, cfg, mb)["sent"] == 0
    cfg["pacing"]["daily_limit"] = 1000
    cfg["pacing"]["window_start"], cfg["pacing"]["window_end"] = "00:00", "00:00"
    assert run(conn, cfg, mb) == {**run(conn, cfg, mb), "in_window": False} and len(mb.sent) == 2


def test_hostinger_daily_limit_env_wins(conn, cfg, monkeypatch):
    monkeypatch.setenv("HOSTINGER_DAILY_LIMIT", "1")
    assert sender.daily_cap(conn, cfg) == 1


def test_checked_in_cap_is_100_from_the_first_sending_day(monkeypatch):
    """No database: the cap does not depend on send history, so day 1 and every later day are the same."""
    monkeypatch.delenv("HOSTINGER_DAILY_LIMIT", raising=False)
    real = sender.load_config()
    p = real["pacing"]
    assert "ramp" not in p and p["daily_limit"] == 100 and p["provider_daily_limit"] == 100
    assert sender.daily_cap(None, real) == 100
    monkeypatch.setenv("HOSTINGER_DAILY_LIMIT", "40")
    assert sender.daily_cap(None, real) == 40                  # a lower provider limit still lowers it
    monkeypatch.setenv("HOSTINGER_DAILY_LIMIT", "1000")
    assert sender.daily_cap(None, real) == 100                 # a higher one never raises it past 100
    assert (p["window_start"], p["window_end"], p["send_days"]) == ("08:00", "17:30", [0, 1, 2, 3, 4])
    assert (p["min_gap_seconds"], p["max_gap_seconds"], p["timezone"]) == (55, 110, "America/Chicago")
    central = lambda *a: datetime(*a, tzinfo=sender.ZoneInfo("America/Chicago"))  # noqa: E731
    assert sender.in_window(real, central(2026, 10, 1, 8, 0)) and sender.in_window(real, central(2026, 10, 1, 17, 29))
    assert not sender.in_window(real, central(2026, 10, 1, 7, 59))
    assert not sender.in_window(real, central(2026, 10, 1, 17, 30))
    assert not sender.in_window(real, central(2026, 10, 3, 12, 0))          # Saturday


def test_run_waits_a_configured_gap_between_sends(conn, cfg):
    for i in range(3):
        company(conn, f"Co {i}", f"co{i}.com", f"a@co{i}.com")
    sender.plan(conn, cfg)
    cfg["pacing"].update(min_gap_seconds=55, max_gap_seconds=110)
    gaps: list[float] = []
    st = sender.run(conn, FakeMailbox(), cfg, from_email=FROM, minutes=60, sleep=gaps.append, clock=lambda: 0.0)
    assert st["sent"] == 3 and len(gaps) == 3 and all(55 <= g <= 110 for g in gaps)


# ---------------------------------------------------------- replies stop it
def _reply(conn, body, to_mid, sender_addr="john@abcroofing.com"):
    return process_inbound(conn, dict(provider_message_id=f"r-{uuid.uuid4()}@mail.test", thread_id=None,
                                      in_reply_to=to_mid, references=[to_mid], sender=sender_addr, recipient=FROM,
                                      subject="Re: hi", body=body, occurred_at=datetime.now(timezone.utc),
                                      headers={}, provider="hostinger", source_ref="test"),
                           our_addresses={FROM}, send=lambda p: (True, "test"), today=date.today())


def test_genuine_reply_cancels_followups(conn, cfg):
    cid = company(conn)
    sender.plan(conn, cfg)
    mb = FakeMailbox()
    run(conn, cfg, mb)
    r = _reply(conn, "Yes, interested - can you call me Tuesday?", q(conn, cid)["message_id_header"])
    assert r["company_id"] == cid and r["match"] == "in_reply_to"
    assert sender.sweep(conn, cfg)["cancelled"] == 1 and q(conn, cid, 1)["state"] == "cancelled"
    conn.execute("UPDATE outreach_queue SET state = 'queued', due_at = now() - interval '1 minute' WHERE step = 1")
    conn.commit()
    run(conn, cfg, mb)                                   # even if something re-queued it, the guard refuses
    assert len(mb.sent) == 1 and q(conn, cid, 1)["state"] == "cancelled"


def test_out_of_office_does_not_stop_sequence(conn, cfg):
    cid = company(conn)
    sender.plan(conn, cfg)
    run(conn, cfg, FakeMailbox())
    _reply(conn, "I am out of the office until Monday with limited access to email.", q(conn, cid)["message_id_header"])
    assert sender.sweep(conn, cfg)["cancelled"] == 0 and q(conn, cid, 1)["state"] == "queued"


def test_stop_reply_unsubscribes_permanently(conn, cfg):
    cid = company(conn)
    sender.plan(conn, cfg)
    run(conn, cfg, FakeMailbox())
    r = _reply(conn, "stop", q(conn, cid)["message_id_header"])
    assert r["classification"] == "UNSUBSCRIBE"
    sender.sweep(conn, cfg)
    assert q(conn, cid, 1)["state"] == "cancelled"
    # the same business under another record and another address at its domain: still never emailed again
    other = company(conn, "ABC Roofing again", "abc-other.com", "office@abcroofing.com")
    out = sender.plan(conn, cfg)
    assert out["queued"] == 0 and q(conn, other) is None
    assert guard.check(conn, "john@abcroofing.com", "cold")["allowed"] is False


# ---------------------------------------------------------- mailbox reader
class FakeIMAP:
    def __init__(self, messages):
        self.messages = messages       # uid -> raw bytes

    def login(self, u, p):
        return "OK", [b""]

    def select(self, f, readonly=True):
        return "OK", [b""]

    def status(self, f, what):
        return "OK", [b'"INBOX" (UIDVALIDITY 7)']

    def uid(self, cmd, *args):
        if cmd == "SEARCH":
            spec = args[-1]
            lo = int(spec.split()[1].split(":")[0]) if spec.startswith("UID") else 0
            return "OK", [" ".join(str(u) for u in self.messages if u >= lo).encode()]
        uids = [int(u) for u in args[0].split(",")]
        return "OK", [(f"{u} (UID {u} BODY[] {{1}}".encode(), self.messages[u]) for u in uids] + [b")"]

    def logout(self):
        pass


def _raw(frm, subject, body, in_reply_to="", mid=None):
    return (f"From: {frm}\r\nTo: {FROM}\r\nSubject: {subject}\r\nDate: Wed, 30 Sep 2026 15:00:00 +0000\r\n"
            f"Message-ID: <{mid or uuid.uuid4()}@x.test>\r\n" + (f"In-Reply-To: <{in_reply_to}>\r\n" if in_reply_to else "")
            + f"Content-Type: text/plain\r\n\r\n{body}\r\n").encode()


def test_inbox_reader_processes_replies_once_and_ignores_personal_mail(conn, cfg):
    cid = company(conn)
    sender.plan(conn, cfg)
    run(conn, cfg, FakeMailbox())
    root = q(conn, cid)["message_id_header"]
    box = {1: _raw("friend@gmail.com", "lunch?", "want lunch"),
           2: _raw("John <john@abcroofing.com>", "Re: More booked jobs", "How much does this cost?", in_reply_to=root)}
    fac = lambda h, p: FakeIMAP(box)  # noqa: E731
    r1 = replies.poll(conn, user=FROM, password="x", imap_factory=fac, send=lambda p: (True, "t"))
    assert r1["scanned"] == 2 and r1["relevant"] == 1 and r1["inbound"] == {"PRICE_QUESTION": 1}
    assert conn.execute("SELECT count(*) n FROM outreach_messages WHERE direction = 'inbound'").fetchone()["n"] == 1
    r2 = replies.poll(conn, user=FROM, password="x", imap_factory=fac, send=lambda p: (True, "t"))
    assert r2["scanned"] == 0
    assert sender.sweep(conn, cfg)["cancelled"] == 1


# --------------------------------------------------------------- AgentMail
def test_agentmail_notice_is_sent_once_and_waits_without_key(conn, cfg):
    posts = []
    post = lambda inbox, payload: (posts.append((inbox, payload)) or True, "ok")  # noqa: E731
    a = agentmail.notify(conn, cfg, role="manager", dedupe_key="t1", subject="S", text="T", post=post)
    b = agentmail.notify(conn, cfg, role="manager", dedupe_key="t1", subject="S", text="T", post=post)
    assert a["created"] and a["delivered"] and not b["created"] and len(posts) == 1
    assert posts[0][0] == cfg["agentmail"]["inboxes"]["manager"]
    pinged = []
    c = agentmail.notify(conn, cfg, role="research", dedupe_key="t2", subject="S", text="T", severity="high",
                         discord=lambda p: pinged.append(p))
    assert c["created"] and not c["delivered"] and len(pinged) == 1
    assert agentmail.flush(conn, cfg, post=post)["delivered"] == 1 and posts[-1][0] == cfg["agentmail"]["inboxes"]["research"]


def test_reply_events_route_to_the_right_inboxes(conn, cfg):
    cid = company(conn)
    sender.plan(conn, cfg)
    run(conn, cfg, FakeMailbox())
    _reply(conn, "Sounds good, I'm interested. What would this look like for us?", q(conn, cid)["message_id_header"])
    posts = []
    post = lambda inbox, payload: (posts.append(inbox) or True, "ok")  # noqa: E731
    n = status.events(conn, cfg, post=post)
    assert n == {"manager": 1, "followup": 1}
    assert status.events(conn, cfg, post=post) == {"manager": 0, "followup": 0}
    assert sorted(posts) == sorted([cfg["agentmail"]["inboxes"]["manager"], cfg["agentmail"]["inboxes"]["followup"]])


# ---------------------------------------------------------------- Airtable
class FakeAirtable:
    def __init__(self):
        self.calls = []

    def request(self, method, url, json=None):
        self.calls.append((method, len(json["records"])))
        recs = [{"id": r.get("id") or f"rec{uuid.uuid4().hex[:14]}"} for r in json["records"]]

        class R:
            status_code = 200

            def json(self_inner):
                return {"records": recs}
        return R()


def test_airtable_only_changed_rows_in_batches_within_budget(conn, cfg, monkeypatch):
    for k in ("AIRTABLE_API_KEY", "AIRTABLE_BASE_ID", "AIRTABLE_TABLE_NAME"):
        monkeypatch.setenv(k, "x")
    for i in range(23):
        company(conn, f"Co {i}", f"co{i}.com", f"a@co{i}.com")
    sender.plan(conn, cfg)
    run(conn, cfg, FakeMailbox())
    conn.execute("INSERT INTO airtable_state (key, value) VALUES ('record_count', '100')")
    conn.commit()
    fake = FakeAirtable()
    r = airtable_sync.sync(conn, cfg, client=fake)
    assert r["created"] == 23 and [n for _, n in fake.calls] == [10, 10, 3]
    assert airtable_sync.sync(conn, cfg, client=fake)["calls"] == 0          # nothing changed, nothing spent
    cfg["airtable"]["monthly_call_budget"] = 3
    one = conn.execute("SELECT company_id FROM outreach_queue LIMIT 1").fetchone()["company_id"]
    _reply(conn, "not interested, thanks", q(conn, one)["message_id_header"], sender_addr=q(conn, one)["recipient"])
    r = airtable_sync.sync(conn, cfg, client=fake)
    assert r["calls"] == 0 and r["deferred"] == 1                            # budget used up this month: waits


def test_funnel_reports_what_it_counts(conn, cfg):
    company(conn)
    company(conn, "No Email", "none.com", None, status_="not_ready", qual="LOW")
    sender.plan(conn, cfg)
    run(conn, cfg, FakeMailbox())
    f = status.funnel(conn)
    assert f["ready_today"] == 1 and f["sent_today"] == 1 and f["no_usable_email_today"] == 1
    assert f["ready_gap"] == 299 and "READY today: 1 of 300" in status.as_text(f)


# ---------------------------------------------------------------- planner state
def _state(**kw):
    s = {"blocking": [], "interested_total": 0, "failed_today": 0, "ambiguous_total": 0, "target_remaining": 0,
         "ready_waiting": 500, "send_cap_today": 15}
    s.update(kw)
    return s


def test_next_action_order_and_stop():
    from cloudos.outreach.status import next_action
    hs = "Hostinger mailbox password not saved yet - x"
    assert next_action(_state(blocking=[hs], interested_total=2))["decision"] == "human_review"
    assert next_action(_state(blocking=["AgentMail key not saved yet"]))["decision"] == "stop"
    assert next_action(_state(interested_total=1))["agent"] == "claude"
    assert next_action(_state(ambiguous_total=1))["agent"] == "codex"
    assert next_action(_state(target_remaining=50, ready_waiting=10))["agent"] == "codex"
    assert next_action(_state(target_remaining=50))["decision"] == "stop"


def test_planner_state_reads_live_tables(conn, cfg):
    from cloudos.outreach import status
    company(conn, "Plan Roofing", "planroof.com", "a@planroof.com")
    sender.plan(conn, cfg)
    s = status.planner_state(conn, status.funnel(conn, cap=15))
    for k in ("discovered_total", "ready_waiting", "queued", "claimed", "sent_today", "failed_today",
              "replied_total", "interested_total", "suppressed_total", "target_remaining", "next_action"):
        assert k in s
    assert s["queued"] == 1 and s["claimed"] == 0
