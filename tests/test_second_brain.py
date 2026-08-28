"""Tests for cloudos.second_brain (Agent 9).

No live DB, no network, and the real Second Brain directory is never touched:
everything runs against a tmp_path fixture brain. cloudos.privacy (Agent 10)
and cloudos.db (Agent 4) are written concurrently, so both are stubbed via
sys.modules — never imported for real.
"""
from __future__ import annotations

import sys
import types

import pytest

from cloudos.config import reset_settings_cache
from cloudos.contracts import (
    CloudOSError,
    ErrorCode,
    GateDecision,
    PrivacyLabel,
    SecretHit,
    sha256_hash,
)
from cloudos.second_brain import get_context, parse_frontmatter, read_note, reindex

FAKE_KEY = "sk_live_FAKE1234567890abc"  # fake fixture secret — never a real credential


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

ARCHITECTURE_MD = """---
privacy: public
tags:
  - cloudos
---
# Architecture Overview

The cloudos architecture: worker, oracle vm, supabase metadata index.
"""

JOURNAL_MD = """# Daily Journal

Worked on the cloudos architecture today. Personal feelings included.
"""

ROADMAP_MD = """---
privacy: sensitive
---
# Quarterly Roadmap

Sensitive cloudos architecture plans for next quarter.
"""

API_KEYS_MD = f"""Deployment notes for the cloudos architecture.

STRIPE_KEY = "{FAKE_KEY}"
"""


@pytest.fixture
def brain(tmp_path, monkeypatch):
    """Temporary Second Brain with public/personal/sensitive/secret-bearing notes."""
    root = tmp_path / "brain"
    (root / "personal").mkdir(parents=True)
    (root / "projects").mkdir()
    (root / "architecture.md").write_text(ARCHITECTURE_MD, encoding="utf-8")
    (root / "personal" / "journal.md").write_text(JOURNAL_MD, encoding="utf-8")
    (root / "projects" / "roadmap.md").write_text(ROADMAP_MD, encoding="utf-8")
    (root / "projects" / "api-keys.md").write_text(API_KEYS_MD, encoding="utf-8")
    # Decoys that must be pruned/ignored by every walk.
    (root / ".obsidian").mkdir()
    (root / ".obsidian" / "workspace.md").write_text("# not a note", encoding="utf-8")
    (root / ".git").mkdir()
    (root / ".git" / "objects.md").write_text("# not a note", encoding="utf-8")
    (root / "node_modules" / "pkg").mkdir(parents=True)
    (root / "node_modules" / "pkg" / "README.md").write_text("# dep", encoding="utf-8")
    (root / "notes.txt").write_text("not markdown", encoding="utf-8")

    monkeypatch.setenv("SECOND_BRAIN_PATH", str(root))
    reset_settings_cache()
    yield root
    reset_settings_cache()


@pytest.fixture
def privacy_stub(monkeypatch):
    """Deterministic stand-in for Agent 10's cloudos.privacy (never the real one)."""
    module = types.ModuleType("cloudos.privacy")

    def classify(path, frontmatter, default=PrivacyLabel.INTERNAL):
        fm = frontmatter or {}
        if "privacy" in fm:
            return PrivacyLabel(str(fm["privacy"]).lower())
        if str(path).startswith("personal/"):
            return PrivacyLabel.PERSONAL
        return default

    def gate(content, label):
        if "sk_live_" in content:
            return GateDecision(
                False, "secret detected", hits=[SecretHit("stripe_key", (0, 7), "sk_l…")]
            )
        if "alpha-half" in content and "beta-half" in content:
            return GateDecision(False, "combined halves detected")
        if label in (PrivacyLabel.PUBLIC, PrivacyLabel.INTERNAL):
            return GateDecision(True, "ok")
        return GateDecision(False, f"label {label.value} blocked")

    module.classify = classify
    module.gate = gate
    monkeypatch.setitem(sys.modules, "cloudos.privacy", module)
    return module


@pytest.fixture
def no_privacy(monkeypatch):
    """Make cloudos.privacy unimportable (None sys.modules entry => ImportError)."""
    monkeypatch.setitem(sys.modules, "cloudos.privacy", None)


@pytest.fixture
def no_db(monkeypatch):
    """Make cloudos.db unimportable so lazy DB acquisition yields no connection."""
    monkeypatch.setitem(sys.modules, "cloudos.db", None)


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)


class FakeConn:
    """Records sb_index upserts; serves SELECTs from the recorded rows."""

    def __init__(self):
        self.index: dict[str, dict] = {}
        self.executed: list[tuple[str, tuple | None]] = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if sql.lstrip().upper().startswith("SELECT"):
            return FakeResult(list(self.index.values()))
        path, title, label, content_hash, mtime, indexed_at = params
        self.index[path] = {
            "path": path,
            "title": title,
            "privacy_label": label,
            "content_hash": content_hash,
            "modified_at": mtime,
            "indexed_at": indexed_at,
        }
        return FakeResult([])


