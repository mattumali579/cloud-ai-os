"""Secret scanner tests (Agent 10). All example secrets are OBVIOUSLY FAKE.

Assertions are written so failure output shows pattern names / redacted
samples only — never a full secret value in an assertion message.
"""
import pytest

from cloudos.privacy import scan_secrets
from cloudos.privacy.secrets import shannon_entropy


def _names(text: str) -> list[str]:
    return [h.pattern_name for h in scan_secrets(text)]


# --- every pattern: positive ------------------------------------------------

POSITIVES = [
    ("aws_access_key", "key id AKIAIOSFODNN7EXAMPLE in config"),
    ("google_api_key", "maps key " + "AIza" + "Fake1" * 7 + " here"),
    ("slack_token", "bot uses xoxb-1234567890-NOTREALNOTREAL today"),
    ("github_token", "pat ghp_" + "Ab1Cd2Ef3Gh4" * 3 + " leaked"),
    ("stripe_key", "charge with sk_live_FAKEfake1234FAKEfake now"),
    ("stripe_key", "restricted rk_live_FAKEfake1234FAKEfake key"),
    (
        "private_key_block",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIFAKEFAKEFAKE\n-----END RSA PRIVATE KEY-----",
    ),
    ("private_key_block", "-----BEGIN OPENSSH PRIVATE KEY-----"),
    ("jwt", "token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJmYWtlIn0.FAKEsig12345678 found"),
    ("connection_string", "postgresql://fakeuser:fakepass123@db.example.com:5432/app"),
    ("connection_string", "mongodb+srv://fakeuser:fakepass@cluster0.example.net/db"),
    ("connection_string", "redis://default:fakepass1234@cache.example.com:6379"),
    ("authorization_header", "Authorization: Bearer FAKEFAKEFAKEFAKE1234"),
    ("authorization_header", "authorization: basic RkFLRUZBS0VGQUtFRkFLRQ=="),
    ("env_assignment", 'API_KEY="Zq83vN1pXk7Rt2Lm9Jd4"'),
    ("env_assignment", "DB_PASSWORD: xK92mQ4vL8zR3tW6nB1c"),
    ("env_assignment", "my-client-secret=Wf7pQ2xN9kEj4Rt8Vz1m"),
]


@pytest.mark.parametrize("expected_name,text", POSITIVES)
def test_pattern_positive(expected_name, text):
    assert expected_name in _names(text), f"pattern {expected_name} did not fire"


# --- tricky negatives -------------------------------------------------------

NEGATIVES = [
    "The token bucket algorithm hands tokens to each request.",
    "PASSWORD=test",  # too short
    "SECRET=aaaaaaaaaaaaaaaaaaaa",  # long but low-entropy
    "This api key rotation policy explains secret management basics.",
    "postgresql://localhost:5432/app",  # no credentials in URL
    "Authorization: Bearer short",  # value too short
    "Use xoxb tokens for Slack bots.",  # prefix word without token body
    "My password: hunter2",  # short value
    "The word token: appears in this grammar spec.",  # value too short
    "AKIA is the AWS access-key prefix.",  # prefix without key body
]


@pytest.mark.parametrize("text", NEGATIVES)
def test_negative_no_hits(text):
    assert _names(text) == []


def test_empty_and_none_safe():
    assert scan_secrets("") == []


# --- redaction invariants ---------------------------------------------------

def test_redaction_never_leaks_full_secret():
    text = "AKIAIOSFODNN7EXAMPLE"
    hits = scan_secrets(text)
    assert len(hits) == 1
    hit = hits[0]
    assert hit.pattern_name == "aws_access_key"
    assert hit.redacted_sample.endswith("…")
    assert len(hit.redacted_sample) <= 5  # 4 chars + ellipsis, max
    assert hit.redacted_sample != text
    assert text not in hit.redacted_sample


def test_redaction_short_for_every_pattern():
    for _, text in POSITIVES:
        for hit in scan_secrets(text):
            assert len(hit.redacted_sample) <= 5
            assert hit.redacted_sample.endswith("…")


# --- spans and ordering -----------------------------------------------------

def test_span_points_at_match():
    prefix = "config value "
    text = prefix + "AKIAIOSFODNN7EXAMPLE"
    (hit,) = scan_secrets(text)
    assert hit.span == (len(prefix), len(text))


def test_multiple_hits_sorted_by_position():
    text = (
        "first AKIAIOSFODNN7EXAMPLE then "
        "postgresql://fakeuser:fakepass123@db.example.com/app"
    )
    hits = scan_secrets(text)
    assert [h.pattern_name for h in hits] == ["aws_access_key", "connection_string"]
    assert hits[0].span[0] < hits[1].span[0]


# --- entropy helper ---------------------------------------------------------

def test_entropy_orders_random_above_repeats():
    assert shannon_entropy("") == 0.0
    assert shannon_entropy("aaaaaaaaaaaaaaaa") == 0.0
    assert shannon_entropy("Zq83vN1pXk7Rt2Lm9Jd4") > 3.5
    assert shannon_entropy("hunter2hunter2") < 3.5
