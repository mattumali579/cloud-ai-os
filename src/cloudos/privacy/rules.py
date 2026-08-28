"""Deterministic privacy classification and the external-AI gate.

ARCHITECTURE.md §10 / §9-L2. Everything here is rule-based — NEVER AI-decided.
The fail-safe direction is always "more private": unknown or invalid inputs
classify as SENSITIVE, and the gate blocks.

Config comes from `privacy.yaml` (settings.privacy_config_path). Contract keys
are FIXED: default_label, frontmatter_key, internal_external_ok_key,
external_ai.allowed, external_ai.blocked.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml

from cloudos.config import get_settings
from cloudos.contracts import GateDecision, PrivacyLabel
from cloudos.privacy.secrets import scan_secrets

__all__ = [
    "classify",
    "gate",
    "load_privacy_config",
    "reset_privacy_config_cache",
    "PrivacyConfig",
]

_HARD_BLOCKED = frozenset(
    {PrivacyLabel.PERSONAL, PrivacyLabel.SENSITIVE, PrivacyLabel.SECRET}
)

# Strict truthiness for external_ok: a string "false"/"no" must NOT count as
# consent to leave the machine. Anything unrecognised counts as False.
_TRUTHY_STRINGS = frozenset({"true", "yes", "1", "on", "y"})


@dataclass(frozen=True)
class PrivacyConfig:
    default_label: PrivacyLabel
    frontmatter_key: str
    internal_external_ok_key: str
    allowed_globs: tuple[str, ...]
    blocked_globs: tuple[str, ...]
    allowed_res: tuple[re.Pattern[str], ...]
    blocked_res: tuple[re.Pattern[str], ...]
    source_path: str


# ---------------------------------------------------------------------------
# glob → regex (fnmatch-style with ** support, / is a real separator)
# ---------------------------------------------------------------------------

def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate a glob into a full-path regex.

    `**/` matches zero or more leading directories, `**` matches across `/`,
    `*` and `?` never cross `/`. Case-insensitive: the Second Brain lives on
    case-insensitive filesystems (Windows/macOS), so `Personal/x.md` must
    still hit `personal/**` (fail-safe).
    """
    out: list[str] = []
    i, n = 0, len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if pattern[i : i + 3] == "**/":
                out.append("(?:.*/)?")
                i += 3
            elif pattern[i : i + 2] == "**":
                out.append(".*")
                i += 2
            else:
                out.append("[^/]*")
                i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("".join(out) + r"\Z", re.IGNORECASE)


def _normalize_path(path: str) -> str:
    """Posix-normalize a relative path: forward slashes, no ./ or leading /."""
    p = str(path).replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    p = p.lstrip("/")
    return re.sub(r"/{2,}", "/", p)


# ---------------------------------------------------------------------------
# config loading (cached; reset hook for tests)
# ---------------------------------------------------------------------------

_cache_lock = threading.Lock()
_config_cache: dict[str, PrivacyConfig] = {}


def _parse_label(value: Any) -> Optional[PrivacyLabel]:
    """Case-insensitive value → PrivacyLabel, or None if not a valid label."""
    if isinstance(value, PrivacyLabel):
        return value
    if isinstance(value, str):
        try:
            return PrivacyLabel(value.strip().lower())
        except ValueError:
            return None
    return None


def _fail_closed_config(source: str) -> PrivacyConfig:
    """Config used when privacy.yaml is missing/unreadable: everything that
    relies on path rules or the default label becomes SENSITIVE → gate blocks.
    """
    return PrivacyConfig(
        default_label=PrivacyLabel.SENSITIVE,
        frontmatter_key="privacy",
        internal_external_ok_key="external_ok",
        allowed_globs=(),
        blocked_globs=(),
        allowed_res=(),
        blocked_res=(),
        source_path=source,
    )


def _build_config(path: str) -> PrivacyConfig:
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return _fail_closed_config(f"{path} (unreadable — fail closed)")
    if not isinstance(raw, dict):
        return _fail_closed_config(f"{path} (invalid — fail closed)")

    default_label = _parse_label(raw.get("default_label"))
    if default_label is None:
        # Contract default (§10). Only used when the file exists but the
        # default_label value is absent/invalid.
        default_label = PrivacyLabel.INTERNAL

    external_ai = raw.get("external_ai") or {}
    if not isinstance(external_ai, dict):
        external_ai = {}
    allowed = tuple(str(g) for g in (external_ai.get("allowed") or []) if g)
    blocked = tuple(str(g) for g in (external_ai.get("blocked") or []) if g)

    return PrivacyConfig(
        default_label=default_label,
        frontmatter_key=str(raw.get("frontmatter_key") or "privacy"),
        internal_external_ok_key=str(raw.get("internal_external_ok_key") or "external_ok"),
        allowed_globs=allowed,
        blocked_globs=blocked,
        allowed_res=tuple(_glob_to_regex(g) for g in allowed),
        blocked_res=tuple(_glob_to_regex(g) for g in blocked),
        source_path=path,
    )


