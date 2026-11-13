"""Bounds for a short, visible, user-started camera session."""

from __future__ import annotations

from dataclasses import dataclass

# Policy stop, not a calibration of the Ray-Ban temperature sensor.
DEFAULT_THERMAL_STOP_C = 45.0
HOT_STATES = frozenset({"hot", "critical", "severe", "emergency"})


@dataclass(frozen=True)
class SafetyLimits:
    max_session_s: float = 20.0
    max_fps: float = 2.0
    max_frames: int = 40
    thermal_stop_c: float = DEFAULT_THERMAL_STOP_C
    min_battery_percent: int = 15
    retain_frames: bool = False
    lost_after_misses: int = 5

    def __post_init__(self) -> None:
        if self.max_fps <= 0 or self.max_fps > 2:
            raise ValueError("tracking is capped at 2 frames per second")
        if self.max_session_s <= 0 or self.max_session_s > 60:
            raise ValueError("a tracking session must stay within 60 seconds")
        if self.max_frames < 1 or self.max_frames > 120:
            raise ValueError("frame cap is out of range")

    @property
    def min_interval_s(self) -> float:
        return 1.0 / self.max_fps


def heat_or_battery(
    limits: SafetyLimits,
    *,
    thermal_c: float | None = None,
    thermal_state: str | None = None,
    battery_percent: int | None = None,
) -> str | None:
    """Return ``thermal`` or ``battery`` when capture must stop before more work."""
    state = (thermal_state or "").strip().lower()
    if state in HOT_STATES:
        return "thermal"
    if thermal_c is not None and thermal_c >= limits.thermal_stop_c:
        return "thermal"
    if battery_percent is not None and battery_percent < limits.min_battery_percent:
        return "battery"
    return None
