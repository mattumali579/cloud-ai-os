"""Versioned glasses archive records. No invented depth, speed, or durable ids."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

SCHEMA_EVENT = "glasses.archive.event.v1"
SCHEMA_MANIFEST = "glasses.archive.manifest.v1"
SCHEMA_SUMMARY = "glasses.archive.summary.v1"

_FORBIDDEN = {
    "depth_m",
    "distance_m",
    "speed_mps",
    "durable_id",
    "permanent_id",
    "face_embedding",
    "face_template",
    "identity_name",
}


class SchemaError(ValueError):
    """A record claimed something this archive does not store."""


def build_event(
    *,
    session_id: str,
    source: str,
    model_name: str,
    model_version: str,
    t: datetime | None = None,
    event_id: str | None = None,
    detections: list[dict] | None = None,
    track_id: str | None = None,
    trajectory: list[dict] | None = None,
    gesture: str | None = None,
    landmarks: list | None = None,
    selection_method: str | None = None,
    target_track_id: str | None = None,
    last_seen: str | None = None,
    lost: bool = False,
    frame_count: int = 0,
    motion: dict | None = None,
    error: dict | None = None,
    media_sha256: str | None = None,
    hardware_verified: bool = False,
) -> dict:
    """Return one event. Landmark coordinates are stored only when provided."""
    if hardware_verified:
        raise SchemaError("this archive cannot mark a record hardware-verified")
    if track_id is not None and not str(track_id).startswith("trk-"):
        raise SchemaError("track ids are session-scoped and use the trk- prefix")
    if landmarks is not None and len(landmarks) != 21:
        raise SchemaError("landmarks are stored only as the 21 hand points")
    found = [_checked_detection(item) for item in detections or []]
    path = [_checked_point(item) for item in trajectory or []]
    if motion is not None:
        _checked_motion(motion)
    when = t or datetime.now(timezone.utc)
    if when.tzinfo is None:
        raise SchemaError("timestamps must be timezone-aware UTC")
    record = {
        "schema": SCHEMA_EVENT,
        "event_id": event_id or str(uuid.uuid4()),
        "session_id": session_id,
        "t": when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": source,
        "camera": {"authorized": True, "hardware_verified": False, "persistent_phone_store": False},
        "model": {"name": model_name, "version": model_version},
        "detections": found,
        "track_id": track_id,
        "trajectory": path,
        "gesture": None
        if gesture is None
        else {"type": gesture, "landmark_count": 0 if landmarks is None else 21, "landmarks": landmarks},
        "selection": {
            "method": selection_method,
            "target_track_id": target_track_id or track_id,
            "durable_identity": False,
        },
        "last_seen": last_seen,
        "lost": bool(lost),
        "counts": {"detections": len(found), "frames": int(frame_count)},
        "motion": motion,
        "error": error,
        "media_sha256": media_sha256,
        "face_biometrics": False,
    }
    _reject_forbidden(record)
    return record


def _checked_detection(item: dict) -> dict:
    box = item.get("bbox_norm")
    if box is not None:
        if len(box) != 4 or any(not isinstance(value, (int, float)) or value < 0 or value > 1 for value in box):
            raise SchemaError("bbox_norm is four numbers from 0 to 1")
    return {
        "class": str(item.get("class") or item.get("label") or ""),
        "confidence": float(item.get("confidence", 0)),
        "bbox_norm": None if box is None else [float(value) for value in box],
    }


def _checked_point(item: dict) -> dict:
    box = item.get("bbox_norm")
    if box is not None and (
        len(box) != 4 or any(not isinstance(value, (int, float)) or value < 0 or value > 1 for value in box)
    ):
        raise SchemaError("trajectory bbox_norm is four numbers from 0 to 1")
    return {"t": item.get("t"), "bbox_norm": None if box is None else [float(value) for value in box]}


def _checked_motion(motion: dict) -> None:
    if any(key in motion for key in ("depth_m", "distance_m", "speed_mps")):
        raise SchemaError("motion must not invent distance or speed")
    if "unit" not in motion or "calibrated" not in motion or "confidence" not in motion:
        raise SchemaError("motion needs a unit, a calibrated flag, and a confidence")
    if motion.get("calibrated") is True and motion.get("unit") == "unspecified":
        raise SchemaError("a calibrated motion needs a real unit")


def _reject_forbidden(record: dict) -> None:
    found = _keys(record) & _FORBIDDEN
    if found:
        raise SchemaError(f"forbidden archive fields: {sorted(found)}")


def _keys(value) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        found.update(value)
        for item in value.values():
            found.update(_keys(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_keys(item))
    return found
