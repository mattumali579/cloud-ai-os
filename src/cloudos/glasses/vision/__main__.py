"""Show the Visor 3.0.4 camera audit, or describe one local PPM."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cloudos.glasses.vision.capability import visor_304_camera_capability
from cloudos.glasses.vision.capture import PhotoFileAdapter
from cloudos.glasses.vision.service import VisionService, default_store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline glasses vision helper")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("capability", help="Print the Visor 3.0.4 camera-frame audit")
    describe = sub.add_parser("describe", help="Describe one authorized PPM or supported still")
    describe.add_argument("path", type=Path)
    args = parser.parse_args(argv)
    if args.command == "capability":
        json.dump(visor_304_camera_capability(), sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    frame = PhotoFileAdapter().load(args.path, authorized=True)
    result = VisionService(default_store()).handle("describe what I'm looking at", frame)
    json.dump(
        {"speech": result["speech"], "detections": result["detections"], "hardware_verified": False},
        sys.stdout,
        indent=2,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