def load_privacy_config(path: Optional[str] = None) -> PrivacyConfig:
    """Load (and cache) the privacy config from settings.privacy_config_path."""
    cfg_path = path or get_settings().privacy_config_path
    key = str(cfg_path)
    with _cache_lock:
        cfg = _config_cache.get(key)
        if cfg is None:
            cfg = _build_config(cfg_path)
            _config_cache[key] = cfg
        return cfg


def reset_privacy_config_cache() -> None:
    """Test/reload hook: drop cached configs (pair with config.reset_settings_cache)."""
    with _cache_lock:
        _config_cache.clear()


# ---------------------------------------------------------------------------
# classify
# ---------------------------------------------------------------------------

def _lookup_key(mapping: dict, wanted: str) -> tuple[bool, Any]:
    """Exact key first, then first case-insensitive match (insertion order)."""
    if wanted in mapping:
        return True, mapping[wanted]
    folded = wanted.casefold()
    for k, v in mapping.items():
        if isinstance(k, str) and k.casefold() == folded:
            return True, v
    return False, None


def classify(
    path: Optional[str],
    frontmatter: Optional[dict],
    default: Optional[PrivacyLabel] = None,
) -> PrivacyLabel:
    """Deterministic label for a note/file. NEVER AI-decided.

    Precedence (§10):
      1. explicit frontmatter[frontmatter_key] — case-insensitive value;
         a present-but-invalid value fails safe to SENSITIVE.
      2. path rules from privacy.yaml — blocked globs → SENSITIVE (checked
         first: block beats allow), allowed globs → PUBLIC.
      3. `default` argument if given, else privacy.yaml default_label.
    """
    cfg = load_privacy_config()

    if frontmatter:
        found, value = _lookup_key(frontmatter, cfg.frontmatter_key)
        if found and value is not None:
            label = _parse_label(value)
            return label if label is not None else PrivacyLabel.SENSITIVE

    if path:
        rel = _normalize_path(path)
        for rx in cfg.blocked_res:
            if rx.match(rel):
                return PrivacyLabel.SENSITIVE
        for rx in cfg.allowed_res:
            if rx.match(rel):
                return PrivacyLabel.PUBLIC

    if default is not None:
        label = _parse_label(default)
        return label if label is not None else PrivacyLabel.SENSITIVE
    return cfg.default_label


# ---------------------------------------------------------------------------
# gate
# ---------------------------------------------------------------------------

def _is_external_ok(frontmatter: Optional[dict], key: str) -> bool:
    if not frontmatter:
        return False
    found, value = _lookup_key(frontmatter, key)
    if not found:
        return False
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().casefold() in _TRUTHY_STRINGS
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value != 0
    return False


def gate(
    content: str,
    label: PrivacyLabel,
    frontmatter: Optional[dict] = None,
) -> GateDecision:
    """May `content` be sent to a free external AI (§9-L2)? Deterministic.

    Label routing: PUBLIC → allowed; INTERNAL → allowed only when the note's
    frontmatter marks it external_ok; PERSONAL/SENSITIVE/SECRET → blocked,
    always. Then the FULL content is secret-scanned — any hit blocks with
    reason "secret_detected" and safe hit metadata. Both checks must pass.
    """
    cfg = load_privacy_config()

    parsed = _parse_label(label)
    if parsed is None:
        return GateDecision(allowed=False, reason="unknown_label")

    if parsed in _HARD_BLOCKED:
        return GateDecision(allowed=False, reason=f"label_blocked:{parsed.value}")

    if parsed is PrivacyLabel.INTERNAL and not _is_external_ok(
        frontmatter, cfg.internal_external_ok_key
    ):
        return GateDecision(allowed=False, reason="internal_requires_external_ok")

    hits = scan_secrets(content or "")
    if hits:
        return GateDecision(allowed=False, reason="secret_detected", hits=hits)

    return GateDecision(allowed=True, reason="allowed")
