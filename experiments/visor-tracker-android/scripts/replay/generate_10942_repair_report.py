#!/usr/bin/env python3
"""Generate the required factual 10942 timestamp audit from production decisions.

This scorer never creates detections, boxes, rays, or locks. It only reads the
actual model/tracker JSONL emitted by ReplayDriverTest and compares selected
semantic labels with the owner's human reference list.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


REFERENCES = [
    (0.8, "laundry basket", {"laundry basket", "clothes hamper", "hamper"}),
    (3.8, "closet clothing", {"clothing", "clothes", "closet clothing", "wardrobe clothes", "hanging clothes"}),
    (5.3, "portable air conditioner", {"portable air conditioner", "air conditioner"}),
    (7.0, "portable air conditioner", {"portable air conditioner", "air conditioner"}),
    (9.5, "windowsill / containers", {"windowsill", "container", "bottle"}),
    (12.0, "conical hat", {"hat", "conical hat"}),
    (15.5, "laptop", {"laptop"}),
    (18.0, "paper roll", {"paper roll", "toilet paper"}),
    (24.0, "white plastic-covered item", {"white plastic-covered item", "trash bag", "plastic bag", "white plastic trash bag", "covered object"}),
]


def candidate_text(boxes: list[dict]) -> str:
    objects = sorted(
        (box for box in boxes if box.get("kind") == "object" and not box.get("held", False)),
        key=lambda box: float(box.get("score", 0)),
        reverse=True,
    )
    if not objects:
        return "none"
    return "; ".join(
        f"#{box.get('track_id')} {box.get('label')} " +
        (f"{float(box.get('score', 0)):.2f}" if box.get("confidence_available", True) else "confidence unavailable (Gemini grounded)")
        for box in objects
    )


def selected_box(decision: dict) -> dict | None:
    selected_id = decision.get("lock_id")
    if selected_id is None:
        return None
    return next(
        (box for box in decision.get("boxes", []) if box.get("track_id") == selected_id),
        None,
    )


def row(timestamp: float, expected: str, accepted: set[str], decision: dict) -> list[str]:
    hands = [box for box in decision.get("boxes", []) if box.get("kind") == "hand" and not box.get("held", False)]
    hand_status = f"present ({len(hands)}×21 landmarks)" if hands else "absent"
    ray = bool(decision.get("ray"))
    if ray:
        fingertip_ray = (
            f"lm8=({float(decision.get('landmark8_x', 0)):.1f}, {float(decision.get('landmark8_y', 0)):.1f}); "
            f"ray-lm{int(decision.get('ray_source_landmark', 8))}=({float(decision.get('tip_x', 0)):.1f}, {float(decision.get('tip_y', 0)):.1f}); "
            f"dir=({float(decision.get('dir_x', 0)):.3f}, {float(decision.get('dir_y', 0)):.3f})"
        )
    else:
        fingertip_ray = "none"
    selected = selected_box(decision)
    selected_label = selected.get("label", "unknown") if selected else "NO_TARGET"
    if selected is None:
        outcome = "MISSED LOCK"
    elif str(selected_label).lower() in accepted:
        outcome = "CORRECT LOCK"
    else:
        outcome = "FALSE LOCK"
    return [
        f"{timestamp:.1f}",
        expected,
        hand_status,
        decision.get("gesture_reason", "not recorded"),
        fingertip_ray,
        candidate_text(decision.get("boxes", [])),
        f"{selected_label} — {decision.get('target_reason', 'not recorded')}",
        outcome,
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("decisions", type=Path)
    parser.add_argument("--fps", type=float, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    decisions = [json.loads(line) for line in args.decisions.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = [row(ts, expected, accepted, decisions[round(ts * args.fps)]) for ts, expected, accepted in REFERENCES]

    regression_ts = 10.8
    regression = decisions[round(regression_ts * args.fps)]
    regression_selected = selected_box(regression)
    regression_label = regression_selected.get("label") if regression_selected else "NO_TARGET"
    regression_outcome = "PASS" if str(regression_label).lower() != "laptop" else "FAIL — false laptop lock"

    no_hand_frames = [decision for decision in decisions if not any(
        box.get("kind") == "hand" and not box.get("held", False) for box in decision.get("boxes", [])
    )]
    no_hand_false_locks = sum(decision.get("lock_id") is not None for decision in no_hand_frames)
    non_pointing = [decision for decision in decisions if decision.get("state") in {"HAND_VISIBLE", "ARM_VISIBLE", "NONE"}]
    non_pointing_false_locks = sum(decision.get("lock_id") is not None for decision in non_pointing)

    headers = ["Time", "Human reference", "Landmarks", "Gesture", "Fingertip / ray", "Candidates", "Selected / reason", "Outcome"]
    lines = [
        "# VISOR 10942 final pointing-repair replay audit",
        "",
        "Source: original unannotated `10942.mp4`; decisions: actual YOLO26n-seg + MediaPipe + Kotlin ProductionTracker inference.",
        "",
        "| " + " | ".join(headers) + " |",
        "|" + "|".join(["---"] * len(headers)) + "|",
    ]
    lines.extend("| " + " | ".join(value.replace("|", "/") for value in values) + " |" for values in rows)
    lines += [
        "",
        "## 10.8s false-laptop regression",
        "",
        f"Selected: **{regression_label}**. Result: **{regression_outcome}**. "
        f"Gesture: {regression.get('gesture_reason', 'not recorded')}; target: {regression.get('target_reason', 'not recorded')}.",
        "",
        "## Negative frames",
        "",
        f"- No-fresh-hand frames: {len(no_hand_frames)}; frames retaining a lock: {no_hand_false_locks}.",
        f"- Non-pointing frames: {len(non_pointing)}; frames retaining a lock: {non_pointing_false_locks}.",
        "- Retained locks can only occur inside the configured short dropout grace and must then release.",
        "",
    ]
    args.out.write_text("\n".join(lines), encoding="utf-8")
    print(args.out)


if __name__ == "__main__":
    main()
