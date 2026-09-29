"""Reply classifier + text helpers: pure logic, no database."""
from __future__ import annotations

import pytest

from cloudos.conversations import status as sm
from cloudos.conversations.classify import classify
from cloudos.conversations.text import msgid, msgids, strip_quoted

COLD = {"direction": "outbound", "kind": "cold", "subject": "quick question about ABC Roofing",
        "body": "Hi,\nNoticed your site only has a contact form. Worth a quick look? Also, who handles after-hours calls?",
        "occurred_at": "2026-09-20", "offer": "Missed-call follow-up"}
PRICING = {"direction": "outbound", "kind": "pricing", "subject": "re", "body": "It's $1,500 one-time. Want to go ahead?",
           "occurred_at": "2026-09-22", "price_quoted": "$1,500 one-time"}
ASK = {"direction": "outbound", "kind": "followup", "subject": "re", "body": "Want me to send over a 2-minute demo video?",
       "occurred_at": "2026-09-22", "cta": "Want me to send over a 2-minute demo video?"}


def c(body, thread=(COLD,), **kw):
    return classify({"subject": kw.get("subject", "Re: x"), "body": body, "sender": kw.get("sender", "john@abcroofing.com"),
                     "headers": kw.get("headers", {})}, list(thread), our_addresses={"matt@brightreach.test"})


@pytest.mark.parametrize("body,label", [
    ("This sounds interesting. How much?", "PRICE_QUESTION"),
    ("Sounds useful. What does something like this cost?", "PRICE_QUESTION"),
    ("Remove me from your list.", "UNSUBSCRIBE"),
    ("Stop emailing me or I'll report you to the FTC", "UNSUBSCRIBE"),
    ("Talk to Mike at mike@example.com.", "REFERRAL"),
    ("I'm not the owner, talk to Sarah", "REFERRAL"),
    ("Not interested, thanks.", "NOT_INTERESTED"),
    ("Can you call me Wednesday at 3pm?", "MEETING_REQUEST"),
    ("Maybe in November, we're in busy season.", "NOT_NOW"),
    ("We already have an answering service.", "OBJECTION"),
    ("No need to call, just email me the details", "MORE_INFORMATION"),
    ("Let's do it. Send the invoice.", "READY_TO_BUY"),
    ("Talk to you soon, sounds good", "INTERESTED"),                     # not a referral
    ("Your email went to my spam folder but sounds good", "INTERESTED"),  # not an unsubscribe
])
def test_labels(body, label):
    assert c(body).label == label


@pytest.mark.parametrize("body,reason", [
    ("Yeah.", "affirmation_ambiguous"),
    ("ok", "affirmation_ambiguous"),
    ("Not interested. How much though?", "conflict_negative_and_positive"),
    ("Who is this? How did you get my email?", "legal_or_angry"),
    ("Hmm.", "no_recognised_intent"),
])
def test_ambiguous_goes_to_review(body, reason):
    r = c(body)
    assert r.label == "NEEDS_REVIEW" and r.needs_review_reason == reason


def test_yes_is_read_against_the_thread():
    assert c("Yeah that works.", thread=(COLD, PRICING)).label == "READY_TO_BUY"
    assert c("Yes please", thread=(COLD, ASK)).label == "MORE_INFORMATION"
    assert c("Yeah.", thread=(COLD,)).label == "NEEDS_REVIEW"
    assert c("Yes", thread=()).label == "NEEDS_REVIEW"


def test_our_quoted_pitch_is_ignored():
    body = "Sure, sounds good\n\nOn Sat, Sep 20, 2026 at 10:00 AM Matt <matt@x.com>\nwrote:\n> how much does it cost? call me"
    assert strip_quoted(body) == "Sure, sounds good"
    assert c("Not for us.\n> Want pricing? Let's set up a call").label == "NOT_INTERESTED"


def test_machines():
    assert c("I am out of the office until Oct 3.", subject="Automatic reply: hi").label == "AUTO_REPLY"
    assert c("hi", headers={"Auto-Submitted": "auto-replied"}).label == "AUTO_REPLY"
    assert c("Address not found", subject="Delivery Status Notification (Failure)",
             sender="mailer-daemon@googlemail.com").label == "DELIVERY_FAILURE"


