"""Local session archive and encrypted upload spool for glasses vision.

Events are kept. Raw frames are off unless a caller opts in. Nothing in this
package stores a Drive folder id, an OAuth token, or a passphrase. The host
worker drains the spool with ``glasses.archive.drain``.
"""

from cloudos.glasses.archive.schema import SCHEMA_EVENT, build_event
from cloudos.glasses.archive.session import SessionBundle

__all__ = ["SCHEMA_EVENT", "SessionBundle", "build_event"]
