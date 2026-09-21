from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


VERIFIED = "VERIFIED"
UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ApplicantProfile:
    raw: dict[str, Any]
    path: Path

    def field(self, dotted_path: str, default: Any = None, verified_only: bool = True) -> Any:
        node: Any = self.raw
        for part in dotted_path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        if isinstance(node, dict) and "status" in node:
            if verified_only and node.get("status") != VERIFIED:
                return default
            return node.get("value", default)
        return node

    def source(self, dotted_path: str) -> str:
        node: Any = self.raw
        for part in dotted_path.split("."):
            if not isinstance(node, dict) or part not in node:
                return ""
            node = node[part]
        return str(node.get("source", "")) if isinstance(node, dict) else ""

    def verified_values(self) -> set[str]:
        values: set[str] = set()

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                if node.get("status") == VERIFIED and node.get("value") not in (None, ""):
                    value = node["value"]
                    if isinstance(value, list):
                        values.update(str(v).lower() for v in value)
                    elif isinstance(value, (str, int, float, bool)):
                        values.add(str(value).lower())
                else:
                    for child in node.values():
                        walk(child)
            elif isinstance(node, list):
                for child in node:
                    walk(child)

        walk(self.raw)
        return values


class ProfileReader:
    """Reads the runtime export, never mutates the canonical Second Brain."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def read(self) -> ApplicantProfile:
        if not self.path.is_file():
            raise FileNotFoundError(
                f"verified applicant profile not found: {self.path}; run job-agent profile-sync on an authorized host"
            )
        data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            raise ValueError("applicant profile must be a schema_version: 1 mapping")
        return ApplicantProfile(data, self.path)
