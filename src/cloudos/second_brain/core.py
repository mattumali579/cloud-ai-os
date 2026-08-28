"""Implementation of the Second Brain context API. See package docstring for the
documented behavior decisions. Owned by Agent 9."""
from __future__ import annotations

import importlib
import os
import re
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

import yaml

from cloudos.config import REPO_ROOT, get_settings
from cloudos.contracts import (
    CloudOSError,
    ContextBundle,
    ErrorCode,
    PrivacyLabel,
    sha256_hash,
)

# Directories pruned from every walk — never scanned, never counted.
SKIP_DIRS = frozenset({".git", ".obsidian", "node_modules"})

# How many shortlisted files get a body scan in get_context (bounded work).
_SHORTLIST_FACTOR = 4
_SHORTLIST_MIN = 12
_BODY_COUNT_CAP = 5  # per-keyword occurrence cap so one spammy file can't dominate
_TITLE_WEIGHT = 3
_PATH_WEIGHT = 2
_STAGE1_MULTIPLIER = 10  # title/path relevance outranks body-only matches

_UPSERT_SQL = (
    "INSERT INTO sb_index (path, title, privacy_label, content_hash, modified_at, indexed_at) "
    "VALUES (%s, %s, %s, %s, %s, %s) "
    "ON CONFLICT (path) DO UPDATE SET "
    "title = EXCLUDED.title, privacy_label = EXCLUDED.privacy_label, "
    "content_hash = EXCLUDED.content_hash, modified_at = EXCLUDED.modified_at, "
    "indexed_at = EXCLUDED.indexed_at"
)
_SELECT_INDEX_SQL = (
    "SELECT path, title, privacy_label, content_hash, modified_at FROM sb_index"
)

_H1_RE = re.compile(r"^#(?!#)\s+(.+?)\s*$", re.MULTILINE)
_KEYWORD_RE = re.compile(r"[a-z0-9]+")


# ---------------------------------------------------------------------------
# Root resolution / config guard
# ---------------------------------------------------------------------------

def _require_root() -> Path:
    """Resolve the Second Brain root or raise DEPENDENCY_UNAVAILABLE."""
    raw = (get_settings().second_brain_path or "").strip()
    if not raw:
        raise CloudOSError(
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            "SECOND_BRAIN_PATH is not configured; the Second Brain is unavailable",
            {"setting": "second_brain_path"},
        )
    root = Path(raw)
    if not root.is_absolute():
        root = REPO_ROOT / root
    root = root.resolve()
    if not root.is_dir():
        raise CloudOSError(
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            "Second Brain directory does not exist or is not a directory",
            {"path": str(root)},
        )
    return root


def _resolve_note_path(root: Path, path: Any) -> Path:
    """Resolve `path` against the root; refuse anything escaping it."""
    raw = os.fspath(path)
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve()
    except OSError as exc:
        raise CloudOSError(
            ErrorCode.VALIDATION_ERROR, "note path could not be resolved", {"path": str(raw)}
        ) from exc
    if not resolved.is_relative_to(root):
        raise CloudOSError(
            ErrorCode.VALIDATION_ERROR,
            "note path escapes the Second Brain root",
            {"path": str(raw)},
        )
    return resolved


# ---------------------------------------------------------------------------
# Frontmatter / note reading
# ---------------------------------------------------------------------------

