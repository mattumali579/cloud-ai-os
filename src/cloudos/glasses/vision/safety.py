"""Battery / thermal / retention gates for glasses vision sessions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class SafetyStop(RuntimeError):
    """Capture/tracking must stop for safety."""


@dataclass
class SafetyGate:
    min_battery_percent: int = 15
    max_temperature_c: float = 45.0

    def check(self, glasses_status: dict[str, Any] | None) -> None:
        status = glasses_status or {}
        battery = status.get("battery")
        if battery is not None and float(battery) < self.min_battery_percent:
            raise SafetyStop(f"Low battery ({battery}%); stopping camera session.")
        temp = status.get("temperature_c")
        if temp is None:
            temp = status.get("temperature")
        if temp is not None and float(temp) >= self.max_temperature_c:
            raise SafetyStop(
                f"Glasses thermal limit ({temp}C >= {self.max_temperature_c}C); "
                "stopping capture immediately."
            )
        # Explicit operator/device flags
        if status.get("hot") or status.get("thermal_throttle"):
            raise SafetyStop("Glasses reported heat/thermal throttle; stopping capture.")
