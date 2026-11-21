#!/usr/bin/env python3
"""End-to-end replay and full overlay video renderer for 10942.mp4 (Issue #40).

Runs 10942.mp4 through the real production Visor tracking pipeline:
1. MediaPipe HandLandmarker (21 3D joints) on the 1080x1440 camera viewport.
2. EfficientDet Lite0 ObjectDetector on the camera viewport.
3. Production Kotlin ProductionTracker & SpatialTargetingEngine via Gradle test driver.
4. Composites hand skeleton, prominent fingertip, forward pointing ray, detected objects,
   and target-lock highlights onto the original phone screen recording.
5. If the model cannot reliably identify a target, shows NO_TARGET instead of inventing one.
6. Encodes compatible H.264 video with original AAC audio preserved from 10942.mp4.
7. Opens the resulting MP4 in the Windows video player.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np

MODULE = Path(r"C:\Users\Admin\cloud-ai-os-visor-tracker\experiments\visor-tracker-android")
WORKTREE = Path(r"C:\Users\Admin\cloud-ai-os-visor-tracker")
DEFAULT_VIDEO = Path(r"C:\Users\Admin\Downloads\10942.mp4")
DEFAULT_OUT_DIR = WORKTREE / "test-recordings" / "10942"
DEFAULT_OUT_MP4 = DEFAULT_OUT_DIR / "10942_overlay_replay.mp4"
MODELS = MODULE / "app" / "build" / "generated" / "visionAssets"
GRADLE_BAT = Path(r"C:\Users\Admin\AppData\Local\gradle-dist\gradle-9.6.0\bin\gradle.bat")

OBJECT_MAX_W = 512
HAND_MAX_W = 768
HAND_SCORE = 0.15
OBJECT_SCORE = 0.20

VIEWPORT_TOP = 438
VIEWPORT_BOTTOM = 1878
CAM_W = 1080
CAM_H = 1440

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


def detect_objects(detector, bgr_cam: np.ndarray) -> list[Box]:
    import mediapipe as mp

    h, w = bgr_cam.shape[:2]
    scale = min(1.0, OBJECT_MAX_W / w)
    ow, oh = int(w * scale), int(h * scale)
    small = cv2.resize(bgr_cam, (ow, oh), interpolation=cv2.INTER_AREA)
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


def detect_hands(landmarker, bgr_cam: np.ndarray) -> list[Box]:
    import mediapipe as mp

    h, w = bgr_cam.shape[:2]
    scale = min(1.0, HAND_MAX_W / w)
    ow, oh = int(w * scale), int(h * scale)
    small = cv2.resize(bgr_cam, (ow, oh), interpolation=cv2.INTER_AREA)
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
    lines = [f"FRAME {frame_record['width']} {frame_record['height']} {frame_record['t']:.4f}"]
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


def draw_overlay(frame: np.ndarray, decision: dict, frame_idx: int, total_frames: int, t_sec: float) -> np.ndarray:
    out = frame.copy()
    h, w = out.shape[:2]

    lock_id = decision.get("lock_id")
    hit_id = decision.get("hit_id")
    has_ray = decision.get("ray", False)
    targeting_state = decision.get("targeting_state", "NONE")
    state = decision.get("state", "NONE")
    boxes = decision.get("boxes", [])

    y_offset = VIEWPORT_TOP

    # Find locked and hit box labels if available
    locked_label = None
    hit_label = None
    for b in boxes:
        if b.get("kind") == "object":
            if b.get("track_id") == lock_id and lock_id is not None:
                locked_label = b.get("label", "object")
            if b.get("track_id") == hit_id and hit_id is not None:
                hit_label = b.get("label", "object")

    # 1. Draw detected objects
    for b in boxes:
        kind = b.get("kind")
        label = b.get("label", "")
        tid = b.get("track_id", 0)
        score = b.get("score", 0.0)

        # Map coordinates from camera viewport to full screen
        x0 = int(b["left"])
        y0 = int(b["top"] + y_offset)
        x1 = int(b["right"])
        y1 = int(b["bottom"] + y_offset)

        if kind == "object":
            is_locked = (tid == lock_id and lock_id is not None)
            is_hit = (tid == hit_id and hit_id is not None)

            if is_locked:
                color = (0, 255, 60)  # Neon Green
                thick = 5
                tag = f"LOCKED #{tid} {label.upper()} ({score:.2f})"
                # Draw corner brackets for high-tech HUD look
                cv2.rectangle(out, (x0, y0), (x1, y1), color, thick)
                cw = max(16, (x1 - x0) // 5)
                ch = max(16, (y1 - y0) // 5)
                # corners
                cv2.line(out, (x0, y0), (x0 + cw, y0), (255, 255, 255), 4)
                cv2.line(out, (x0, y0), (x0, y0 + ch), (255, 255, 255), 4)
                cv2.line(out, (x1, y0), (x1 - cw, y0), (255, 255, 255), 4)
                cv2.line(out, (x1, y0), (x1, y0 + ch), (255, 255, 255), 4)
                cv2.line(out, (x0, y1), (x0 + cw, y1), (255, 255, 255), 4)
                cv2.line(out, (x0, y1), (x0, y1 - ch), (255, 255, 255), 4)
                cv2.line(out, (x1, y1), (x1 - cw, y1), (255, 255, 255), 4)
                cv2.line(out, (x1, y1), (x1, y1 - ch), (255, 255, 255), 4)

                # Tag background
                tw, th = cv2.getTextSize(tag, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)[0]
                ty = max(y_offset + 30, y0 - 10)
                cv2.rectangle(out, (x0, ty - th - 6), (x0 + tw + 12, ty + 4), (0, 160, 40), -1)
                cv2.putText(out, tag, (x0 + 6, ty - 2), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv2.LINE_AA)

            elif is_hit:
                color = (0, 220, 255)  # Bright Gold / Yellow
                thick = 3
                tag = f"HOVER #{tid} {label} ({score:.2f})"
                cv2.rectangle(out, (x0, y0), (x1, y1), color, thick)
                tw, th = cv2.getTextSize(tag, cv2.FONT_HERSHEY_SIMPLEX, 0.65, 2)[0]
                ty = max(y_offset + 30, y0 - 8)
                cv2.rectangle(out, (x0, ty - th - 4), (x0 + tw + 8, ty + 2), (0, 140, 180), -1)
                cv2.putText(out, tag, (x0 + 4, ty - 1), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2, cv2.LINE_AA)

            elif label.lower() == "person":
                color = (100, 100, 100)  # Muted grey for wearer arm/person
                thick = 1
                tag = f"arm#{tid}"
                cv2.rectangle(out, (x0, y0), (x1, y1), color, thick)
                cv2.putText(out, tag, (x0, max(y_offset + 20, y0 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
            else:
                color = (255, 170, 0)  # Cyan/Blue
                thick = 2
                tag = f"#{tid} {label} ({score:.2f})"
                cv2.rectangle(out, (x0, y0), (x1, y1), color, thick)
                tw, th = cv2.getTextSize(tag, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)[0]
                ty = max(y_offset + 24, y0 - 6)
                cv2.rectangle(out, (x0, ty - th - 4), (x0 + tw + 6, ty + 2), (20, 20, 20), -1)
                cv2.putText(out, tag, (x0 + 3, ty - 1), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 1, cv2.LINE_AA)

        elif kind == "hand" and b.get("points"):
            pts = b["points"]
            # Draw skeleton bones
            for p1_idx, p2_idx in HAND_CONNECTIONS:
                if p1_idx < len(pts) and p2_idx < len(pts):
                    pt1 = (int(pts[p1_idx][0]), int(pts[p1_idx][1] + y_offset))
                    pt2 = (int(pts[p2_idx][0]), int(pts[p2_idx][1] + y_offset))
                    cv2.line(out, pt1, pt2, (255, 220, 0), 2, cv2.LINE_AA)

            # Draw joints
            for p in pts:
                cv2.circle(out, (int(p[0]), int(p[1] + y_offset)), 4, (0, 255, 255), -1)

            # Prominent fingertip on index tip (joint 8)
            tip = pts[8]
            tip_scr = (int(tip[0]), int(tip[1] + y_offset))
            cv2.circle(out, tip_scr, 12, (0, 240, 255), 3, cv2.LINE_AA)
            cv2.circle(out, tip_scr, 6, (0, 0, 255), -1, cv2.LINE_AA)
            cv2.circle(out, tip_scr, 2, (255, 255, 255), -1, cv2.LINE_AA)

    # 2. Draw forward pointing ray
    if has_ray:
        tip_x = decision.get("tip_x", 0.0)
        tip_y = decision.get("tip_y", 0.0) + y_offset
        dir_x = decision.get("dir_x", 0.0)
        dir_y = decision.get("dir_y", 0.0)
        reach = decision.get("reach", 1200.0)

        ray_len = min(reach, max(CAM_W, CAM_H) * 0.85)
        end_x = int(tip_x + dir_x * ray_len)
        end_y = int(tip_y + dir_y * ray_len)

        # Outer glowing ray
        ray_color = (0, 255, 100) if lock_id is not None else ((0, 220, 255) if hit_id is not None else (0, 160, 255))
        cv2.line(out, (int(tip_x), int(tip_y)), (end_x, end_y), ray_color, 4, cv2.LINE_AA)
        cv2.line(out, (int(tip_x), int(tip_y)), (end_x, end_y), (255, 255, 255), 1, cv2.LINE_AA)
        cv2.circle(out, (int(tip_x), int(tip_y)), 7, ray_color, -1, cv2.LINE_AA)
        cv2.circle(out, (end_x, end_y), 5, (255, 255, 255), -1, cv2.LINE_AA)

    # 3. Top HUD Banner (in top black bar: rows 80 to 220)
    top_bar_y0 = 60
    top_bar_h = 130
    cv2.rectangle(out, (20, top_bar_y0), (w - 20, top_bar_y0 + top_bar_h), (25, 25, 28), -1)
    cv2.rectangle(out, (20, top_bar_y0), (w - 20, top_bar_y0 + top_bar_h), (70, 70, 80), 2)

    # Line 1: Header + frame index + timing
    header_title = "VISOR SPATIAL TARGETING REPLAY"
    time_str = f"t={t_sec:5.2f}s | Frame {frame_idx:04d}/{total_frames}"
    cv2.putText(out, header_title, (40, top_bar_y0 + 35), cv2.FONT_HERSHEY_SIMPLEX, 0.85, (0, 240, 255), 2, cv2.LINE_AA)
    cv2.putText(out, time_str, (w - 380, top_bar_y0 + 35), cv2.FONT_HERSHEY_SIMPLEX, 0.70, (200, 200, 200), 2, cv2.LINE_AA)

    # Line 2: State + Target Badge
    state_str = f"STATE: {targeting_state}"
    cv2.putText(out, state_str, (40, top_bar_y0 + 78), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2, cv2.LINE_AA)

    # Target Badge logic:
    # If model cannot reliably identify a target, show NO_TARGET instead of inventing one!
    if lock_id is not None:
        target_badge = f"LOCKED: #{lock_id} {locked_label.upper() if locked_label else 'TARGET'}"
        badge_bg = (0, 160, 40)
        badge_fg = (255, 255, 255)
    elif hit_id is not None:
        target_badge = f"AIMING: #{hit_id} {hit_label if hit_label else 'CANDIDATE'}"
        badge_bg = (0, 140, 200)
        badge_fg = (255, 255, 255)
    elif has_ray:
        # Pointing is active, but ray does not intersect any candidate object!
        target_badge = "NO_TARGET (AWAITING OBJECT)"
        badge_bg = (30, 80, 200)  # Orange-red
        badge_fg = (255, 255, 255)
    else:
        target_badge = "STANDBY (SEARCHING)"
        badge_bg = (50, 50, 55)
        badge_fg = (180, 180, 180)

    # Draw target badge pill
    bw, bh = cv2.getTextSize(target_badge, cv2.FONT_HERSHEY_SIMPLEX, 0.75, 2)[0]
    bx = 40
    by = top_bar_y0 + 115
    cv2.rectangle(out, (bx, by - bh - 6), (bx + bw + 20, by + 6), badge_bg, -1)
    cv2.rectangle(out, (bx, by - bh - 6), (bx + bw + 20, by + 6), (255, 255, 255), 1)
    cv2.putText(out, target_badge, (bx + 10, by - 1), cv2.FONT_HERSHEY_SIMPLEX, 0.75, badge_fg, 2, cv2.LINE_AA)

    # Ray status
    ray_label = "RAY: ACTIVE (FORWARD)" if has_ray else "RAY: INACTIVE"
    ray_color = (0, 255, 100) if has_ray else (140, 140, 140)
    cv2.putText(out, ray_label, (w - 380, top_bar_y0 + 115), cv2.FONT_HERSHEY_SIMPLEX, 0.70, ray_color, 2, cv2.LINE_AA)

    # 4. Bottom Viewport Boundary Lines (camera viewport markers)
    cv2.line(out, (0, VIEWPORT_TOP), (w, VIEWPORT_TOP), (50, 50, 60), 1)
    cv2.line(out, (0, VIEWPORT_BOTTOM), (w, VIEWPORT_BOTTOM), (50, 50, 60), 1)

    # Bottom status in bottom black bar (rows 1930 to 2050)
    bot_y0 = 1940
    bot_h = 100
    cv2.rectangle(out, (20, bot_y0), (w - 20, bot_y0 + bot_h), (25, 25, 28), -1)
    cv2.rectangle(out, (20, bot_y0), (w - 20, bot_y0 + bot_h), (70, 70, 80), 2)
    cv2.putText(out, "Visor Vision Pipeline | MediaPipe HandLandmarker + EfficientDet-Lite0", (40, bot_y0 + 35),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, (180, 180, 180), 1, cv2.LINE_AA)
    cv2.putText(out, "SpatialTargetingEngine (Kotlin) | Real-time Gesture State Machine", (40, bot_y0 + 75),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 220, 255), 1, cv2.LINE_AA)

    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", type=Path, default=DEFAULT_VIDEO)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--out-mp4", type=Path, default=DEFAULT_OUT_MP4)
    parser.add_argument("--skip-detect", action="store_true")
    parser.add_argument("--skip-kotlin", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    det_file = args.out_dir / "detections.txt"
    dec_file = args.out_dir / "decisions.jsonl"

    cap = cv2.VideoCapture(str(args.video))
    if not cap.isOpened():
        raise SystemExit(f"Cannot open video: {args.video}")

    total_video_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.28
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print(f"Loaded input video: {width}x{height}, {total_video_frames} frames @ {fps:.2f} fps", flush=True)

    limit = args.limit if args.limit else total_video_frames

    # Phase 1: Inference & Detection Extraction
    if not args.skip_detect or not det_file.exists():
        print(f"--- Phase 1: Running MediaPipe and EfficientDet on {limit} frames ---", flush=True)
        objects_det, hands_det = load_detectors()

        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        t0 = time.time()
        with open(det_file, "w", encoding="utf-8") as f_out:
            for i in range(limit):
                ret, frame = cap.read()
                if not ret:
                    break
                t_sec = i / fps
                # Crop camera viewport (438:1878, 0:1080)
                cam_bgr = frame[VIEWPORT_TOP:VIEWPORT_BOTTOM, :]

                # Detect hands on camera frame
                hand_boxes = detect_hands(hands_det, cam_bgr)

                # Detect objects on camera frame (every frame or every 2 frames for speed, or every frame)
                obj_boxes = detect_objects(objects_det, cam_bgr)

                frame_record = {
                    "width": CAM_W,
                    "height": CAM_H,
                    "t": t_sec,
                    "boxes": [
                        {
                            "kind": b.kind,
                            "label": b.label,
                            "score": b.score,
                            "left": b.left,
                            "top": b.top,
                            "right": b.right,
                            "bottom": b.bottom,
                            "points": b.points,
                            "depths": b.depths,
                        }
                        for b in (hand_boxes + obj_boxes)
                    ],
                }
                f_out.write(write_detection_record(frame_record))

                if (i + 1) % 50 == 0 or (i + 1) == limit:
                    elapsed = time.time() - t0
                    rate = (i + 1) / elapsed
                    eta = (limit - (i + 1)) / max(0.01, rate)
                    print(f"Detected {i + 1}/{limit} frames ({rate:.1f} fps, ETA {eta:.1f}s)...", flush=True)

        print(f"Phase 1 complete: wrote detections to {det_file}", flush=True)
    else:
        print(f"Using cached detections from {det_file}", flush=True)

    # Phase 2: Run Kotlin ProductionTracker
    if not args.skip_kotlin or not dec_file.exists():
        print("--- Phase 2: Running Kotlin ProductionTracker / SpatialTargetingEngine ---", flush=True)
        run_kotlin_tracker(args.out_dir)
        print(f"Phase 2 complete: decisions written to {dec_file}", flush=True)
    else:
        print(f"Using cached decisions from {dec_file}", flush=True)

    # Read decisions
    print("Loading production decisions...", flush=True)
    decisions = []
    with open(dec_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                decisions.append(json.loads(line))
    print(f"Loaded {len(decisions)} decision steps.", flush=True)

    # Phase 3: Composite Overlay & Render Video with Audio Muxing
    print("--- Phase 3: Compositing overlays and encoding H.264 video with AAC audio ---", flush=True)
    temp_raw_mp4 = args.out_dir / "temp_video_only.mp4"

    # Start ffmpeg process to encode video from raw frames pipe
    ffmpeg_cmd = [
        "ffmpeg",
        "-y",
        "-f", "rawvideo",
        "-vcodec", "rawvideo",
        "-s", f"{width}x{height}",
        "-pix_fmt", "bgr24",
        "-r", str(fps),
        "-i", "-",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-preset", "fast",
        "-crf", "19",
        str(temp_raw_mp4),
    ]

    proc = subprocess.Popen(ffmpeg_cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)

    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    t0 = time.time()
    for i in range(min(limit, len(decisions))):
        ret, frame = cap.read()
        if not ret:
            break
        t_sec = i / fps
        dec = decisions[i]

        annotated = draw_overlay(frame, dec, i + 1, min(limit, len(decisions)), t_sec)
        proc.stdin.write(annotated.tobytes())

        if (i + 1) % 100 == 0 or (i + 1) == min(limit, len(decisions)):
            elapsed = time.time() - t0
            rate = (i + 1) / elapsed
            print(f"Rendered {i + 1}/{min(limit, len(decisions))} frames ({rate:.1f} fps)...", flush=True)

    proc.stdin.flush()
    _, stderr_bytes = proc.communicate()
    cap.release()

    if proc.returncode != 0:
        err = stderr_bytes.decode("utf-8", errors="replace")
        raise SystemExit(f"FFmpeg video encoding failed:\n{err}")

    # Phase 4: Mux original AAC audio from 10942.mp4 into final MP4
    print("Muxing original audio stream into final MP4...", flush=True)
    mux_cmd = [
        "ffmpeg",
        "-y",
        "-i", str(temp_raw_mp4),
        "-i", str(args.video),
        "-map", "0:v:0",
        "-map", "1:a:0?",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        str(args.out_mp4),
    ]
    res_mux = subprocess.run(mux_cmd, capture_output=True, text=True)
    if res_mux.returncode != 0:
        print("Audio muxing fallback to video-only if no audio stream:\n" + res_mux.stderr)
        shutil.copy2(temp_raw_mp4, args.out_mp4)
    else:
        if temp_raw_mp4.exists():
            temp_raw_mp4.unlink()

    print(f"\n=======================================================")
    print(f"Replay video successfully created at:\n{args.out_mp4}")
    print(f"Size: {args.out_mp4.stat().st_size / (1024*1024):.2f} MB")
    print(f"=======================================================\n", flush=True)

    # Launch video player in Windows
    print("Launching video player...", flush=True)
    subprocess.run(["powershell", "-Command", f"Start-Process '{args.out_mp4}'"], check=False)
    print("Video player launched.", flush=True)


if __name__ == "__main__":
    main()