def test_facts_extracted():
    r = c("$1,500 is too expensive for us. I'd need to run it by my partner. We only want lead follow-up, not a website rebuild.",
          thread=(COLD, PRICING))
    types = {f.fact_type for f in r.facts}
    assert {"price_objection", "decision_maker", "service_interest", "scope_exclusion"} <= types


def test_message_ids():
    assert msgid("<ABC@Mail.com>") == "abc@mail.com"
    assert msgids("<a@x> <b@y>") == ["a@x", "b@y"]


def test_status_machine():
    assert sm.target_for("PRICE_QUESTION", "emailed") == "interested"
    assert sm.target_for("PRICE_QUESTION", "proposal_sent") == "proposal_sent"      # never backwards
    assert sm.target_for("INTERESTED", "do_not_contact") == "do_not_contact"        # terminal
    assert sm.target_for("READY_TO_BUY", "proposal_sent") == "negotiating"
    assert sm.target_for("AUTO_REPLY", "emailed") == "emailed"
    with pytest.raises(sm.TransitionError):
        sm.check("do_not_contact", "interested")
    sm.check("do_not_contact", "interested", owner_override=True)
    assert not sm.cold_sequence_allowed("won")


def test_lead_engine_feedback_only_reorders(monkeypatch):
    from cloudos.conversations import reports
    from cloudos.leadgen.store import order_by_feedback

    class Conn:
        def execute(self, *a, **k):
            return None

    cands = [{"query": q, "industry": i} for q, i in (("a", "gym"), ("b", "hvac"), ("c", "roofing"), ("d", ""))]
    monkeypatch.setattr(reports, "feedback", lambda conn: {"lead_engine_priority": {"hvac": "increase_priority",
                                                                                     "gym": "decrease_priority"}})
    assert [c["query"] for c in order_by_feedback(Conn(), cands)] == ["b", "c", "d", "a"]

    def boom(conn):
        raise RuntimeError("reply tables missing")
    monkeypatch.setattr(reports, "feedback", boom)
    assert order_by_feedback(Conn(), cands) == cands


# --------------------------------------------- regressions from the 2026-09-29 adversarial review
@pytest.mark.parametrize("body", [
    "I'm not really interested", "Not currently interested", "We're not that interested", "Not very interested",
    "I'm no longer interested", "Would not be interested", "I don't think we're interested", "Not sure we're interested",
    "I’m not really interested, thanks", "We are not at all interested.", "Honestly never been less keen",
])
def test_negated_interest_is_a_no(body):
    r = c(body)
    assert r.label == "NOT_INTERESTED", (body, r.label)


@pytest.mark.parametrize("body,label", [
    ("Not sure yet, but interested. How much?", "PRICE_QUESTION"),    # a clause break keeps the interest
    ("Couldn't be more interested!", "INTERESTED"),
])
def test_negation_does_not_cross_a_clause(body, label):
    assert c(body).label == label


@pytest.mark.parametrize("body", [
    "I'd love to stop getting these", "Please stop.", "Stop", "STOP", "quit emailing me", "Please don't send more emails",
    "No further emails please", "I do not wish to receive further emails", "Please do not send me any more messages",
    "Cease all communication", "Kindly remove my address from your records", "please delete my info", "unsub",
    "not interested and do not want to hear from you again",
])
def test_clear_opt_outs_are_unsubscribe(body):
    r = c(body)
    assert r.label == "UNSUBSCRIBE", (body, r.label, r.needs_review_reason)


def test_our_quoted_opt_out_footer_never_counts():
    pitch = dict(COLD, body="Worth a quick look? Reply STOP and I'll remove you from my list.")
    body = ("Sounds interesting, how much?\n\nOn Sat, Sep 20, 2026 at 9:00 AM Matt <matt@brightreach.test> wrote:\n"
            "> Worth a quick look? Reply STOP and I'll remove you from my list.")
    assert c(body, thread=(pitch,)).label == "PRICE_QUESTION"
    assert c("> Reply STOP and I'll remove you\n", thread=(pitch,)).label == "NEEDS_REVIEW"   # nothing new written


