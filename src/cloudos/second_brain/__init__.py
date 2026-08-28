"""cloudos.second_brain — Context API over the Git/Markdown Second Brain (Agent 9, §11).

Canonical knowledge lives as Markdown files under ``settings.second_brain_path``.
This module NEVER uploads file content anywhere: the only thing that may leave the
local filesystem is compact ``sb_index`` metadata (path, title, privacy label,
content hash, mtime) upserted through an injected/lazily-acquired DB connection.

Public API (re-exported here):

- ``read_note(path) -> (frontmatter: dict, body: str)``
- ``reindex(conn=None) -> {"scanned": int, "indexed": int, "skipped": int}``
- ``get_context(query, max_files=5, max_chars=8000, for_external=False, conn=None)
  -> ContextBundle``

Documented behavior decisions (chosen where the contract allowed a choice):

1. **Missing configuration fails loud.** If ``second_brain_path`` is unset, or the
   directory does not exist, ALL THREE functions — including ``get_context`` —
   raise ``CloudOSError(DEPENDENCY_UNAVAILABLE)``. We deliberately chose raising
   over returning an empty bundle so callers (worker ``sb.reindex`` handler,
   router) can distinguish "no relevant notes" from "knowledge base unavailable".
2. **Path traversal is refused.** Any note path that resolves outside the Second
   Brain root raises ``CloudOSError(VALIDATION_ERROR)``. A missing note file also
   raises ``VALIDATION_ERROR``.
3. **Frontmatter is tolerant.** Absent, unterminated, malformed, or non-mapping
   YAML frontmatter yields ``{}`` (the original text is preserved as the body —
   content is never dropped on parse failure).
4. **Privacy is fail-closed.** ``classify`` is lazily imported from
   ``cloudos.privacy``; if it is unavailable or raises, the file is labeled
   ``SENSITIVE`` (most restrictive practical default). With ``for_external=True``,
   an unavailable ``cloudos.privacy.gate`` raises
   ``CloudOSError(DEPENDENCY_UNAVAILABLE)`` — content is never exported ungated.
   A per-file gate error excludes that file; if the FINAL gate over the assembled
   bundle text fails (belt-and-braces secret scan), ``get_context`` raises
   ``CloudOSError(SECRET_DETECTED)`` when secret hits were reported, otherwise
   ``CloudOSError(PRIVACY_BLOCKED)`` — with safe metadata only.
5. **No-DB mode degrades gracefully.** ``reindex(conn=None)`` with no usable
   ``cloudos.db`` returns counts only (every readable file counts as ``indexed``,
   nothing persisted); ``get_context`` falls back to a filesystem walk when no
   index rows are available.
6. **Skip-unchanged.** During ``reindex``, a file whose (mtime, content hash) both
   match the existing index row is counted as ``skipped``; unreadable files are
   also counted as ``skipped``. ``.git``, ``.obsidian`` and ``node_modules``
   directories are pruned entirely (not scanned).
"""
from cloudos.second_brain.core import (  # noqa: F401
    get_context,
    parse_frontmatter,
    read_note,
    reindex,
)

__all__ = ["read_note", "reindex", "get_context", "parse_frontmatter"]