# ---------------------------------------------------------------------------
# read_note + frontmatter
# ---------------------------------------------------------------------------

def test_read_note_parses_frontmatter(brain):
    frontmatter, body = read_note("architecture.md")
    assert frontmatter == {"privacy": "public", "tags": ["cloudos"]}
    assert body.startswith("# Architecture Overview")
    assert "---" not in body


def test_read_note_without_frontmatter(brain):
    frontmatter, body = read_note("personal/journal.md")
    assert frontmatter == {}
    assert body == JOURNAL_MD


def test_read_note_accepts_absolute_path_inside_root(brain):
    frontmatter, _body = read_note(str(brain / "architecture.md"))
    assert frontmatter["privacy"] == "public"


def test_malformed_frontmatter_yields_empty_dict_and_keeps_content(brain):
    (brain / "bad.md").write_text("---\nkey: [unclosed\n---\nBody text\n", encoding="utf-8")
    frontmatter, body = read_note("bad.md")
    assert frontmatter == {}
    assert "key: [unclosed" in body  # nothing dropped on parse failure
    assert "Body text" in body


def test_non_mapping_frontmatter_yields_empty_dict(brain):
    (brain / "listy.md").write_text("---\n- a\n- b\n---\nBody\n", encoding="utf-8")
    frontmatter, body = read_note("listy.md")
    assert frontmatter == {}
    assert "Body" in body


def test_unterminated_frontmatter_treated_as_body():
    frontmatter, body = parse_frontmatter("---\nprivacy: public\nno closing fence")
    assert frontmatter == {}
    assert body == "---\nprivacy: public\nno closing fence"


def test_empty_frontmatter_block():
    frontmatter, body = parse_frontmatter("---\n---\nHello\n")
    assert frontmatter == {}
    assert body == "Hello\n"


@pytest.mark.parametrize(
    "escape",
    ["../outside.md", "..\\outside.md", "personal/../../outside.md"],
)
def test_read_note_refuses_traversal(brain, tmp_path, escape):
    (tmp_path / "outside.md").write_text("# outside", encoding="utf-8")
    with pytest.raises(CloudOSError) as excinfo:
        read_note(escape)
    assert excinfo.value.code == ErrorCode.VALIDATION_ERROR


