"""Rebuildable local index. It does not live on a Drive-synced path and holds no frames."""

from __future__ import annotations

import json
from pathlib import Path


class Index:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def add(self, state: dict) -> None:
        entry = {
            "session_id": state["session_id"],
            "created_at": state.get("created_at"),
            "remote_prefix": state.get("remote_prefix"),
            "labels": list(state.get("labels") or []),
            "track_ids": list(state.get("track_ids") or []),
            "media_opt_in": bool(state.get("media_opt_in")),
            "status": "committed",
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")

    def records(self) -> list[dict]:
        if not self.path.is_file():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def search(self, *, label: str | None = None, track_id: str | None = None, day: str | None = None) -> list[dict]:
        found = []
        for record in self.records():
            if label is not None and label not in record.get("labels", []):
                continue
            if track_id is not None and track_id not in record.get("track_ids", []):
                continue
            if day is not None and day not in str(record.get("created_at") or ""):
                continue
            found.append(record)
        return found

    def rebuild(self, states: list[dict]) -> None:
        """Replace the index from committed state records. Safe to run again."""
        if self.path.exists():
            self.path.unlink()
        for state in states:
            self.add(state)