@pytest.mark.parametrize("subject,body,label", [
    ("Re: x", "I'm out of the office next week but yes interested, how much?", "PRICE_QUESTION"),
    ("Re: x", "We have received your email and I'm interested. how much?", "PRICE_QUESTION"),
    ("Away from desk but interested", "", "INTERESTED"),
    ("Away from desk but interested", "Sent from my iPhone", "INTERESTED"),
])
def test_human_intent_beats_out_of_office_wording(subject, body, label):
    assert c(body, subject=subject).label == label


@pytest.mark.parametrize("subject,body,headers", [
    ("Automatic reply: quick question", "I am out of the office until Oct 3.", {}),
    ("Out of Office: Re: Interested in more booked jobs?", "I am out of the office until Monday.", {}),  # our subject
    ("Re: x", "I am away. For urgent matters call my cell or contact Mike at mike@example.com.", {}),
    ("Re: x", "Thanks, interested? We'll get back to you.", {"Auto-Submitted": "auto-replied"}),
    ("Re: x", "Got it", {"Precedence": "auto_reply"}),
])
def test_real_auto_replies_stay_auto(subject, body, headers):
    assert c(body, subject=subject, headers=headers).label == "AUTO_REPLY"


DAEMON = "Mail Delivery Subsystem <mailer-daemon@googlemail.com>"


@pytest.mark.parametrize("subject,body", [
    ("Delivery Status Notification (Delay)",
     "Delivery incomplete\nThere was a temporary problem delivering your message to someone@example.com. Gmail will "
     "retry for 47 more hours. You'll be notified if the delivery fails permanently."),
    ("Delivery Status Notification (Delay)", "Reporting-MTA: dns; googlemail.com\nAction: delayed\nStatus: 4.4.1"),
    ("Undelivered Mail Returned to Sender", "Action: delayed\nStatus: 4.7.0 greylisted, try again later"),
])
def test_delayed_delivery_is_not_a_bounce(subject, body):
    r = c(body, subject=subject, sender=DAEMON)
    assert r.label == "AUTO_REPLY" and "temporary" in r.interpretation


@pytest.mark.parametrize("subject,body", [
    ("Delivery Status Notification (Failure)", "Address not found\nYour message wasn't delivered to someone@example.com."),
    ("Undelivered Mail Returned to Sender", "Action: failed\nStatus: 5.1.1\nDiagnostic-Code: smtp; 550 5.1.1 user unknown"),
    ("Delivery Status Notification (Delay)", "Action: failed\nStatus: 5.4.7 (delivery time expired)"),
])
def test_permanent_failure_is_a_bounce(subject, body):
    assert c(body, subject=subject, sender=DAEMON).label == "DELIVERY_FAILURE"


def test_person_writing_undelivered_is_never_a_bounce():
    r = c("Your first email was undelivered to my partner, but I'm interested. How much?",
          subject="Undelivered: your email", sender="john@abcroofing.com")
    assert r.label == "PRICE_QUESTION"
    # a real delivery report carries multipart/report even from an unusual sender
    r = c("Action: failed\nStatus: 5.1.1", subject="Undeliverable: hello", sender="notices@mx.example.com",
          headers={"Content-Type": 'multipart/report; report-type="delivery-status"; boundary="x"'})
    assert r.label == "DELIVERY_FAILURE"


def test_unclear_mail_server_notice_goes_to_review_not_suppression():
    r = c("Something happened with your message.", subject="Mail delivery subsystem", sender=DAEMON)
    assert r.label == "NEEDS_REVIEW" and r.needs_review_reason == "delivery_notice_unclear"


def test_nul_and_control_characters_are_removed():
    from cloudos.conversations.text import clean
    assert clean("How\x00 much?\x07") == "How much?"
    assert clean({"a": ["x\x00y", ("\x00",)], "n": 1}) == {"a": ["xy", ("",)], "n": 1}
    assert clean("tab\tand\nnewline stay") == "tab\tand\nnewline stay"
    assert clean("bad \ud800 surrogate") == "bad  surrogate"
    assert c("How much?\x00").label == "PRICE_QUESTION"