def test_read_note_refuses_absolute_path_outside_root(brain, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("# outside", encoding="utf-8")
    with pytest.raises(CloudOSError) as excinfo:
        read_note(str(outside))
    assert excinfo.value.code == ErrorCode.VALIDATION_ERROR


def test_read_note_missing_file_is_validation_error(brain):
    with pytest.raises(CloudOSError) as excinfo:
        read_note("does-not-exist.md")
    assert excinfo.value.code == ErrorCode.VALIDATION_ERROR


# ---------------------------------------------------------------------------
# Config guard (documented choice: get_context raises too)
# ---------------------------------------------------------------------------

def test_all_functions_raise_when_path_unset(monkeypatch):
    monkeypatch.setenv("SECOND_BRAIN_PATH", "")
    reset_settings_cache()
    try:
        for call in (
            lambda: read_note("x.md"),
            lambda: reindex(),
            lambda: get_context("anything"),
        ):
            with pytest.raises(CloudOSError) as excinfo:
                call()
            assert excinfo.value.code == ErrorCode.DEPENDENCY_UNAVAILABLE
    finally:
        reset_settings_cache()


def test_all_functions_raise_when_dir_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("SECOND_BRAIN_PATH", str(tmp_path / "nope"))
    reset_settings_cache()
    try:
        for call in (
            lambda: read_note("x.md"),
            lambda: reindex(),
            lambda: get_context("anything"),
        ):
            with pytest.raises(CloudOSError) as excinfo:
                call()
            assert excinfo.value.code == ErrorCode.DEPENDENCY_UNAVAILABLE
    finally:
        reset_settings_cache()


# ---------------------------------------------------------------------------
# reindex
# ---------------------------------------------------------------------------

def test_reindex_counts_labels_and_metadata_only(brain, privacy_stub):
    conn = FakeConn()
    result = reindex(conn=conn)
    assert result == {"scanned": 4, "indexed": 4, "skipped": 0}  # decoys pruned

    assert set(conn.index) == {
        "architecture.md",
        "personal/journal.md",
        "projects/roadmap.md",
        "projects/api-keys.md",
    }
    assert conn.index["architecture.md"]["privacy_label"] == "public"
    assert conn.index["personal/journal.md"]["privacy_label"] == "personal"
    assert conn.index["projects/roadmap.md"]["privacy_label"] == "sensitive"
    assert conn.index["projects/api-keys.md"]["privacy_label"] == "internal"

    assert conn.index["architecture.md"]["title"] == "Architecture Overview"
    assert conn.index["projects/api-keys.md"]["title"] == "api-keys"  # no H1 -> stem

    assert conn.index["architecture.md"]["content_hash"] == sha256_hash(ARCHITECTURE_MD)

    # sb_index holds METADATA ONLY — no body text, no secrets, ever.
    dumped = repr(conn.index) + repr(conn.executed)
    assert "sk_live_" not in dumped
    assert "Worked on the cloudos" not in dumped


def test_reindex_skips_unchanged_and_reindexes_modified(brain, privacy_stub):
    conn = FakeConn()
    assert reindex(conn=conn) == {"scanned": 4, "indexed": 4, "skipped": 0}
    assert reindex(conn=conn) == {"scanned": 4, "indexed": 0, "skipped": 4}

    (brain / "architecture.md").write_text(
        ARCHITECTURE_MD + "\nAn edited line.\n", encoding="utf-8"
    )
    assert reindex(conn=conn) == {"scanned": 4, "indexed": 1, "skipped": 3}
    assert conn.index["architecture.md"]["content_hash"] == sha256_hash(
        ARCHITECTURE_MD + "\nAn edited line.\n"
    )


def test_reindex_without_db_returns_counts_only(brain, privacy_stub, no_db):
    assert reindex() == {"scanned": 4, "indexed": 4, "skipped": 0}


def test_reindex_fails_closed_to_sensitive_without_privacy_module(brain, no_privacy):
    conn = FakeConn()
    reindex(conn=conn)
    assert {row["privacy_label"] for row in conn.index.values()} == {"sensitive"}


# ---------------------------------------------------------------------------
# get_context
# ---------------------------------------------------------------------------

def test_get_context_ranking_filesystem_fallback(brain, privacy_stub, no_db):
    bundle = get_context("architecture", max_files=3)
    # Title+path match outranks body-only matches; body-only ties break by path.
    assert bundle.files == [
        "architecture.md",
        "personal/journal.md",
        "projects/api-keys.md",
    ]
    assert "--- architecture.md ---" in bundle.text
    assert bundle.excluded == []
    assert bundle.truncated is False


def test_get_context_uses_db_index_candidates(brain, privacy_stub):
    conn = FakeConn()
    reindex(conn=conn)
    bundle = get_context("journal", conn=conn)
    assert bundle.files == ["personal/journal.md"]
    assert any(sql.lstrip().upper().startswith("SELECT") for sql, _ in conn.executed)


def test_get_context_no_match_returns_empty_bundle(brain, privacy_stub, no_db):
    bundle = get_context("zzzunfindable")
    assert bundle.files == [] and bundle.text == ""
    assert bundle.truncated is False and bundle.excluded == []


def test_get_context_empty_query_returns_empty_bundle(brain, privacy_stub, no_db):
    bundle = get_context("   ")
    assert bundle.files == [] and bundle.text == ""


def test_get_context_for_external_excludes_blocked_files(brain, privacy_stub, no_db):
    bundle = get_context("architecture", max_files=5, for_external=True)
    assert bundle.files == ["architecture.md"]  # only the public, secret-free note
    assert bundle.excluded == [
        "personal/journal.md",  # PERSONAL label blocked
        "projects/api-keys.md",  # fake API key tripped the secret scan
        "projects/roadmap.md",  # explicit privacy: sensitive blocked
    ]
    assert "sk_live_" not in bundle.text
    assert "Architecture Overview" in bundle.text


def test_get_context_internal_does_not_exclude(brain, privacy_stub, no_db):
    bundle = get_context("architecture", max_files=5, for_external=False)
    assert len(bundle.files) == 4  # internal use: no gating, nothing excluded
    assert bundle.excluded == []


def test_get_context_final_gate_belt_and_braces(brain, privacy_stub, no_db):
    # Each note individually passes the per-file gate; only the ASSEMBLED text
    # trips the gate — the final belt-and-braces scan must catch it and raise.
    (brain / "note_a.md").write_text(
        "---\nprivacy: public\n---\n# Combo Alpha\n\ncombo alpha-half\n", encoding="utf-8"
    )
    (brain / "note_b.md").write_text(
        "---\nprivacy: public\n---\n# Combo Beta\n\ncombo beta-half\n", encoding="utf-8"
    )
    with pytest.raises(CloudOSError) as excinfo:
        get_context("combo", for_external=True)
    assert excinfo.value.code == ErrorCode.PRIVACY_BLOCKED
    assert "sk_live_" not in str(excinfo.value)  # safe metadata only


def test_get_context_for_external_requires_gate_module(brain, no_privacy, no_db):
    with pytest.raises(CloudOSError) as excinfo:
        get_context("architecture", for_external=True)
    assert excinfo.value.code == ErrorCode.DEPENDENCY_UNAVAILABLE


def test_get_context_truncates_to_max_chars(brain, privacy_stub, no_db):
    bundle = get_context("architecture", max_files=5, max_chars=40)
    assert bundle.truncated is True
    assert len(bundle.text) <= 40
