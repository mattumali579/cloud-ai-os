"""gate() matrix tests (Agent 10). Fake secrets only; no network, no DB."""
import pytest

import cloudos.config as config
from cloudos.contracts import GateDecision, PrivacyLabel
from cloudos.privacy import gate
from cloudos.privacy.rules import reset_privacy_config_cache

FAKE_SECRET_TEXT = "here is a key AKIAIOSFODNN7EXAMPLE embedded in prose"
CLEAN_TEXT = "A perfectly ordinary paragraph about system architecture."


@pytest.fixture(autouse=True)
def _fresh_caches(monkeypatch):
    monkeypatch.delenv("PRIVACY_CONFIG_PATH", raising=False)
    config.reset_settings_cache()
    reset_privacy_config_cache()
    yield
    config.reset_settings_cache()
    reset_privacy_config_cache()


# --- label routing matrix (§9-L2) -------------------------------------------

def test_public_clean_allowed():
    d = gate(CLEAN_TEXT, PrivacyLabel.PUBLIC)
    assert isinstance(d, GateDecision)
    assert d.allowed is True
    assert d.reason == "allowed"
    assert d.hits == []


def test_internal_blocked_without_external_ok():
    d = gate(CLEAN_TEXT, PrivacyLabel.INTERNAL)
    assert d.allowed is False
    assert d.reason == "internal_requires_external_ok"


def test_internal_allowed_with_external_ok():
    d = gate(CLEAN_TEXT, PrivacyLabel.INTERNAL, frontmatter={"external_ok": True})
    assert d.allowed is True
    assert d.reason == "allowed"


@pytest.mark.parametrize("value", [False, "false", "no", 0, None, ""])
def test_internal_blocked_with_non_truthy_external_ok(value):
    d = gate(CLEAN_TEXT, PrivacyLabel.INTERNAL, frontmatter={"external_ok": value})
    assert d.allowed is False


@pytest.mark.parametrize(
    "label",
    [PrivacyLabel.PERSONAL, PrivacyLabel.SENSITIVE, PrivacyLabel.SECRET],
)
def test_hard_blocked_labels_never_pass(label):
    # Even an explicit external_ok cannot override PERSONAL/SENSITIVE/SECRET.
    d = gate(CLEAN_TEXT, label, frontmatter={"external_ok": True})
    assert d.allowed is False
    assert d.reason == f"label_blocked:{label.value}"
    assert d.hits == []


def test_unknown_label_blocked():
    d = gate(CLEAN_TEXT, "mystery")  # type: ignore[arg-type]
    assert d.allowed is False
    assert d.reason == "unknown_label"


# --- secret scan overrides an otherwise-allowed label -----------------------

def test_public_with_secret_blocked():
    d = gate(FAKE_SECRET_TEXT, PrivacyLabel.PUBLIC)
    assert d.allowed is False
    assert d.reason == "secret_detected"
    assert len(d.hits) == 1
    assert d.hits[0].pattern_name == "aws_access_key"
    # decision carries safe metadata only
    assert len(d.hits[0].redacted_sample) <= 5


def test_internal_external_ok_with_secret_blocked():
    d = gate(FAKE_SECRET_TEXT, PrivacyLabel.INTERNAL, frontmatter={"external_ok": True})
    assert d.allowed is False
    assert d.reason == "secret_detected"
    assert d.hits


def test_empty_content_public_allowed():
    d = gate("", PrivacyLabel.PUBLIC)
    assert d.allowed is True
