"""classify() precedence and path-rule tests (Agent 10). No network, no DB."""
import textwrap

import pytest

import cloudos.config as config
from cloudos.contracts import PrivacyLabel
from cloudos.privacy import classify
from cloudos.privacy.rules import load_privacy_config, reset_privacy_config_cache


@pytest.fixture(autouse=True)
def _fresh_caches(monkeypatch):
    """Isolate every test from env + cached settings/config."""
    monkeypatch.delenv("PRIVACY_CONFIG_PATH", raising=False)
    config.reset_settings_cache()
    reset_privacy_config_cache()
    yield
    config.reset_settings_cache()
    reset_privacy_config_cache()


# --- precedence: frontmatter beats path rules beats default -----------------

def test_frontmatter_beats_blocked_path():
    # secrets/** is a blocked glob, but explicit frontmatter wins.
    assert classify("secrets/how-to-rotate.md", {"privacy": "public"}) == PrivacyLabel.PUBLIC


def test_frontmatter_value_case_insensitive():
    assert classify(None, {"privacy": "Public"}) == PrivacyLabel.PUBLIC
    assert classify(None, {"privacy": "SECRET"}) == PrivacyLabel.SECRET
    assert classify(None, {"privacy": " Personal "}) == PrivacyLabel.PERSONAL


def test_invalid_frontmatter_value_fails_safe_to_sensitive():
    assert classify("architecture/x.md", {"privacy": "wide-open"}) == PrivacyLabel.SENSITIVE
    assert classify(None, {"privacy": True}) == PrivacyLabel.SENSITIVE
    assert classify(None, {"privacy": 42}) == PrivacyLabel.SENSITIVE


def test_empty_frontmatter_value_falls_through_to_path_rules():
    # `privacy:` with no value parses to None → not an explicit label.
    assert classify("architecture/x.md", {"privacy": None}) == PrivacyLabel.PUBLIC


def test_path_rules_beat_default():
    assert classify("personal/journal.md", None) == PrivacyLabel.SENSITIVE
    assert classify("architecture/overview.md", None) == PrivacyLabel.PUBLIC


def test_default_when_nothing_matches():
    assert classify("random/notes.md", None) == PrivacyLabel.INTERNAL
    assert classify(None, None) == PrivacyLabel.INTERNAL


def test_default_argument_overrides_config_default():
    assert classify(None, None, default=PrivacyLabel.PERSONAL) == PrivacyLabel.PERSONAL
    # ...but never overrides a matching rule.
    assert (
        classify("personal/journal.md", None, default=PrivacyLabel.PUBLIC)
        == PrivacyLabel.SENSITIVE
    )


# --- path matching (repo config/privacy.yaml) -------------------------------

@pytest.mark.parametrize(
    "path",
    [
        "personal/journal.md",
        "secrets/api-notes.md",
        "02 Journals/2026-08-28.md",
        ".env",  # **/.env* at repo root
        "app/.env.local",  # **/.env* nested
        "certs/server.key",  # **/*.key nested
        "server.key",  # **/*.key at root
        "personal\\windows\\style.md",  # backslashes normalized
        "./personal/dotted.md",  # leading ./ normalized
        "Personal/CaseDiffers.md",  # case-insensitive (fail-safe)
    ],
)
def test_blocked_paths_are_sensitive(path):
    assert classify(path, None) == PrivacyLabel.SENSITIVE


@pytest.mark.parametrize(
    "path",
    [
        "architecture/overview.md",
        "knowledge/public/deep/nested/note.md",
        "agents/public/spec.md",
    ],
)
def test_allowed_paths_are_public(path):
    assert classify(path, None) == PrivacyLabel.PUBLIC


def test_similar_but_unmatched_path_stays_internal():
    # "personalize" is not "personal/" — * and ** must respect segments.
    assert classify("personalize/notes.md", None) == PrivacyLabel.INTERNAL


# --- rules loaded from a tmp privacy.yaml via monkeypatched settings --------

def test_rules_from_tmp_config(tmp_path, monkeypatch):
    cfg_file = tmp_path / "privacy.yaml"
    cfg_file.write_text(
        textwrap.dedent(
            """\
            default_label: personal
            frontmatter_key: privacy
            internal_external_ok_key: external_ok
            external_ai:
              allowed: ["open/**"]
              blocked: ["open/keys/**", "vault/**"]
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PRIVACY_CONFIG_PATH", str(cfg_file))
    config.reset_settings_cache()
    reset_privacy_config_cache()

    assert load_privacy_config().source_path == str(cfg_file)
    assert classify("open/readme.md", None) == PrivacyLabel.PUBLIC
    # blocked beats allowed when both match
    assert classify("open/keys/k.md", None) == PrivacyLabel.SENSITIVE
    assert classify("vault/x.md", None) == PrivacyLabel.SENSITIVE
    # custom default_label honored
    assert classify("elsewhere/x.md", None) == PrivacyLabel.PERSONAL


def test_missing_config_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("PRIVACY_CONFIG_PATH", str(tmp_path / "does-not-exist.yaml"))
    config.reset_settings_cache()
    reset_privacy_config_cache()
    # No rules loadable → everything defaults to SENSITIVE (fail closed).
    assert classify("anything.md", None) == PrivacyLabel.SENSITIVE
    assert classify(None, None) == PrivacyLabel.SENSITIVE