def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split leading `---` YAML frontmatter from a note.

    Returns (frontmatter_dict, body). Tolerant: absent, unterminated, malformed,
    or non-mapping frontmatter yields {} — and on any parse failure the ORIGINAL
    full text is returned as the body (content is never dropped).
    """
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, text
    for i in range(1, len(lines)):
        if lines[i].strip() in ("---", "..."):
            fm_text = "".join(lines[1:i])
            body = "".join(lines[i + 1 :])
            try:
                data = yaml.safe_load(fm_text)
            except yaml.YAMLError:
                return {}, text
            if data is None:
                return {}, body
            if not isinstance(data, dict):
                return {}, text
            return data, body
    return {}, text  # unterminated fence


def read_note(path: Any) -> tuple[dict, str]:
    """Read one note (path relative to the Second Brain root, or absolute inside it).

    Returns (frontmatter, body). Raises CloudOSError(VALIDATION_ERROR) for paths
    escaping the root or missing files; DEPENDENCY_UNAVAILABLE if the root is
    unset/missing.
    """
    root = _require_root()
    resolved = _resolve_note_path(root, path)
    if not resolved.is_file():
        raise CloudOSError(
            ErrorCode.VALIDATION_ERROR,
            "note not found",
            {"path": resolved.relative_to(root).as_posix()},
        )
    try:
        text = resolved.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise CloudOSError(
            ErrorCode.VALIDATION_ERROR,
            "note could not be read",
            {"path": resolved.relative_to(root).as_posix()},
        ) from exc
    return parse_frontmatter(text)


def _iter_notes(root: Path) -> Iterator[Path]:
    """Yield every **/*.md under root, pruning SKIP_DIRS, in deterministic order."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            if name.lower().endswith(".md"):
                yield Path(dirpath) / name


def _title_for(body: str, file_path: Path) -> str:
    match = _H1_RE.search(body)
    if match:
        return match.group(1)
    return file_path.stem


# ---------------------------------------------------------------------------
# Lazy privacy / db integration (Agents 10 and 4 — written concurrently)
# ---------------------------------------------------------------------------

def _coerce_label(value: Any) -> PrivacyLabel:
    if isinstance(value, PrivacyLabel):
        return value
    try:
        return PrivacyLabel(str(value).lower())
    except ValueError:
        return PrivacyLabel.SENSITIVE  # unknown label => fail closed


def _classify(rel_posix_path: str, frontmatter: dict) -> PrivacyLabel:
    """Lazy cloudos.privacy.classify; fail-closed to SENSITIVE if unavailable."""
    try:
        privacy = importlib.import_module("cloudos.privacy")
        label = privacy.classify(rel_posix_path, frontmatter)
    except Exception:
        return PrivacyLabel.SENSITIVE
    return _coerce_label(label)


def _require_gate():
    """Lazy cloudos.privacy.gate; for_external work MUST NOT proceed without it."""
    try:
        privacy = importlib.import_module("cloudos.privacy")
        return privacy.gate
    except Exception as exc:
        raise CloudOSError(
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            "cloudos.privacy.gate is unavailable; refusing to assemble external context",
            {},
        ) from exc


def _acquire_conn(stack: ExitStack):
    """Best-effort lazy DB connection via cloudos.db; None when unavailable."""
    try:
        db = importlib.import_module("cloudos.db")
    except Exception:
        return None
    try:
        return stack.enter_context(db.get_conn())
    except Exception:
        return None


# ---------------------------------------------------------------------------
# reindex
# ---------------------------------------------------------------------------

def _load_index(conn) -> dict[str, dict]:
    try:
        rows = conn.execute(_SELECT_INDEX_SQL).fetchall()
    except Exception:
        return {}
    out: dict[str, dict] = {}
    for row in rows or []:
        try:
            out[str(row["path"])] = dict(row)
        except (TypeError, KeyError):
            continue
    return out


def _same_mtime(stored: Any, current: datetime) -> bool:
    if isinstance(stored, str):
        try:
            stored = datetime.fromisoformat(stored)
        except ValueError:
            return False
    if not isinstance(stored, datetime):
        return False
    if stored.tzinfo is None:
        stored = stored.replace(tzinfo=timezone.utc)
    return abs((stored - current).total_seconds()) < 1e-6


def reindex(conn=None) -> dict:
    """Walk **/*.md under the Second Brain root and upsert sb_index METADATA.

    Returns {"scanned": n, "indexed": n, "skipped": n}. File CONTENT is never
    uploaded — only path, title, privacy label, sha256 content hash, and mtime.
    With no conn and no usable cloudos.db, returns counts only (nothing persisted).
    Files whose (mtime, hash) match the existing index row are skipped.
    """
    root = _require_root()
    scanned = indexed = skipped = 0
    with ExitStack() as stack:
        c = conn if conn is not None else _acquire_conn(stack)
        existing = _load_index(c) if c is not None else {}
        for file_path in _iter_notes(root):
            scanned += 1
            rel = file_path.relative_to(root).as_posix()
            try:
                stat = file_path.stat()
                text = file_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                skipped += 1
                continue
            mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
            content_hash = sha256_hash(text)
            row = existing.get(rel)
            if (
                row is not None
                and row.get("content_hash") == content_hash
                and _same_mtime(row.get("modified_at"), mtime)
            ):
                skipped += 1
                continue
            frontmatter, body = parse_frontmatter(text)
            title = _title_for(body, file_path)
            label = _classify(rel, frontmatter)
            if c is not None:
                c.execute(
                    _UPSERT_SQL,
                    (
                        rel,
                        title,
                        label.value,
                        content_hash,
                        mtime,
                        datetime.now(tz=timezone.utc),
                    ),
                )
            indexed += 1
    return {"scanned": scanned, "indexed": indexed, "skipped": skipped}


# ---------------------------------------------------------------------------
# get_context
# ---------------------------------------------------------------------------

def _keywords(query: str) -> list[str]:
    seen: list[str] = []
    for token in _KEYWORD_RE.findall((query or "").lower()):
        if token not in seen:
            seen.append(token)
    return seen


def _candidates(conn, root: Path) -> list[tuple[str, str]]:
    """(relative posix path, title) pairs — from sb_index when available, else FS walk."""
    if conn is not None:
        try:
            rows = conn.execute(_SELECT_INDEX_SQL).fetchall()
        except Exception:
            rows = []
        pairs: list[tuple[str, str]] = []
        for row in rows or []:
            try:
                pairs.append((str(row["path"]), str(row.get("title") or "")))
            except (TypeError, KeyError, AttributeError):
                continue
        if pairs:
            return pairs
    return [(p.relative_to(root).as_posix(), p.stem) for p in _iter_notes(root)]


def _stage1_score(rel: str, title: str, keywords: list[str]) -> int:
    title_l = title.lower()
    path_l = rel.lower()
    score = 0
    for kw in keywords:
        if kw in title_l:
            score += _TITLE_WEIGHT
        if kw in path_l:
            score += _PATH_WEIGHT
    return score


def _body_score(body: str, keywords: list[str]) -> int:
    body_l = body.lower()
    return sum(min(body_l.count(kw), _BODY_COUNT_CAP) for kw in keywords)


def get_context(
    query: str,
    max_files: int = 5,
    max_chars: int = 8000,
    for_external: bool = False,
    conn=None,
) -> ContextBundle:
    """Assemble a ContextBundle of the most relevant notes for `query`.

    Deterministic two-stage scoring: keywords against title+path over all
    candidates (sb_index rows when available, else a filesystem walk), then
    keyword occurrences in the bodies of the bounded shortlist; ties break by
    path. When for_external=True, each file's body must pass
    cloudos.privacy.gate for its classified label (rejected paths are recorded
    in bundle.excluded) and the assembled text is gated once more before
    return; a failing final gate raises SECRET_DETECTED/PRIVACY_BLOCKED.
    Truncates to max_chars with truncated=True. Raises DEPENDENCY_UNAVAILABLE
    if the Second Brain root is unset/missing (documented choice — no silent
    empty bundle).
    """
    root = _require_root()
    keywords = _keywords(query)
    if not keywords or max_files <= 0:
        return ContextBundle()

    with ExitStack() as stack:
        c = conn if conn is not None else _acquire_conn(stack)
        candidates = _candidates(c, root)

    scored = sorted(
        ((_stage1_score(rel, title, keywords), rel, title) for rel, title in candidates),
        key=lambda item: (-item[0], item[1]),
    )
    shortlist = scored[: max(max_files * _SHORTLIST_FACTOR, _SHORTLIST_MIN)]

    ranked: list[tuple[int, str, dict, str]] = []  # (-total, rel, frontmatter, body)
    for stage1, rel, _title in shortlist:
        try:
            frontmatter, body = read_note(rel)
        except CloudOSError:
            continue  # stale index row / unreadable file — drop candidate
        total = stage1 * _STAGE1_MULTIPLIER + _body_score(body, keywords)
        if total <= 0:
            continue
        ranked.append((-total, rel, frontmatter, body))
    ranked.sort(key=lambda item: (item[0], item[1]))

    included: list[tuple[str, str]] = []  # (rel, body)
    excluded: list[str] = []
    if for_external:
        gate = _require_gate()
        for _neg, rel, frontmatter, body in ranked:
            if len(included) >= max_files:
                break
            label = _classify(rel, frontmatter)
            try:
                decision = gate(body, label)
                allowed = bool(getattr(decision, "allowed", False))
            except Exception:
                allowed = False  # per-file gate failure => fail closed
            if allowed:
                included.append((rel, body))
            else:
                excluded.append(rel)
    else:
        included = [(rel, body) for _neg, rel, _fm, body in ranked[:max_files]]

    text = "\n\n".join(f"--- {rel} ---\n{body.strip()}" for rel, body in included)

    if for_external and text:
        gate = _require_gate()
        try:
            decision = gate(text, PrivacyLabel.PUBLIC)
            allowed = bool(getattr(decision, "allowed", False))
        except Exception as exc:
            raise CloudOSError(
                ErrorCode.PRIVACY_BLOCKED,
                "privacy gate failed while scanning the assembled context bundle",
                {"files": [rel for rel, _ in included]},
            ) from exc
        if not allowed:
            hits = list(getattr(decision, "hits", []) or [])
            code = ErrorCode.SECRET_DETECTED if hits else ErrorCode.PRIVACY_BLOCKED
            raise CloudOSError(
                code,
                "assembled context bundle was blocked by the privacy gate",
                {
                    "reason": str(getattr(decision, "reason", "")),
                    "files": [rel for rel, _ in included],
                },
            )

    truncated = False
    if len(text) > max_chars:
        text = text[:max_chars]
        truncated = True

    return ContextBundle(
        files=[rel for rel, _ in included],
        text=text,
        truncated=truncated,
        excluded=excluded,
    )
