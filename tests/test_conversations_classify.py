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
