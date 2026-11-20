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
        # Visor glasses.status uses thermal: normal|moderate|... and throttling bool.
        # "moderate" is common during Live View — do NOT hard-stop; only hot/critical/throttle.
        thermal = str(status.get("thermal") or "").strip().lower()
        if thermal in {"hot", "critical", "emergency", "overheat", "overheated"}:
            raise SafetyStop(f"Glasses thermal state is {thermal}; stopping capture immediately.")
        if status.get("throttling") or status.get("hot") or status.get("thermal_throttle"):
            raise SafetyStop("Glasses reported heat/thermal throttle; stopping capture.")
