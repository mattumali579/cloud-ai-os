"""Deterministic secret scanner (ARCHITECTURE.md §10).

Pure regex + a Shannon-entropy check for generic assignments. No AI, no
network, no state. The full matched secret NEVER leaves this module: every
hit carries only the pattern name, the span, and a redacted sample of at
most the first 4 characters plus an ellipsis.
"""
from __future__ import annotations

import math
import re

from cloudos.contracts import SecretHit

__all__ = ["scan_secrets", "shannon_entropy", "PATTERN_NAMES"]

# Entropy threshold (bits/char) for the generic env_assignment pattern.
# Real keys/tokens are high-entropy; words and repeated chars are not.
_ENTROPY_THRESHOLD = 3.5
_MIN_VALUE_LEN = 12

# Named, deterministic patterns. Order is stable → deterministic output.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[0-9A-Za-z\-]{10,}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("stripe_key", re.compile(r"\b[sr]k_live_[0-9A-Za-z]{16,}\b")),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")),
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_\-]+\.eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]{8,}\b"),
    ),
    (
        "connection_string",
        # scheme://user:password@host — a URL WITHOUT credentials does not match.
        re.compile(
            r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|amqp|redis)://"
            r"[^\s:@/]+:[^\s@]+@\S+",
            re.IGNORECASE,
        ),
    ),
    (
        "authorization_header",
        re.compile(r"\bauthorization[ \t]*:[ \t]*(?:bearer|basic)[ \t]+\S{16,}", re.IGNORECASE),
    ),
)

# Generic KEY=value / key: value assignment. Candidate only — a hit also
# requires the value to be long AND high-entropy (cuts prose false positives
# like `PASSWORD=test` or "the token bucket algorithm").
_ENV_ASSIGNMENT = re.compile(
    r"[\w\-]*(?:api[_-]?key|secret|token|passw(?:or)?d)[\w\-]*"
    r"[ \t]*[=:][ \t]*['\"]?(?P<value>\S{12,})",
    re.IGNORECASE,
)

PATTERN_NAMES: tuple[str, ...] = tuple(name for name, _ in _PATTERNS) + ("env_assignment",)


def shannon_entropy(value: str) -> float:
    """Shannon entropy in bits per character. Empty string → 0.0."""
    if not value:
        return 0.0
    counts: dict[str, int] = {}
    for ch in value:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(value)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def _hit(name: str, start: int, end: int, matched: str) -> SecretHit:
    # Redaction invariant: at most the first 4 chars + "…". Never the full match.
    return SecretHit(pattern_name=name, span=(start, end), redacted_sample=matched[:4] + "…")


def scan_secrets(text: str) -> list[SecretHit]:
    """Scan `text` for secret shapes. Deterministic; returns safe metadata only."""
    if not text:
        return []
    hits: list[SecretHit] = []
    for name, rx in _PATTERNS:
        for m in rx.finditer(text):
            hits.append(_hit(name, m.start(), m.end(), m.group(0)))
    for m in _ENV_ASSIGNMENT.finditer(text):
        value = m.group("value").rstrip("'\",;)]}`")
        if len(value) >= _MIN_VALUE_LEN and shannon_entropy(value) > _ENTROPY_THRESHOLD:
            hits.append(_hit("env_assignment", m.start(), m.end(), m.group(0)))
    hits.sort(key=lambda h: (h.span[0], h.span[1], h.pattern_name))
    return hits
