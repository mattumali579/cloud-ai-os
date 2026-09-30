"""Reply -> memory -> notification -> sales layer, against a REAL Postgres.

Needs CONVERSATIONS_TEST_DATABASE_URL pointing at a throwaway database (it is
wiped). Example:
    docker run -d --name br-conv-test -e POSTGRES_PASSWORD=test -e POSTGRES_DB=brtest -p 55432:5432 postgres:17
    CONVERSATIONS_TEST_DATABASE_URL=postgresql://postgres:test@localhost:55432/brtest pytest tests/test_conversations.py
"""
from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

from cloudos.conversations import guard, reports, store
from cloudos.conversations import status as sm
from cloudos.conversations.pipeline import process_inbound

URL = os.getenv("CONVERSATIONS_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="CONVERSATIONS_TEST_DATABASE_URL not set")
MIGRATIONS = Path(__file__).resolve().parents[1] / "db" / "migrations"
OUR = {"matt@brightreach.test"}
T0 = datetime(2026, 9, 20, 15, 0, tzinfo=timezone.utc)
TABLES = ["human_attention_queue", "outreach_drafts", "reply_analyses", "sales_facts", "email_suppressions",
          "company_status_transitions", "company_conversation_state", "notifications", "deals", "reply_poll_state",
          "outreach_messages", "outreach_history", "contacts", "companies"]


@pytest.fixture(scope="module")
def schema():
    from cloudos import db
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        c.execute(db._SCHEMA_MIGRATIONS_DDL)
        # 004 (school system) needs Supabase's pg_cron and is unrelated to outreach; mark it applied
        c.execute("INSERT INTO schema_migrations (version) VALUES ('004_school_system')")
    with psycopg.connect(URL, row_factory=dict_row) as c:
        applied = db._migrate(c)            # the real runner, exactly as production applies them
    assert any(a.startswith("008") for a in applied)
    yield


@pytest.fixture
def conn(schema):
    with psycopg.connect(URL, row_factory=dict_row) as c:
        c.execute("TRUNCATE " + ", ".join(TABLES) + " RESTART IDENTITY CASCADE")
        c.commit()
        yield c


class Outbox:
    """Stands in for Discord: records every payload 'delivered'."""

    def __init__(self):
        self.sent: list[dict] = []

    def __call__(self, payload):
        self.sent.append(payload)
        return True, "HTTP 204 test"

    @property
    def texts(self):
        return [p["embeds"][0]["description"] for p in self.sent]


def make_company(conn, name="ABC Roofing", domain="abcroofing.com", email="john@abcroofing.com", high=False,
                 rec="recABC0000000001"):
    cid = str(uuid.uuid4())
    conn.execute("INSERT INTO companies (company_id, company_name, normalized_name, domain, normalized_domain, website, "
                 "industry, discovery_source, qualification_status, outreach_status, handoff_ref, personalization) "
                 "VALUES (%s,%s,%s,%s,%s,%s,'roofing','test',%s,'handed_off',%s,%s)",
                 (cid, name, name.lower(), domain, domain, f"https://{domain}/", "HIGH" if high else "MEDIUM",
                  f"airtable:{rec}", '{"has_contact_form": false, "has_chat_or_call_tracking": false, '
                                     '"evidence_url": "https://%s/", "pages_read": 3}' % domain))
    conn.execute("INSERT INTO contacts (company_id, email, email_status) VALUES (%s,%s,'published')", (cid, email))
    conn.commit()
    return cid


def cold_send(conn, cid, to="john@abcroofing.com", mid="<cold-1@brightreach.test>", thread="T1", when=T0,
              body="Hi,\nNoticed your site has no quote form. Worth a quick look? Also, who handles after-hours calls?",
              kind="cold", **kw):
    out = store.record_confirmed_send(conn, company_id=cid, recipient=to, sender="Matt <matt@brightreach.test>",
                                      subject="quick question about ABC Roofing", body=body, sent_at=when,
                                      provider="gmail-smtp", provider_message_id=mid.strip("<>").lower(), thread_id=thread,
                                      kind=kind, offer="Missed-Call Revenue Recovery Build", copy_variant="office-agent-v2",
                                      **kw)
    conn.commit()
    return out


def reply(conn, body, *, sender="John <john@abcroofing.com>", mid=None, thread="T1", in_reply_to="<cold-1@brightreach.test>",
          when=None, subject="Re: quick question about ABC Roofing", outbox=None, headers=None, references=None):
    msg = dict(provider_message_id=(mid or f"r-{uuid.uuid4()}@mail.test"), thread_id=thread, in_reply_to=in_reply_to,
               references=references or in_reply_to, sender=sender, recipient="matt@brightreach.test", subject=subject,
               body=body, occurred_at=when or T0 + timedelta(days=1), headers=headers or {}, provider="gmail",
               source_ref="test")
    return process_inbound(conn, msg, our_addresses=OUR, send=outbox or Outbox(), today=date(2026, 9, 29))


def state(conn, cid):
    return conn.execute("SELECT * FROM company_conversation_state WHERE company_id = %s", (cid,)).fetchone()


# ------------------------------------------------------------ matching
def test_reply_matched_to_correct_company_by_thread(conn):
    a = make_company(conn)
    b = make_company(conn, "Smith HVAC", "smithhvac.com", "sam@smithhvac.com", rec="recSMITH00000001")
    cold_send(conn, a)
    cold_send(conn, b, to="sam@smithhvac.com", mid="<cold-2@brightreach.test>", thread="T2")
    r = reply(conn, "Sounds interesting, tell me more.", sender="sam@smithhvac.com", thread="T2",
              in_reply_to="<cold-2@brightreach.test>")
    assert r["company_id"] == b and r["match"] == "thread_id"
    assert state(conn, a)["current_status"] == "emailed"


def test_in_reply_to_and_exact_sender_levels(conn):
    a = make_company(conn)
    cold_send(conn, a)
    r = reply(conn, "How much?", thread=None)
    assert r["company_id"] == a and r["match"] == "in_reply_to"
    r2 = reply(conn, "Also, do you work weekends?", thread=None, in_reply_to=None)
    assert r2["company_id"] == a and r2["match"] == "exact_recipient"


def test_incorrect_match_rejected_domain_only(conn):
    a = make_company(conn)
    cold_send(conn, a)
    out = Outbox()
    # someone else at the same domain, new email, no thread: domain only -> never auto-acted
    r = reply(conn, "What is this about?", sender="stranger@abcroofing.com", thread=None, in_reply_to=None, outbox=out)
    assert r["status"] == "needs_review_unmatched" and r["confidence"] == "medium"
    assert state(conn, a)["current_status"] == "emailed"          # untouched
    assert conn.execute("SELECT count(*) n FROM outreach_drafts").fetchone()["n"] == 0
    assert len(out.sent) == 1 and "UNMATCHED" in out.texts[0]


def test_conflicting_identifiers_rejected(conn):
    a = make_company(conn)
    b = make_company(conn, "Smith HVAC", "smithhvac.com", "sam@smithhvac.com", rec="recSMITH00000001")
    cold_send(conn, a)
    cold_send(conn, b, to="sam@smithhvac.com", mid="<cold-2@brightreach.test>", thread="T2")
    # thread says company A, but the sender is company B's known contact
    r = reply(conn, "Interested", sender="sam@smithhvac.com", thread="T1", in_reply_to="<cold-1@brightreach.test>")
    assert r["status"] == "needs_review_unmatched" and r["confidence"] == "low"


# ----------------------------------------------------- the four chains
def test_chain1_price_question(conn):
    cid = make_company(conn)
    cold_send(conn, cid)
    assert guard.check(conn, "john@abcroofing.com", "followup")["allowed"] is True
    out = Outbox()
    r = reply(conn, "This sounds interesting. How much?\n\nOn Sat, Sep 20, 2026 at 10:00 AM Matt <matt@brightreach.test> wrote:\n"
                    "> Worth a quick look?", outbox=out)
    assert r["classification"] == "PRICE_QUESTION" and r["match"] == "thread_id"
    s = state(conn, cid)
    assert s["current_status"] == "interested" and s["previous_status"] == "emailed"
    assert s["cold_sequence_active"] is False                       # generic follow-ups cancelled
    chk = guard.check(conn, "john@abcroofing.com", "followup")
    assert chk["allowed"] is False and "reply_received" in chk["reasons"]
    assert len(out.sent) == 1                                       # notified
    text = out.texts[0]
    for needle in ("ABC Roofing", "john@abcroofing.com", "EMAILED → INTERESTED", "How much?", "$1,500 one-time",
                   "Approve the prepared pricing reply", "airtable.com/appQNVOTVqdIYjSho/tblggJQ6ttPCvXygu/recABC0000000001"):
        assert needle in text, needle
    d = conn.execute("SELECT * FROM outreach_drafts WHERE company_id = %s", (cid,)).fetchall()
    assert [x["kind"] for x in d] == ["pricing"] and "$1,500 one-time" in d[0]["body"] and d[0]["state"] == "awaiting_approval"
    msgs = store.thread(conn, cid)
    assert [m["direction"] for m in msgs] == ["outbound", "inbound"]   # conversation stored
    assert conn.execute("SELECT classification FROM reply_analyses").fetchone()["classification"] == "PRICE_QUESTION"


def test_chain2_unsubscribe(conn):
    cid = make_company(conn)
    cold_send(conn, cid)
    out = Outbox()
    r = reply(conn, "Remove me from your list.", outbox=out)
    assert r["classification"] == "UNSUBSCRIBE"
    s = state(conn, cid)
    assert s["current_status"] == "do_not_contact" and s["do_not_contact"] and not s["cold_sequence_active"]
    assert conn.execute("SELECT count(*) n FROM email_suppressions WHERE email = 'john@abcroofing.com'").fetchone()["n"] == 1
    for kind in ("cold", "followup", "reply", "pricing", "proposal"):
        assert guard.check(conn, "john@abcroofing.com", kind)["allowed"] is False, kind
    # even a different address at the same company is blocked by the company suppression
    conn.execute("INSERT INTO contacts (company_id, email) VALUES (%s, 'office@abcroofing.com')", (cid,))
    conn.commit()
    assert guard.check(conn, "office@abcroofing.com", "followup")["allowed"] is False
    # and an address we have never seen at their own domain
    assert "company_suppressed" in guard.check(conn, "someone.new@abcroofing.com", "cold")["reasons"]
    assert out.sent == []                                            # routine: no ping
    with pytest.raises(sm.TransitionError):
        store.set_status(conn, cid, "interested", "try to reopen")
    conn.rollback()
    assert "company_suppressed" in guard.check(conn, "office@abcroofing.com", "reply")["reasons"]


def test_chain3_referral(conn):
    cid = make_company(conn)
    cold_send(conn, cid)
    out = Outbox()
    r = reply(conn, "Talk to Mike at mike@example.com.", outbox=out)
    assert r["classification"] == "REFERRAL"
    f = conn.execute("SELECT * FROM sales_facts WHERE company_id = %s AND fact_type = 'referral'", (cid,)).fetchone()
    assert f["value"]["email"] == "mike@example.com" and f["value"]["name"] == "Mike"
    assert conn.execute("SELECT company_id FROM contacts WHERE email = 'mike@example.com'").fetchone()["company_id"] == uuid.UUID(cid)
    assert state(conn, cid)["current_status"] == "replied"
    d = conn.execute("SELECT * FROM outreach_drafts WHERE kind = 'referral_intro'").fetchone()
    assert d["to_email"] == "mike@example.com" and "john@abcroofing.com" in d["body"]
    assert len(out.sent) == 1 and "mike@example.com" in out.texts[0]


def test_chain4_bare_yeah_needs_review(conn):
    cid = make_company(conn)
    cold_send(conn, cid)
    out = Outbox()
    r = reply(conn, "Yeah.", outbox=out)
    assert r["classification"] == "NEEDS_REVIEW"
    a = conn.execute("SELECT * FROM reply_analyses").fetchone()
    assert a["needs_review_reason"] == "affirmation_ambiguous"
    assert "We first emailed on 2026-09-20" in a["conversation_summary"]      # whole thread was loaded
    assert conn.execute("SELECT count(*) n FROM outreach_drafts").fetchone()["n"] == 0   # no guessing
    assert state(conn, cid)["current_status"] == "needs_review"
    assert guard.check(conn, "john@abcroofing.com", "reply")["allowed"] is False
    assert len(out.sent) == 1 and "NEEDS YOU" in out.texts[0]
    q = reports.attention_queue(conn)
    assert len(q) == 1 and q[0]["reason_code"] == "affirmation_ambiguous"


def test_yeah_after_a_price_is_understood_from_the_thread(conn):
    cid = make_company(conn)
    cold_send(conn, cid)
    reply(conn, "How much?")
    cold_send(conn, cid, mid="<price-1@brightreach.test>", body="It's $1,500 one-time. Want to go ahead?", kind="pricing",
              when=T0 + timedelta(days=2), price_quoted="$1,500 one-time")
    out = Outbox()
    r = reply(conn, "Yeah that works.", when=T0 + timedelta(days=3), in_reply_to="<price-1@brightreach.test>", outbox=out)
    assert r["classification"] == "READY_TO_BUY"
    s = state(conn, cid)
    assert s["current_status"] == "proposal_needed" and s["current_price"] == "$1,500 one-time"
    assert "READY TO BUY" in out.texts[0] and "$1,500 one-time" in out.texts[0]
    assert conn.execute("SELECT kind FROM outreach_drafts WHERE state='awaiting_approval'").fetchone()["kind"] == "proposal"


# ------------------------------------------------------ safety rules
def test_not_interested_stops_followups(conn):
    cid = make_company(conn)
    cold_send(conn, cid)
    out = Outbox()
    r = reply(conn, "Not interested, thanks.", outbox=out)
    assert r["classification"] == "NOT_INTERESTED" and state(conn, cid)["current_status"] == "lost"
    assert guard.check(conn, "john@abcroofing.com", "followup")["allowed"] is False
    assert out.sent == []


def test_interested_stops_generic_sequence(conn):
    cid = make_company(conn)
    cold_send(conn, cid)
    reply(conn, "Sounds interesting!")
    s = state(conn, cid)
    assert s["current_status"] == "interested" and not s["cold_sequence_active"]
    assert "status_interested_stops_generic_sequence" in guard.check(conn, "john@abcroofing.com", "followup")["reasons"]
    assert guard.check(conn, "john@abcroofing.com", "reply")["allowed"] is True   # owner-approved replies still fine
    kinds = sorted(r["kind"] for r in conn.execute("SELECT kind FROM outreach_drafts").fetchall())
    assert kinds == ["audit", "reply"]


def test_auto_reply_does_not_count_as_reply(conn):
    cid = make_company(conn)
    cold_send(conn, cid)
    out = Outbox()
    r = reply(conn, "I am out of the office until Oct 3.", subject="Automatic reply: quick question", outbox=out)
    assert r["classification"] == "AUTO_REPLY" and state(conn, cid)["current_status"] == "emailed"
    assert guard.check(conn, "john@abcroofing.com", "followup")["allowed"] is True and out.sent == []


def test_bounce_suppresses_address(conn):
    cid = make_company(conn, high=True)
    cold_send(conn, cid)
    out = Outbox()
    r = reply(conn, "Address not found\nYour message wasn't delivered to john@abcroofing.com because the address couldn't be found.",
              sender="Mail Delivery Subsystem <mailer-daemon@googlemail.com>", subject="Delivery Status Notification (Failure)",
              thread=None, in_reply_to=None, outbox=out)
    assert r["classification"] == "DELIVERY_FAILURE" and r["company_id"] == cid
    s = state(conn, cid)
    assert s["bounced"] and not s["cold_sequence_active"]
    assert "email_suppressed" in guard.check(conn, "john@abcroofing.com", "followup")["reasons"]
    assert len(out.sent) == 1                    # high-value prospect: bounce is worth a ping


def test_duplicate_email_prevention(conn):
    cid = make_company(conn)
    assert guard.check(conn, "john@abcroofing.com", "cold")["allowed"] is True
    cold_send(conn, cid)
    chk = guard.check(conn, "john@abcroofing.com", "cold")
    assert chk["allowed"] is False and "already_contacted" in chk["reasons"]
    # same company, different address - still a duplicate first touch
    assert "already_contacted" in guard.check(conn, "office@abcroofing.com", "cold", company_id=cid)["reasons"]
    # recording the same provider message twice is a no-op
    assert cold_send(conn, cid) is None
    assert conn.execute("SELECT count(*) n FROM outreach_messages").fetchone()["n"] == 1
    assert conn.execute("SELECT count(*) n FROM outreach_history").fetchone()["n"] == 1


def test_duplicate_contact_risk_notifies_once(conn):
    cid = make_company(conn)
    cold_send(conn, cid)
    out = Outbox()
    for _ in range(3):
        guard.check_and_alert(conn, "john@abcroofing.com", "cold", send=out)
    assert len(out.sent) == 1 and "DUPLICATE" in out.texts[0]


def test_notification_exactly_once(conn):
    cid = make_company(conn)
    cold_send(conn, cid)
    out = Outbox()
    mid = "same-reply@mail.test"
    r1 = reply(conn, "How much?", mid=mid, outbox=out)
    r2 = reply(conn, "How much?", mid=mid, outbox=out)
    assert r1["status"] == "processed" and r2["status"] == "duplicate"
    assert len(out.sent) == 1
    assert conn.execute("SELECT count(*) n FROM notifications WHERE delivered").fetchone()["n"] == 1


def test_failed_delivery_is_retried_not_duplicated(conn):
    from cloudos.conversations import notify
    cid = make_company(conn)
    cold_send(conn, cid)
    reply(conn, "How much?", outbox=lambda p: (False, "HTTP 500"))
    ok = Outbox()
    assert notify.flush(conn, send=ok) == {"retried": 1, "delivered": 1}
    assert notify.flush(conn, send=ok) == {"retried": 0, "delivered": 0}
    assert len(ok.sent) == 1


def test_provider_ids_stored(conn):
    cid = make_company(conn)
    cold_send(conn, cid)
    reply(conn, "How much?", mid="abc-123@mail.test")
    rows = conn.execute("SELECT direction, provider_message_id, thread_id FROM outreach_messages ORDER BY occurred_at").fetchall()
    assert rows[0]["provider_message_id"] == "cold-1@brightreach.test" and rows[0]["thread_id"] == "T1"
    assert rows[1]["provider_message_id"] == "abc-123@mail.test" and rows[1]["thread_id"] == "T1"


def test_history_is_append_only(conn):
    cid = make_company(conn)
    cold_send(conn, cid)
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("UPDATE outreach_messages SET body = 'rewritten'")
    conn.rollback()
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("DELETE FROM outreach_messages")
    conn.rollback()


def test_conversation_history_persists_across_connections(conn):
    cid = make_company(conn)
    cold_send(conn, cid)
    reply(conn, "How much?")
    with psycopg.connect(URL, row_factory=dict_row) as other:
        assert len(store.thread(other, cid)) == 2
        assert store.facts(other, cid)[0]["fact_type"] == "question"


def test_previous_pricing_remembered(conn):
    cid = make_company(conn)
    cold_send(conn, cid)
    reply(conn, "How much?")
    cold_send(conn, cid, mid="<price-1@brightreach.test>", body="It's $1,500 one-time.", kind="pricing",
              when=T0 + timedelta(days=2), price_quoted="$1,500 one-time")
    reply(conn, "$1,500 is too expensive for us. I'd need to run it by my partner.", when=T0 + timedelta(days=3),
          in_reply_to="<price-1@brightreach.test>")
    types = {f["fact_type"]: f["fact_text"] for f in store.facts(conn, cid)}
    assert types["price_discussed"] == "We quoted $1,500 one-time"
    assert "too expensive" in types["price_objection"] and "partner" in types["decision_maker"]
    a = conn.execute("SELECT conversation_summary FROM reply_analyses ORDER BY analysis_id DESC LIMIT 1").fetchone()
    assert "We quoted $1,500 one-time" in a["conversation_summary"]
    assert state(conn, cid)["current_status"] == "negotiating"


def test_emailed_at_only_after_confirmed_send(conn):
    cid = make_company(conn)
    with pytest.raises(ValueError):
        store.record_confirmed_send(conn, company_id=cid, recipient="john@abcroofing.com", sender="m@x.test", subject="s",
                                    body="b", sent_at=T0, provider="gmail-smtp", provider_message_id="")
    conn.rollback()
    calls = []
    res = guard.confirm_send(conn, company_id=cid, recipient="john@abcroofing.com", sender="matt@brightreach.test",
                             subject="s", body="b", sent_at=T0, provider="gmail-smtp", provider_message_id=None,
                             airtable=lambda rec, when: calls.append((rec, when)))
    assert res["recorded"] is False and calls == []                 # no provider id -> not sent, no Emailed At
    res = guard.confirm_send(conn, company_id=cid, recipient="john@abcroofing.com", sender="matt@brightreach.test",
                             subject="s", body="the exact body", sent_at=T0, provider="gmail-smtp",
                             provider_message_id="<ok-1@brightreach.test>", thread_id="T9",
                             airtable=lambda rec, when: calls.append((rec, when)) or True)
    assert res["recorded"] and res["emailed_at_written"] and calls == [("recABC0000000001", T0)]
    m = conn.execute("SELECT * FROM outreach_messages").fetchone()
    assert m["body"] == "the exact body" and m["thread_id"] == "T9" and str(m["company_id"]) == cid
    assert state(conn, cid)["current_status"] == "emailed"


def test_status_transitions(conn):
    cid = make_company(conn)
    cold_send(conn, cid)
    reply(conn, "Sounds interesting")
    reply(conn, "Can you call me Wednesday at 3pm?")
    reply(conn, "Let's do it. Send the invoice.")
    path = [r["to_status"] for r in conn.execute("SELECT to_status FROM company_status_transitions WHERE company_id = %s "
                                                  "ORDER BY id", (cid,)).fetchall()]
    assert path == ["ready", "emailed", "interested", "meeting_requested", "proposal_needed"]
    # asking the price again later never drags status backwards
    reply(conn, "Remind me, how much was it?")
    assert state(conn, cid)["current_status"] == "proposal_needed"
    with pytest.raises(sm.TransitionError):
        sm.check("won", "emailed")
    # contradictory states are impossible at the database level
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("UPDATE company_conversation_state SET current_status = 'won', cold_sequence_active = true "
                     "WHERE company_id = %s", (cid,))
    conn.rollback()


def test_owner_resolves_review_and_marks_won(conn):
    cid = make_company(conn)
    cold_send(conn, cid)
    reply(conn, "Yeah.")
    guard.owner_set_status(conn, cid, "interested", "Matt read it: they want pricing")
    assert state(conn, cid)["current_status"] == "interested"
    assert reports.attention_queue(conn) == []
    out = Outbox()
    guard.mark_won(conn, cid, setup_usd=1500, monthly_usd=400, send=out)
    assert state(conn, cid)["current_status"] == "won" and len(out.sent) == 1 and "WON" in out.texts[0]


def test_human_attention_queue_only_needs_human(conn):
    a = make_company(conn)
    b = make_company(conn, "Smith HVAC", "smithhvac.com", "sam@smithhvac.com", rec="recSMITH00000001")
    c = make_company(conn, "Joe's Plumbing", "joesplumbing.com", "joe@joesplumbing.com", rec="recJOE000000001")
    cold_send(conn, a)
    cold_send(conn, b, to="sam@smithhvac.com", mid="<cold-2@brightreach.test>", thread="T2")
    cold_send(conn, c, to="joe@joesplumbing.com", mid="<cold-3@brightreach.test>", thread="T3")
    reply(conn, "What does this cost?")
    reply(conn, "Can you call Wednesday?", sender="sam@smithhvac.com", thread="T2", in_reply_to="<cold-2@brightreach.test>")
    reply(conn, "Not interested", sender="joe@joesplumbing.com", thread="T3", in_reply_to="<cold-3@brightreach.test>")
    reply(conn, "I am out of the office", subject="Out of Office", sender="joe@joesplumbing.com", thread="T3",
          in_reply_to="<cold-3@brightreach.test>")
    q = reports.attention_queue(conn)
    assert {i["company"] for i in q} == {"ABC Roofing", "Smith HVAC"}
    assert all(i["recommended_action"] and i["reason_human_needed"] for i in q)
    rep = reports.status_report(conn, day=(T0 + timedelta(days=1)).date())
    assert rep["today"]["replies"] == 3 and rep["today"]["price_questions"] == 1 and rep["today"]["meeting_requests"] == 1
    assert rep["today"]["lost"] == 1


def test_feedback_waits_for_real_samples(conn):
    a = make_company(conn)
    cold_send(conn, a)
    reply(conn, "How much?")
    fb = reports.feedback(conn)
    roof = fb["by_industry"]["roofing"]
    assert roof["sent"] == 1 and roof["positive_replies"] == 1
    assert roof["recommendation"] == "insufficient_data"            # one send is not a signal


# ------------------------------------------------------- gmail sync (fake IMAP)
class FakeIMAP:
    """Just enough Gmail IMAP: All Mail with UIDs + X-GM-THRID, and Drafts APPEND."""
    store: dict = {}
    appended: list = []

    def __init__(self, host, port):
        pass

    def login(self, u, p):
        return "OK", [b""]

    def select(self, box, readonly=False):
        return "OK", [b"1"]

    def status(self, box, what):
        return "OK", [b'"[Gmail]/All Mail" (UIDVALIDITY 777)']

    def uid(self, cmd, *args):
        if cmd == "SEARCH":
            if args[1].startswith("UID"):
                lo = int(args[1].split()[1].split(":")[0])
                ids = [u for u in self.store if u >= lo] or [max(self.store)]
            else:
                ids = list(self.store)
            return "OK", [" ".join(map(str, sorted(ids))).encode()]
        uids = [int(x) for x in args[0].split(",")]
        out = []
        for u in uids:
            thr, raw, *lab = self.store[u]
            labels = lab[0] if lab else ""
            if "HEADER.FIELDS" in args[1]:
                raw = raw.split(b"\r\n\r\n", 1)[0] + b"\r\n\r\n"
            out.append((f"{u} (UID {u} X-GM-THRID {thr} X-GM-LABELS ({labels}) BODY[] {{{len(raw)}}}".encode(), raw))
            out.append(b")")
        return "OK", out

    def append(self, box, flags, when, data):
        FakeIMAP.appended.append((box, data))
        return "OK", [b""]

    def logout(self):
        return "BYE", [b""]


def _raw(frm, to, subject, body, mid, date="Sat, 20 Sep 2026 15:00:00 +0000", extra=""):
    return (f"From: {frm}\r\nTo: {to}\r\nSubject: {subject}\r\nDate: {date}\r\nMessage-ID: <{mid}>\r\n{extra}"
            f"Content-Type: text/plain; charset=utf-8\r\n\r\n{body}\r\n").encode()


def test_gmail_sync_end_to_end(conn, monkeypatch):
    monkeypatch.setenv("EMAIL_ADDRESS", "matt@brightreach.test")
    monkeypatch.setenv("EMAIL_APP_PASSWORD", "x")
    from cloudos.conversations import gmail_sync
    cid = make_company(conn)
    FakeIMAP.appended = []
    FakeIMAP.store = {
        1: ("900", _raw("Matt <matt@brightreach.test>", "john@abcroofing.com", "quick question about ABC Roofing",
                        "Hi,\nNo quote form on your site. Worth a quick look?", "cold-9@brightreach.test",
                        extra="X-Leadgen-Outreach: office-agent-first\r\nX-Leadgen-Record-ID: recABC0000000001\r\n")),
        2: ("555", _raw("Mom <mom@family.test>", "matt@brightreach.test", "dinner?", "call me about dinner, how much was the cake?",
                        "personal-1@family.test")),
        3: ("900", _raw("John <john@abcroofing.com>", "matt@brightreach.test", "Re: quick question about ABC Roofing",
                        "This sounds interesting. How much?\n\nOn Sat, Sep 20, 2026 Matt wrote:\n> Worth a quick look?",
                        "reply-9@abcroofing.com", date="Sun, 28 Sep 2026 15:00:00 +0000",
                        extra="In-Reply-To: <cold-9@brightreach.test>\r\nReferences: <cold-9@brightreach.test>\r\n")),
    }
    out = Outbox()
    res = gmail_sync.sync(conn, send=out, imap_factory=FakeIMAP, repair_emailed_at=False)
    assert res["scanned"] == 3 and res["relevant"] == 2 and res["outbound_recorded"] == 1
    assert res["inbound"] == {"PRICE_QUESTION": 1}
    assert conn.execute("SELECT count(*) n FROM outreach_messages WHERE sender LIKE '%%family%%'").fetchone()["n"] == 0
    m = conn.execute("SELECT * FROM outreach_messages WHERE direction='outbound'").fetchone()
    assert m["thread_id"] == "900" and m["kind"] == "cold" and "No quote form" in m["body"] and str(m["company_id"]) == cid
    assert state(conn, cid)["current_status"] == "interested"
    assert len(FakeIMAP.appended) == 1
    draft = FakeIMAP.appended[0][1]
    assert b"$1,500" in draft and b"In-Reply-To: <reply-9@abcroofing.com>" in draft
    # second run: nothing new, nothing repeated
    res2 = gmail_sync.sync(conn, send=out, imap_factory=FakeIMAP, repair_emailed_at=False)
    assert res2["scanned"] == 0 and len(FakeIMAP.appended) == 1
    # Matt sends the draft from Gmail -> recorded as the pricing send, draft closes
    FakeIMAP.store[4] = ("900", _raw("Matt <matt@brightreach.test>", "john@abcroofing.com", "Re: pricing for ABC Roofing",
                                     "It's $1,500 one-time.", "sent-draft-1@brightreach.test",
                                     date="Sun, 28 Sep 2026 17:00:00 +0000"))
    gmail_sync.sync(conn, send=out, imap_factory=FakeIMAP, repair_emailed_at=False)
    assert conn.execute("SELECT state FROM outreach_drafts WHERE kind='pricing'").fetchone()["state"] == "sent"
    assert state(conn, cid)["current_price"] == "$1,500"
    assert len(out.sent) == 1          # one reply -> exactly one notification across three runs


def test_hand_sent_pipeline_draft_is_tracked(conn, monkeypatch):
    """Regression (2026-09-29, Price Busters): a hand-sent draft carries only the Gmail 'Leads' label,
    goes to a freemail address, and the company is in no database. Its reply must still be caught."""
    monkeypatch.setenv("EMAIL_ADDRESS", "matt@brightreach.test")
    monkeypatch.setenv("EMAIL_APP_PASSWORD", "x")
    from cloudos.conversations import gmail_sync
    FakeIMAP.appended = []
    FakeIMAP.store = {
        10: ("777", _raw("Matt <matt@brightreach.test>", "pbhvac@gmail.com", "Missed calls at Price Busters Heating & Air",
                         "Hi, quick note about missed calls. Worth a look?", "hand-1@brightreach.test"), '"\\Sent" LG/Processed Leads'),
        11: ("777", _raw("PB <pbhvac@gmail.com>", "matt@brightreach.test", "Re: Missed calls at Price Busters Heating & Air",
                         "What would this cost us?", "pb-reply-1@gmail.com", date="Sun, 28 Sep 2026 15:00:00 +0000",
                         extra="In-Reply-To: <hand-1@brightreach.test>\r\n")),
        12: ("778", _raw("Matt <matt@brightreach.test>", "friend@gmail.com", "lunch", "lunch friday?", "p-2@brightreach.test")),
    }
    res = gmail_sync.sync(conn, send=Outbox(), imap_factory=FakeIMAP, repair_emailed_at=False, push_drafts=False)
    assert res["outbound_recorded"] == 1 and res["inbound"] == {"PRICE_QUESTION": 1}
    c = conn.execute("SELECT * FROM companies WHERE discovery_source = 'gmail_outreach'").fetchone()
    assert c["company_name"] == "Price Busters Heating & Air" and c["normalized_domain"] is None
    assert "already_contacted" in guard.check(conn, "pbhvac@gmail.com", "cold")["reasons"]
    assert conn.execute("SELECT count(*) n FROM outreach_messages WHERE recipient = 'friend@gmail.com'").fetchone()["n"] == 0


# ============================== regressions from the 2026-09-29 adversarial review ==============================
def _connect():
    return psycopg.connect(URL, row_factory=dict_row)


# 1) the send guard is wired into the real senders and fails closed
def test_real_senders_consult_the_guard(conn):
    cid = make_company(conn)
    cold_send(conn, cid)
    reply(conn, "Remove me from your list.")
    for kind in ("cold", "followup", "reply"):
        assert guard.outreach_allowed("john@abcroofing.com", kind, connect=_connect)["allowed"] is False
    fresh = make_company(conn, "Fresh Plumbing", "freshplumb.com", "amy@freshplumb.com", rec="recFRESH0000001")
    assert guard.outreach_allowed("amy@freshplumb.com", "cold", connect=_connect)["allowed"] is True
    assert fresh


def test_check_command_exit_codes(conn, capsys):
    import importlib.util
    spec = importlib.util.spec_from_file_location("outreach_replies_cli", MIGRATIONS.parents[1] / "outreach_replies.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    cid = make_company(conn)
    assert cli._check("john@abcroofing.com", "cold", None, connect=_connect) == 0
    cold_send(conn, cid)
    reply(conn, "Please stop.")
    assert cli._check("john@abcroofing.com", "cold", None, connect=_connect) == 3        # blocked
    assert cli._check("john@abcroofing.com", "reply", "not-a-uuid", connect=_connect) == 3  # bad input -> error

    def down():
        raise OSError("database unreachable")
    assert cli._check("new@nowhere.test", "cold", None, connect=down) == 3            # error is never "go"
    assert '"guard_error"' in capsys.readouterr().out


def test_guard_matches_plus_addresses_and_gmail_dots(conn):
    store.suppress(conn, reason="unsubscribe", email="John.Smith+promo@googlemail.com")
    store.suppress(conn, reason="unsubscribe", email="owner+x@acme-roof.test")
    conn.commit()
    for variant in ("johnsmith@gmail.com", "john.smith@gmail.com", "JOHN.SMITH+other@gmail.com", "j.o.h.n.smith@googlemail.com"):
        assert "email_suppressed" in guard.check(conn, variant, "cold")["reasons"], variant
    assert "email_suppressed" in guard.check(conn, "Owner@Acme-Roof.test", "cold")["reasons"]
    assert "email_suppressed" not in guard.check(conn, "o.wner@acme-roof.test", "cold")["reasons"]  # dots only fold for Gmail
    assert guard.check(conn, "janesmith@gmail.com", "cold")["allowed"] is True


def test_lead_engine_handoff_holds_back_suppressed_and_fails_closed(conn):
    from cloudos.leadgen import handoff
    ok = make_company(conn, "Good Roofing", "goodroof.test", "ann@goodroof.test", rec="recGOOD00000001")
    bad = make_company(conn, "Gone Roofing", "goneroof.test", "bob@goneroof.test", rec="recGONE00000001")
    conn.execute("UPDATE companies SET outreach_status = 'outreach_ready', handoff_ref = NULL")
    store.suppress(conn, reason="hard_bounce", email="bob@goneroof.test")
    conn.commit()

    class FakeAirtable:
        def __init__(self):
            self.created = []

        def all(self, fields):
            return []

        def create(self, rows):
            self.created += rows
            return [{"id": f"recNEW{i:09d}"} for i, _ in enumerate(rows)]

    cfg = {"handoff": {"offer": "x", "status": "Ready", "airtable_ready_window": 10, "airtable_record_ceiling": 900}}
    at = FakeAirtable()
    res = handoff.sync_and_handoff(conn, at, cfg)
    assert [r["Email"] for r in at.created] == ["ann@goodroof.test"] and res["blocked_by_guard"] == {"suppressed": 1}
    status = {str(r["company_id"]): r["outreach_status"] for r in conn.execute("SELECT company_id, outreach_status FROM companies")}
    assert status[ok] == "handed_off" and status[bad] == "do_not_contact"

    # guard broken (function missing) -> nothing at all is handed off
    conn.execute("UPDATE companies SET outreach_status = 'outreach_ready' WHERE company_id = %s", (ok,))
    conn.execute("ALTER FUNCTION outreach_send_check(text, text, uuid) RENAME TO outreach_send_check_off")
    conn.commit()
    try:
        at2 = FakeAirtable()
        res2 = handoff.sync_and_handoff(conn, at2, cfg)
        assert at2.created == [] and res2.get("guard_error")
    finally:
        conn.rollback()
        conn.execute("ALTER FUNCTION outreach_send_check_off(text, text, uuid) RENAME TO outreach_send_check")
        conn.commit()


# 2 + 7) negated interest and plain opt-outs, end to end
def test_negated_interest_is_lost_not_interested(conn):
    cid = make_company(conn)
    cold_send(conn, cid)
    out = Outbox()
    r = reply(conn, "I'm not really interested", outbox=out)
    assert r["classification"] == "NOT_INTERESTED" and state(conn, cid)["current_status"] == "lost"
    assert conn.execute("SELECT count(*) n FROM outreach_drafts").fetchone()["n"] == 0 and out.sent == []


@pytest.mark.parametrize("body", ["Please stop.", "unsub", "Kindly remove my address from your records",
                                  "not interested and do not want to hear from you again"])
def test_plain_opt_out_suppresses(conn, body):
    cid = make_company(conn)
    cold_send(conn, cid)
    r = reply(conn, body)
    assert r["classification"] == "UNSUBSCRIBE" and state(conn, cid)["do_not_contact"]
    assert conn.execute("SELECT count(*) n FROM email_suppressions WHERE email = 'john@abcroofing.com'").fetchone()["n"] == 1


# 4) an out-of-office opener doesn't swallow a buying signal
def test_out_of_office_with_buying_intent_notifies(conn):
    cid = make_company(conn)
    cold_send(conn, cid)
    out = Outbox()
    r = reply(conn, "I'm out of the office next week but yes interested, how much?", outbox=out)
    assert r["classification"] == "PRICE_QUESTION" and len(out.sent) == 1
    assert state(conn, cid)["current_status"] == "interested"


# 5) only permanent bounces suppress
def test_delay_notice_and_human_undelivered_do_not_suppress(conn):
    cid = make_company(conn)
    cold_send(conn, cid)
    r = reply(conn, "Delivery incomplete\nThere was a temporary problem delivering your message to john@abcroofing.com. "
                    "Gmail will retry for 47 more hours. You'll be notified if the delivery fails permanently.\n"
                    "Final-Recipient: rfc822; john@abcroofing.com\nAction: delayed\nStatus: 4.4.1",
              sender="Mail Delivery Subsystem <mailer-daemon@googlemail.com>", subject="Delivery Status Notification (Delay)")
    assert r["classification"] == "AUTO_REPLY"
    s = state(conn, cid)
    assert not s["bounced"] and s["current_status"] == "emailed"
    r = reply(conn, "Undelivered to our office manager, forwarding it. We're interested, how much?",
              subject="Undelivered: quick question about ABC Roofing")
    assert r["classification"] == "PRICE_QUESTION"
    assert conn.execute("SELECT count(*) n FROM email_suppressions").fetchone()["n"] == 0
    assert not state(conn, cid)["bounced"]


# 6) an opt-out we can't tie to one company for certain still suppresses
def test_unsubscribe_matched_only_by_domain_suppresses_sender_and_company(conn):
    cid = make_company(conn, email="info@abcroofing.com")
    cold_send(conn, cid, to="info@abcroofing.com")
    out = Outbox()
    r = reply(conn, "Please remove us from your list.", sender="owner@abcroofing.com", thread=None, in_reply_to=None,
              outbox=out)
    assert r["status"] == "needs_review_unmatched" and r["suppressed"] == "company"
    assert "email_suppressed" in guard.check(conn, "owner@abcroofing.com", "cold")["reasons"]
    assert "company_suppressed" in guard.check(conn, "info@abcroofing.com", "followup")["reasons"]
    assert len(out.sent) == 1                        # the owner still hears about it, to confirm the company


def test_unsubscribe_from_unknown_sender_suppresses_the_address(conn):
    r = reply(conn, "unsubscribe", sender="stranger@elsewhere.test", thread=None, in_reply_to=None)
    assert r["status"] == "needs_review_unmatched" and r["suppressed"] == "address"
    assert "email_suppressed" in guard.check(conn, "stranger@elsewhere.test", "cold")["reasons"]


def test_unmatched_permanent_bounce_suppresses_the_dead_address(conn):
    r = reply(conn, "Address not found\nYour message wasn't delivered to ghost@nowhere.test because the address "
                    "couldn't be found.", sender="mailer-daemon@googlemail.com", thread=None, in_reply_to=None,
              subject="Delivery Status Notification (Failure)")
    assert r["suppressed"] == "bounce"
    assert "email_suppressed" in guard.check(conn, "ghost@nowhere.test", "cold")["reasons"]


# 3) one poisoned email never blocks the mailbox
def test_nul_byte_in_reply_is_stored_clean(conn):
    cid = make_company(conn)
    cold_send(conn, cid)
    r = reply(conn, "How much\x00 is it?", subject="Re: quick\x00 question")
    assert r["classification"] == "PRICE_QUESTION"
    m = conn.execute("SELECT body, subject FROM outreach_messages WHERE direction = 'inbound'").fetchone()
    assert m["body"] == "How much is it?" and "\x00" not in m["subject"]


def test_poison_message_is_quarantined_and_later_mail_still_flows(conn, monkeypatch):
    monkeypatch.setenv("EMAIL_ADDRESS", "matt@brightreach.test")
    monkeypatch.setenv("EMAIL_APP_PASSWORD", "x")
    from cloudos.conversations import gmail_sync
    make_company(conn)
    FakeIMAP.appended = []
    FakeIMAP.store = {
        1: ("900", _raw("Matt <matt@brightreach.test>", "john@abcroofing.com", "quick question about ABC Roofing",
                        "Worth a quick look?", "cold-p@brightreach.test",
                        extra="X-Leadgen-Outreach: office-agent-first\r\nX-Leadgen-Record-ID: recABC0000000001\r\n")),
        2: ("900", _raw("John <john@abcroofing.com>", "matt@brightreach.test", "Re: quick question about ABC Roofing",
                        "Sounds \x00interesting\x00", "poison-1@abcroofing.com", date="Sun, 28 Sep 2026 15:00:00 +0000",
                        extra="In-Reply-To: <cold-p@brightreach.test>\r\n")),
        3: ("900", _raw("John <john@abcroofing.com>", "matt@brightreach.test", "Re: quick question about ABC Roofing",
                        "How much?", "good-1@abcroofing.com", date="Sun, 28 Sep 2026 16:00:00 +0000",
                        extra="In-Reply-To: <cold-p@brightreach.test>\r\n")),
    }
    # the NUL alone is now harmless; also force a hard failure on message 2 to prove isolation
    real = gmail_sync.process_inbound

    def flaky(conn_, msg, **kw):
        if msg["provider_message_id"] == "poison-1@abcroofing.com":
            conn_.execute("SELECT 1/0")            # a real database error mid-message
        return real(conn_, msg, **kw)
    monkeypatch.setattr(gmail_sync, "process_inbound", flaky)
    out = Outbox()
    res = gmail_sync.sync(conn, send=out, imap_factory=FakeIMAP, repair_emailed_at=False, push_drafts=False)
    assert res["quarantined"] == [{"uid": 2, "error": "DivisionByZero"}]
    assert res["inbound"] == {"PRICE_QUESTION": 1}                                  # message 3 still processed
    assert conn.execute("SELECT last_uid FROM reply_poll_state").fetchone()["last_uid"] == 3   # moved past it
    assert any("ONE EMAIL SKIPPED" in t for t in out.texts)
    assert not any("Sounds" in t for t in out.texts)                                 # no message text in the notice
    res2 = gmail_sync.sync(conn, send=out, imap_factory=FakeIMAP, repair_emailed_at=False, push_drafts=False)
    assert res2["scanned"] == 0                                                      # never retried forever

    # and without the forced failure, the NUL message itself goes through cleanly
    monkeypatch.setattr(gmail_sync, "process_inbound", real)
    FakeIMAP.store[4] = ("900", _raw("John <john@abcroofing.com>", "matt@brightreach.test", "Re: quick",
                                     "Also\x00 can you call me Friday?", "nul-2@abcroofing.com",
                                     date="Sun, 28 Sep 2026 17:00:00 +0000", extra="In-Reply-To: <cold-p@brightreach.test>\r\n"))
    res3 = gmail_sync.sync(conn, send=out, imap_factory=FakeIMAP, repair_emailed_at=False, push_drafts=False)
    assert "quarantined" not in res3 and res3["inbound"] == {"MEETING_REQUEST": 1}


def test_database_outage_does_not_skip_mail(conn, monkeypatch):
    monkeypatch.setenv("EMAIL_ADDRESS", "matt@brightreach.test")
    monkeypatch.setenv("EMAIL_APP_PASSWORD", "x")
    from cloudos.conversations import gmail_sync
    make_company(conn)
    cold_send(conn, make_company(conn, "Other Co", "other.test", "x@other.test", rec="recOTHER000001"), to="x@other.test",
              mid="<o-1@brightreach.test>", thread="901")
    FakeIMAP.store = {5: ("901", _raw("X <x@other.test>", "matt@brightreach.test", "Re: hi", "How much?", "o-r@other.test",
                                      extra="In-Reply-To: <o-1@brightreach.test>\r\n"))}

    def down(*a, **k):
        raise psycopg.OperationalError("server closed the connection")
    monkeypatch.setattr(gmail_sync, "process_inbound", down)
    with pytest.raises(psycopg.OperationalError):
        gmail_sync.sync(conn, send=Outbox(), imap_factory=FakeIMAP, repair_emailed_at=False, push_drafts=False)
    conn.rollback()
    assert conn.execute("SELECT count(*) n FROM reply_poll_state").fetchone()["n"] == 0   # position not advanced


# cheap extra: two pollers at once never push the same draft twice
def test_draft_push_skips_a_draft_another_poller_holds(conn, monkeypatch):
    monkeypatch.setenv("EMAIL_ADDRESS", "matt@brightreach.test")
    monkeypatch.setenv("EMAIL_APP_PASSWORD", "x")
    from cloudos.conversations import gmail_sync
    cid = make_company(conn)
    cold_send(conn, cid)
    reply(conn, "How much?")
    FakeIMAP.appended = []
    conn.execute("SET lock_timeout = '3s'")     # a regression must fail fast, not hang waiting on the lock
    conn.commit()
    with _connect() as other:
        other.execute("SELECT 1 FROM outreach_drafts FOR UPDATE")         # the other poller is mid-push
        assert gmail_sync.push_pending_drafts(conn, imap_factory=FakeIMAP) == 0
        other.rollback()
    assert FakeIMAP.appended == []
    assert gmail_sync.push_pending_drafts(conn, imap_factory=FakeIMAP) == 1
    assert gmail_sync.push_pending_drafts(conn, imap_factory=FakeIMAP) == 0
    assert len(FakeIMAP.appended) == 1
