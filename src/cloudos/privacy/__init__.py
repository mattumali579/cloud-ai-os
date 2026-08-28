"""cloudos.privacy — deterministic classifier, external-AI gate, secret scanner.

ARCHITECTURE.md §10. Public surface: classify, gate, scan_secrets.
Nothing in this package is AI-decided, and no secret material ever appears
in a return value, log, or error — safe metadata only.
"""
from cloudos.privacy.rules import (
    classify,
    gate,
    load_privacy_config,
    reset_privacy_config_cache,
)
from cloudos.privacy.secrets import scan_secrets

__all__ = [
    "classify",
    "gate",
    "scan_secrets",
    "load_privacy_config",
    "reset_privacy_config_cache",
]
