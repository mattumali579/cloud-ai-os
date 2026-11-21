#!/usr/bin/env python3
"""Full simulation and annotated replay for Visor_20261120_221717.mp4 (Issue #40).

Decodes every frame with original timestamps, executes production EfficientDet Lite0
and MediaPipe HandLandmarker, runs production Kotlin ProductionTracker &
SpatialTargetingEngine, renders an annotated MP4, and evaluates against ground truth.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

MODULE = Path(r"C:\Users\Admin\cloud-ai-os-visor-tracker\experiments\visor-tracker-android")
WORKTREE = Path(r"C:\Users\Admin\cloud-ai-os-visor-tracker")
DEFAULT_VIDEO = Path(r"C:\Users\Admin\Downloads\Visor_20261120_221717.mp4")
DEFAULT_OUT = WORKTREE / "test-recordings" / "20261120_221717" / "replay"
MODELS = MODULE / "app" / "build" / "generated" / "visionAssets"
GT_PATH = WORKTREE / "test-recordings" / "20261120_221717" / "ground_truth_20261120_221717.json"
GRADLE_BAT = Path(r"C:\Users\Admin\AppData\Local\gradle-dist\gradle-9.6.0\bin\gradle.bat")

OBJECT_MAX_W = 512
HAND_MAX_W = 768
HAND_SCORE = 0.15
OBJECT_SCORE = 0.20

WRIST, INDEX_MCP, INDEX_PIP, INDEX_DIP, INDEX_TIP = 0, 5, 6, 7, 8

HAND_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4),        # thumb
    (0, 5), (5, 6), (6, 7), (7, 8),        # index
    (5, 9), (9, 10), (10, 11), (11, 12),   # middle
    (9, 13), (13, 14), (14, 15), (15, 16), # ring
    (13, 17), (17, 18), (18, 19), (19, 20),# pinky
    (0, 17)                                # palm
]


class Box:
    def __init__(self, kind: str, label: str, score: float, left: float, top: float, right: float, bottom: float,
                 points: list[tuple[float, float]] = None, depths: list[float] = None, track_id: int = 0):
        self.kind = kind
        self.label = label
        self.score = score
        self.left = left
        self.top = top
        self.right = right
        self.bottom = bottom
        self.points = points or []
        self.depths = depths or []
        self.track_id = track_id

    def area(self) -> float:
        return max(0.0, self.right - self.left) * max(0.0, self.bottom - self.top)


def load_detectors():
    from mediapipe.tasks.python.core.base_options import BaseOptions
    from mediapipe.tasks.python.vision import (
        HandLandmarker,
        HandLandmarkerOptions,
        ObjectDetector,
        ObjectDetectorOptions,
        RunningMode,
    )

    obj_model = MODELS / "efficientdet_lite0.tflite"
    hand_model = MODELS / "hand_landmarker.task"
    if not obj_model.exists() or not hand_model.exists():
        raise SystemExit(f"Missing production models in {MODELS}")

    objects = ObjectDetector.create_from_options(
        ObjectDetectorOptions(
            base_options=BaseOptions(model_asset_path=str(obj_model)),
            running_mode=RunningMode.IMAGE,
            score_threshold=OBJECT_SCORE,
            max_results=10,
        )
    )
    hands = HandLandmarker.create_from_options(
        HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(hand_model)),
            running_mode=RunningMode.IMAGE,
            num_hands=2,
            min_hand_detection_confidence=HAND_SCORE,
            min_hand_presence_confidence=HAND_SCORE,
            min_tracking_confidence=HAND_SCORE,
        )
    )
    return objects, hands


def detect_objects(detector, bgr: np.ndarray) -> list[Box]:
    import mediapipe as mp

    h, w = bgr.shape[:2]
    scale = min(1.0, OBJECT_MAX_W / w)
    ow, oh = int(w * scale), int(h * scale)
    small = cv2.resize(bgr, (ow, oh), interpolation=cv2.INTER_AREA)
    rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
    result = detector.detect(mp_image)
    boxes = []
    sx = w / ow
    sy = h / oh
    for det in result.detections:
        cat = det.categories[0]
        bb = det.bounding_box
        name = cat.category_name or "object"
        score = float(cat.score or 0)
        boxes.append(
            Box(
                kind="object",
                label=name,
                score=score,
                left=float(bb.origin_x * sx),
                top=float(bb.origin_y * sy),
                right=float((bb.origin_x + bb.width) * sx),
                bottom=float((bb.origin_y + bb.height) * sy),
            )
        )
    return boxes


def detect_hands(landmarker, bgr: np.ndarray) -> list[Box]:
    import mediapipe as mp

    h, w = bgr.shape[:2]
    scale = min(1.0, HAND_MAX_W / w)
    ow, oh = int(w * scale), int(h * scale)
    small = cv2.resize(bgr, (ow, oh), interpolation=cv2.INTER_AREA)
    rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
    result = landmarker.detect(mp_image)
    boxes = []
    handed = result.handedness if hasattr(result, "handedness") and result.handedness else []
    for idx, lm in enumerate(result.hand_landmarks):
        if len(lm) < 21:
            continue
        score = 0.80
        if idx < len(handed) and len(handed[idx]) > 0:
            score = float(handed[idx][0].score)
        if score < HAND_SCORE:
            continue

        pts = [(p.x * w, p.y * h) for p in lm]
        depths = [float(p.z * w) for p in lm]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        boxes.append(
            Box(
                kind="hand",
                label="hand",
                score=score,
                left=min(xs),
                top=min(ys),
                right=max(xs),
                bottom=max(ys),
                points=pts,
                depths=depths,
            )
        )
    return boxes


def write_detection_record(frame_record: dict) -> str:
    lines = [f"FRAME {frame_record['width']} {frame_record['height']} {frame_record['t']}"]
    for box in frame_record["boxes"]:
        label = box["label"].replace(" ", "\\u0020").replace("\"", "\\\"")
        lines.append(
            f"BOX {box['kind']} \"{label}\" {box['score']:.4f} {box['left']:.2f} {box['top']:.2f} {box['right']:.2f} {box['bottom']:.2f}"
        )
        if box.get("points"):
            flat = " ".join(f"{p[0]:.2f} {p[1]:.2f}" for p in box["points"])
            lines.append("POINTS " + flat)
        if box.get("depths"):
            flat_d = " ".join(f"{d:.4f}" for d in box["depths"])
            lines.append("DEPTHS " + flat_d)
    lines.append("END")
    return "\n".join(lines) + "\n"


def run_kotlin_tracker(out_dir: Path) -> None:
    if not GRADLE_BAT.exists():
        raise SystemExit(f"gradle.bat not found at {GRADLE_BAT}")
    env = os.environ.copy()
    env["ANDROID_HOME"] = r"C:\Users\Admin\AppData\Local\Android\Sdk"
    env["VISOR_REPLAY_DIR"] = str(out_dir)
    print("Executing ProductionTracker / SpatialTargetingEngine via Gradle test driver...", flush=True)
    cmd = [
        str(GRADLE_BAT),
        ":app:testDebugUnitTest",
        "--rerun-tasks",
        "--tests",
        "com.mattumali.visortracker.ReplayDriverTest",
        "--no-daemon",
    ]
    res = subprocess.run(cmd, cwd=MODULE, env=env, capture_output=True, text=True)
    if res.returncode != 0:
        print("Gradle failed:\n" + res.stdout + "\n" + res.stderr)
        raise SystemExit("ProductionTracker replay execution failed")
    print("Production decisions generated successfully.", flush=True)


def draw_annotated_frame(frame: np.ndarray, boxes: list[dict], decision: dict, t: float) -> np.ndarray:
    out = frame.copy()
    h, w = out.shape[:2]

    lock_id = decision.get("lock_id")
    hit_id = decision.get("hit_id")
    has_ray = decision.get("ray", False)
    state = decision.get("targeting_state") or decision.get("state", "NONE")

    # 1. Draw detected object boxes
    for b in boxes:
        kind = b.get("kind")
        label = b.get("label", "")
        tid = b.get("track_id", 0)
        score = b.get("score", 0.0)
        x0, y0, x1, y1 = int(b["left"]), int(b["top"]), int(b["right"]), int(b["bottom"])

        if kind == "object":
            is_locked = (tid == lock_id and lock_id is not None)
            is_hit = (tid == hit_id and hit_id is not None)

            if is_locked:
                color = (0, 255, 0) # Bright green
                thick = 4
                tag = f"LOCKED #{tid} {label.upper()} ({score:.2f})"
            elif is_hit:
                color = (0, 255, 255) # Yellow
                thick = 3
                tag = f"HOVER #{tid} {label} ({score:.2f})"
            elif label.lower() == "person":
                color = (120, 120, 120) # Muted grey for wearer arm/person
                thick = 1
                tag = f"arm#{tid}"
            else:
                color = (255, 180, 0) # Cyan/Blue for normal objects
                thick = 2
                tag = f"#{tid} {label} ({score:.2f})"

            cv2.rectangle(out, (x0, y0), (x1, y1), color, thick)
            cv2.putText(out, tag, (x0, max(24, y0 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2, cv2.LINE_AA)

        elif kind == "hand" and b.get("points"):
            pts = b["points"]
            # Draw skeleton bones
            for p1_idx, p2_idx in HAND_CONNECTIONS:
                if p1_idx < len(pts) and p2_idx < len(pts):
                    pt1 = (int(pts[p1_idx][0]), int(pts[p1_idx][1]))
                    pt2 = (int(pts[p2_idx][0]), int(pts[p2_idx][1]))
                    cv2.line(out, pt1, pt2, (0, 220, 255), 2)

            # Draw joints
            for p in pts:
                cv2.circle(out, (int(p[0]), int(p[1])), 4, (0, 255, 255), -1)

            # Prominent fingertip on index tip (joint 8)
            tip = pts[8]
            cv2.circle(out, (int(tip[0]), int(tip[1])), 10, (0, 0, 255), 3)
            cv2.circle(out, (int(tip[0]), int(tip[1])), 4, (0, 255, 255), -1)

    # 2. Draw aiming ray
    if has_ray:
        tip_x = decision.get("tip_x", 0.0)
        tip_y = decision.get("tip_y", 0.0)
        dir_x = decision.get("dir_x", 0.0)
        dir_y = decision.get("dir_y", 0.0)
        reach = decision.get("reach", 1200.0)

        ray_len = min(reach, max(w, h) * 0.8)
        end_x = int(tip_x + dir_x * ray_len)
        end_y = int(tip_y + dir_y * ray_len)

        # Draw laser-style ray: thick semi-transparent green + solid core
        cv2.line(out, (int(tip_x), int(tip_y)), (end_x, end_y), (0, 255, 0), 4, cv2.LINE_AA)
        cv2.circle(out, (int(tip_x), int(tip_y)), 6, (0, 255, 0), -1)

    # 3. Draw top status HUD banner
    banner_h = 90
    cv2.rectangle(out, (0, 0), (w, banner_h), (20, 20, 20), -1)
    cv2.line(out, (0, banner_h), (w, banner_h), (80, 80, 80), 2)

    lock_text = f"LOCK: #{lock_id}" if lock_id is not None else "LOCK: NONE"
    hit_text = f"AIM HIT: #{hit_id}" if hit_id is not None else "AIM HIT: NONE"
    ray_text = "RAY: ACTIVE" if has_ray else "RAY: OFF"

    hud_line1 = f"Visor Real Replay | t={t:5.2f}s | State: {state} | {ray_text}"
    hud_line2 = f"{lock_text} | {hit_text}"

    cv2.putText(out, hud_line1, (20, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.85, (0, 255, 180), 2, cv2.LINE_AA)
    cv2.putText(out, hud_line2, (20, 75), cv2.FONT_HERSHEY_SIMPLEX, 0.85, (255, 255, 255), 2, cv2.LINE_AA)

    return out


def segment_at(gt: dict, t: float) -> dict:
    for seg in gt["segments"]:
        if seg["t0"] <= t < seg["t1"]:
            return seg
    return gt["segments"][-1]


def lock_in_region(lock_box: dict | None, region: list[float] | None, fw: int, fh: int) -> bool:
    if lock_box is None or not region:
        return False
    cx = (lock_box["left"] + lock_box["right"]) * 0.5 / fw
    cy = (lock_box["top"] + lock_box["bottom"]) * 0.5 / fh
    x0, y0, x1, y1 = region
    return x0 <= cx <= x1 and y0 <= cy <= y1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", type=Path, default=DEFAULT_VIDEO)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--fps", type=float, default=10.0)
    parser.add_argument("--skip-detect", action="store_true")
    args = parser.parse_args()

    video_path = args.video
    if not video_path.exists():
        raise SystemExit(f"Video file not found at {video_path}")

    out_dir = args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    gt = json.loads(GT_PATH.read_text(encoding="utf-8"))

    cap = cv2.VideoCapture(str(video_path))
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_src_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    fh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    step = max(1, int(round(src_fps / args.fps)))

    print(f"Loaded {video_path.name}: {fw}x{fh}, {src_fps:.2f} fps, {total_src_frames} frames ({total_src_frames/src_fps:.2f}s)")
    print(f"Sampling at {args.fps:.1f} fps (every {step} frames)...")

    detections_file = out_dir / "detections.txt"

    if args.skip_detect and detections_file.exists():
        print(f"Skipping detection, loading existing {detections_file}...")
        # read saved detections
        # parse line protocol if needed or read cached json
    else:
        obj_det, hand_det = load_detectors()
        detections = []
        frame_i = 0
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            if frame_i % step == 0:
                t = frame_i / src_fps
                objs = detect_objects(obj_det, frame)
                hands = detect_hands(hand_det, frame)

                boxes_payload = []
                for b in objs:
                    boxes_payload.append({
                        "kind": "object", "label": b.label, "score": b.score,
                        "left": b.left, "top": b.top, "right": b.right, "bottom": b.bottom
                    })
                for h in hands:
                    boxes_payload.append({
                        "kind": "hand", "label": "hand", "score": h.score,
                        "left": h.left, "top": h.top, "right": h.right, "bottom": h.bottom,
                        "points": h.points, "depths": h.depths
                    })

                detections.append({
                    "t": round(t, 3), "width": fw, "height": fh, "boxes": boxes_payload
                })
                if len(detections) % 50 == 0:
                    print(f"Detected {len(detections)} frames ({t:.1f}s)...", flush=True)

            frame_i += 1
        cap.release()

        # Write detections line protocol for Kotlin driver
        with open(detections_file, "w", encoding="utf-8") as f:
            for d in detections:
                f.write(write_detection_record(d))
        print(f"Wrote {len(detections)} detection frames to {detections_file}.", flush=True)

    # Run Kotlin ProductionTracker
    run_kotlin_tracker(out_dir)

    # Read decisions produced by Kotlin
    decisions_path = out_dir / "decisions.jsonl"
    decisions = [json.loads(line) for line in decisions_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    # Render Annotated Video
    replay_mp4 = out_dir / "Visor_20261120_221717_replay.mp4"
    print(f"Rendering annotated replay video to {replay_mp4} at {args.fps} fps...", flush=True)

    # Use 1296x1728 or full 1728x2304
    out_w, out_h = fw, fh
    writer = cv2.VideoWriter(
        str(replay_mp4),
        cv2.VideoWriter_fourcc(*"mp4v"),
        args.fps,
        (out_w, out_h),
    )

    cap = cv2.VideoCapture(str(video_path))
    frame_i = 0
    sample_idx = 0
    rows = []

    while True:
        ret, frame = cap.read()
        if not ret or sample_idx >= len(decisions):
            break
        if frame_i % step != 0:
            frame_i += 1
            continue

        decision = decisions[sample_idx]
        t = frame_i / src_fps
        seg = segment_at(gt, t)

        annotated = draw_annotated_frame(frame, decision.get("boxes", []), decision, t)
        writer.write(annotated)

        # Ground truth evaluation
        locked_id = decision.get("lock_id")
        hit_id = decision.get("hit_id")
        has_ray = decision.get("ray", False)
        lock_box = next((b for b in decision.get("boxes", []) if b.get("track_id") == locked_id), None)

        is_pointing_intended = (seg["hand"] == "pointing")
        is_target_region = seg.get("region") is not None

        target_ok = False
        if not is_pointing_intended or seg["target"] == "none":
            # Pass if no lock on an intentional target
            target_ok = (locked_id is None)
        else:
            # Pointing intended: lock must fall inside ground-truth target region
            target_ok = lock_in_region(lock_box, seg.get("region"), fw, fh)

        ray_hit_target = False
        if is_pointing_intended and has_ray and seg.get("region"):
            # Check if ray enters target region
            tip_x = decision.get("tip_x", 0)
            tip_y = decision.get("tip_y", 0)
            dir_x = decision.get("dir_x", 0)
            dir_y = decision.get("dir_y", 0)
            reach = decision.get("reach", 1200)
            r = seg["region"]
            x0, y0, x1, y1 = r[0] * fw, r[1] * fh, r[2] * fw, r[3] * fh
            # Simple ray-box test
            for dist_step in range(10, int(reach), 20):
                px = tip_x + dir_x * dist_step
                py = tip_y + dir_y * dist_step
                if x0 <= px <= x1 and y0 <= py <= y1:
                    ray_hit_target = True
                    break

        rows.append({
            "t": round(t, 3),
            "gt_hand": seg["hand"],
            "gt_target": seg["target"],
            "state": decision.get("state"),
            "targeting_state": decision.get("targeting_state"),
            "locked_id": locked_id,
            "hit_id": hit_id,
            "has_ray": has_ray,
            "target_ok": target_ok,
            "ray_hit_target": ray_hit_target,
            "lock_label": lock_box.get("label") if lock_box else None,
        })

        sample_idx += 1
        frame_i += 1

    cap.release()
    writer.release()
    print(f"Annotated replay video saved: {replay_mp4}", flush=True)

    # Compute Metrics
    pointing_rows = [r for r in rows if r["gt_hand"] == "pointing"]
    aimed_target_rows = [r for r in pointing_rows if r["gt_target"] != "none"]
    none_rows = [r for r in rows if r["gt_target"] == "none"]

    total_pointing_frames = len(pointing_rows)
    ray_active_count = sum(1 for r in pointing_rows if r["has_ray"])
    ray_hit_target_count = sum(1 for r in aimed_target_rows if r["ray_hit_target"])
    correct_lock_count = sum(1 for r in aimed_target_rows if r["target_ok"] and r["locked_id"] is not None)
    false_lock_in_none = sum(1 for r in none_rows if r["locked_id"] is not None)

    metrics = {
        "video": str(video_path),
        "replay_mp4": str(replay_mp4),
        "total_frames_evaluated": len(rows),
        "ground_truth_pointing_frames": total_pointing_frames,
        "ground_truth_none_frames": len(none_rows),
        "ray_active_while_pointing_rate": round(ray_active_count / max(1, total_pointing_frames), 4),
        "ray_hit_target_rate": round(ray_hit_target_count / max(1, len(aimed_target_rows)), 4),
        "target_lock_recall": round(correct_lock_count / max(1, len(aimed_target_rows)), 4),
        "false_lock_rate_when_idle": round(false_lock_in_none / max(1, len(none_rows)), 4),
        "false_locks_count": false_lock_in_none,
        "per_target_breakdown": {},
    }

    for target_name in sorted({r["gt_target"] for r in aimed_target_rows}):
        group = [r for r in aimed_target_rows if r["gt_target"] == target_name]
        g_correct = sum(1 for r in group if r["target_ok"] and r["locked_id"] is not None)
        g_ray = sum(1 for r in group if r["ray_hit_target"])
        metrics["per_target_breakdown"][target_name] = {
            "frames": len(group),
            "lock_recall": round(g_correct / len(group), 4),
            "ray_hit_rate": round(g_ray / len(group), 4),
            "locked_labels": list({r["lock_label"] for r in group if r["lock_label"]}),
        }

    metrics_file = out_dir / "metrics.json"
    metrics_file.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    print("\n=== REPLAY METRICS SUMMARY ===")
    print(json.dumps(metrics, indent=2))
    return metrics


if __name__ == "__main__":
    main()
