"""Filter tests built from a real 14-day sample of the owner's actual inbox.

Every REAL_INBOX row below is a genuine subject/sender/snippet triple that was
sitting in the mailbox this filter is for. The point of testing against real
mail rather than invented examples is that invented spam is always easy to
reject; the mail that actually causes false positives is a marketing blast
whose snippet happens to say "they understood the assignment".
"""
import pytest

from cloudos.email_alerts.filter import classify

# Headers a normal mailing-list/marketing sender carries.
BULK = {"List-Unsubscribe": "<https://example.test/u>", "Precedence": "bulk"}
# GitHub notifications are technically bulk too -- that must not veto them.
GH_BULK = {"List-Id": "job-radar.mattumali579.github.com",
           "List-Unsubscribe": "<https://github.com/u>"}
OWNER = ("matt.umali579@gmail.com", "mattumali579@gmail.com")

# (subject, sender, snippet, headers, should_notify)
REAL_INBOX = [
    # --- must notify -------------------------------------------------------
    ("Nathan Lott paid you $35.00", "venmo@venmo.com",
     "Nathan Lott paid you $35.00 Teaching See transaction Money credited to your Venmo account.",
     BULK, True),
    ("[mattumali579/job-radar] Run failed: Watchdog - main (bfe5b35)",
     "notifications@github.com",
     "Watchdog workflow run Watchdog: All jobs have failed View workflow run",
     GH_BULK, True),
    ("Firecrawl concurrent browser limit reached", "help@firecrawl.dev",
     "You've reached your browser concurrency limit on Firecrawl. New requests are being queued.",
     BULK, True),
    ("You are near your Public API limit", "noreply@airtable.com",
     "Your Workspace workspace has utilized 80% of the API call limit under the Free plan.",
     BULK, True),

    # --- must stay quiet ---------------------------------------------------
    ("559294 - Your Spotify login code", "no-reply@alerts.spotify.com",
     "Enter this code to continue logging in without a password: 559294", {}, False),
    ("Your receipt from Anthropic, PBC #2774-8502-9110",
     "invoice+statements@mail.anthropic.com", "Your receipt from Anthropic, PBC", BULK, False),
    ("Welcome to ChatGPT Plus", "noreply@email.openai.com",
     "You've unlocked premium access to advanced intelligence", BULK, False),
    ("Welcome to Turing!", "no-reply@turing.com",
     "Thank you for signing up to work with Turing. Complete your profile.", BULK, False),
    ("Action Recommended for mattumali579 : [74c3cb77]",
     "noreply@mail.announcements.us-shawnee-1.oci.oraclecloud.com",
     "Your Oracle Cloud Account is Fully Provisioned. You can now sign into your cloud account.",
     BULK, False),
    ("Verify your email to create your Oracle Cloud account",
     "noreply@verify.signup.us-ashburn-1.oci.oraclecloud.com",
     "Thanks for your interest in creating an Oracle Cloud account. Please verify your email address.",
     {}, False),
    ("You have been invited to Aug 30, 2026 on August 30, 2026 at 10:00 PM EDT",
     "no-reply@emails.whop.com", "You've been invited to Aug 30, 2026!", BULK, False),
    ("MIT unveils plane that needs no runway", "superhuman@mail.joinsuperhuman.ai",
     "August 30, 2026 | Read online Welcome back, Superhuman.", BULK, False),
    ("The Best Books of September", "barnesandnoble@e.barnesandnoble.com",
     "Fiction, nonfiction, YA, kids' books, paperbacks & more.", BULK, False),
    ("wait... these deals??", "hello@info.myunidays.com",
     "they understood the assignment", BULK, False),
    ("what to do if you procrastinate", "maximus@nextwork.org",
     "People with accountability partners are 95% more likely to achieve their goals.",
     BULK, False),
    ("Your AI tools are waiting", "team@mail.airtable.com",
     "Looks like you haven't tried Omni and Field Agents yet. Your Airtable plan includes AI credits.",
     BULK, False),
    ("Monitor changes detected: AI Gig Radar - Platform Signups & Roles",
     "notifications@notifications.firecrawl.dev",
     "Changed: 3 (1 meaningful, 2 noise) New: 0 Removed: 0 Errors: 0", BULK, False),

    # --- the system's own digests must never notify about themselves -------
    ("LeadGen follow-ups - 9 new drafts waiting", "matt.umali579@gmail.com",
     "Drafted 9 follow-up bumps tonight, sitting in Gmail Drafts", {}, False),
    ("Money digest - 56 drafts waiting, 0 sent today", "matt.umali579@gmail.com",
     "Airtable (203 total rows): No Email Found: 143, Drafted: 56", {}, False),
]


def test_real_inbox_sorts_correctly():
    wrong = []
    for subject, sender, snippet, headers, expected in REAL_INBOX:
        result = classify(subject, snippet, sender, headers, OWNER)
        if result.important != expected:
            wrong.append(
                f"{subject!r}: expected notify={expected}, got {result.important} "
                f"(score={result.score}, cats={result.categories}, "
                f"skip={result.skip_reason})"
            )
    assert not wrong, "misclassified real messages:\n" + "\n".join(wrong)


def test_no_false_positive_rate_on_real_noise():
    """The 16 real non-urgent messages must produce zero notifications."""
    noise = [row for row in REAL_INBOX if row[4] is False]
    fired = [
        row[0] for row in noise
        if classify(row[0], row[2], row[1], row[3], OWNER).important
    ]
    assert fired == [], f"these would have spammed the phone: {fired}"


def test_owner_self_mail_is_always_dropped():
    result = classify("anything at all", "urgent exam due today", "matt.umali579@gmail.com",
                      {}, OWNER)
    assert result.important is False
    assert result.skip_reason == "from_self"


def test_school_assignment_from_edu_notifies():
    result = classify(
        "MATH 1550: Homework 5 due Friday 11:59pm",
        "Homework 5 covering related rates is due Friday at 11:59pm on Moodle.",
        "professor@lsu.edu", {}, OWNER,
    )
    assert result.important is True
    assert "school" in result.categories


def test_bursar_hold_notifies():
    result = classify(
        "Registration hold on your account",
        "A financial aid hold prevents registration for the spring semester.",
        "bursar@lsu.edu", {}, OWNER,
    )
    assert result.important is True


def test_interview_invite_notifies():
    result = classify(
        "Next steps for your application",
        "We would like to speak with you. Can you schedule a call this week?",
        "recruiter@company.example", {}, OWNER,
    )
    assert result.important is True
    assert "job" in result.categories


def test_security_alert_beats_bulk_headers():
    result = classify(
        "Security alert: new sign-in from a new device",
        "Someone signed in to your account from a device we do not recognize.",
        "no-reply@accounts.google.com", BULK, OWNER,
    )
    assert result.important is True
    assert "security" in result.categories


def test_security_alert_wins_over_otp_shortcut():
    """A code-shaped subject that is really a breach alert must still fire."""
    result = classify(
        "Your verification code was requested - unusual activity",
        "We detected unusual sign-in activity on your account.",
        "no-reply@bank.example", {}, OWNER,
    )
    assert result.important is True


def test_urgent_wording_raises_urgency():
    result = classify(
        "URGENT: tuition payment past due",
        "Your tuition payment is past due and must be resolved immediately.",
        "bursar@lsu.edu", {}, OWNER,
    )
    assert result.important is True
    assert result.urgency == "high"


def test_empty_message_is_safe():
    result = classify("", "", "", {}, OWNER)
    assert result.important is False
    assert result.score == 0


class TestPaymentFailureWording:
    """Money mail is the most expensive category to miss, and real senders
    never use the adjacent phrasing the first version of the rule required."""

    @pytest.mark.parametrize(
        "subject, body",
        [
            ("Your payment of $842.00 failed",
             "We could not process your rent payment. Act by Sept 1."),
            ("Payment for invoice 41 was declined", "Please update your card."),
            ("We were unable to process your payment",
             "Your card on file was rejected."),
            ("Your payment was unsuccessful", "Please try another method."),
            ("Payment declined", "Card ending 4242 was declined."),
        ],
    )
    def test_real_payment_failures_alert(self, subject, body):
        result = classify(subject, body, "billing@vendor.com", {},
                          owner_addresses=())
        assert result.important, f"missed a payment failure: {subject!r}"
        assert result.label == "money"

    @pytest.mark.parametrize(
        "subject, body, headers",
        [
            # "payment ... due" must stay adjacent or this becomes a false alarm.
            ("Your payment options are due for review",
             "Update your saved cards at your convenience.",
             {"list-unsubscribe": "<https://x.com/u>"}),
            ("Manage your payment methods",
             "You can add or remove a card anytime.", {}),
        ],
    )
    def test_payment_admin_chatter_stays_quiet(self, subject, body, headers):
        result = classify(subject, body, "billing@saas.com", headers,
                          owner_addresses=())
        assert not result.important, f"false alarm on: {subject!r}"
