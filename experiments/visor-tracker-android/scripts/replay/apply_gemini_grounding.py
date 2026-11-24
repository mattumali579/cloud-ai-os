#!/usr/bin/env python3
"""Add strict Gemini-grounded proposals to replay detections.

The trigger is derived only from production decisions: a sustained stable point
with no hit or lock. Gemini supplies normalized boxes; this script never creates
boxes from labels, timestamps, or the human reference list. Run once with
--dry-run to audit triggers without uploading frames.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path

import cv2


VIEWPORT_TOP = 438
VIEWPORT_BOTTOM = 1878
CAM_W = 1080
CAM_H = 1440
STABLE_FRAMES = 6
REQUEST_INTERVAL_FRAMES = 45
BOX_TTL_FRAMES = 36
GEMINI_MODEL = os.environ.get("VISOR_GEMINI_MODEL", "gemini-3.8-flash").strip()


def stable_triggers(decisions: list[dict]) -> list[int]:
    triggers: list[int] = []
    stable = 0
    last_dir: tuple[float, float] | None = None
    last_request = -10_000
    for index, decision in enumerate(decisions):
        eligible = bool(decision.get("ray")) and decision.get("hit_id") is None and decision.get("lock_id") is None
        if not eligible:
            stable = 0
            last_dir = None
            continue
        direction = (float(decision.get("dir_x", 0)), float(decision.get("dir_y", 0)))
        dot = 1.0 if last_dir is None else last_dir[0] * direction[0] + last_dir[1] * direction[1]
        stable = stable + 1 if dot >= 0.96 else 1
        last_dir = direction
        if stable >= STABLE_FRAMES and index - last_request >= REQUEST_INTERVAL_FRAMES:
            triggers.append(index)
            last_request = index
    return triggers


def strict_boxes(raw: str) -> list[dict]:
    text = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    payload = json.loads(text)
    output: list[dict] = []
    for item in payload.get("objects", []):
        label = str(item.get("label", "")).strip().lower()
        box = item.get("box_2d")
        if not label or not isinstance(box, list) or len(box) != 4:
            continue
        if not all(isinstance(value, (int, float)) and math.isfinite(value) and 0 <= value <= 1000 for value in box):
            continue
        ymin, xmin, ymax, xmax = [float(value) / 1000.0 for value in box]
        if ymax <= ymin or xmax <= xmin or (ymax - ymin) * (xmax - xmin) > 0.35:
            continue
        output.append({"label": label, "box": (xmin * CAM_W, ymin * CAM_H, xmax * CAM_W, ymax * CAM_H)})
    return output[:8]


def prompt(decision: dict) -> str:
    tip_x = max(0, min(1000, round(float(decision["tip_x"]) / CAM_W * 1000)))
    tip_y = max(0, min(1000, round(float(decision["tip_y"]) / CAM_H * 1000)))
    end_x = max(0, min(1000, round((float(decision["tip_x"]) + float(decision["dir_x"]) * CAM_W) / CAM_W * 1000)))
    end_y = max(0, min(1000, round((float(decision["tip_y"]) + float(decision["dir_y"]) * CAM_H) / CAM_H * 1000)))
    return (
        "Locate tangible household objects intersected by the directed image ray from "
        f"(x={tip_x},y={tip_y}) toward (x={end_x},y={end_y}). Return only objects visibly "
        "grounded in this image, ordered nearest along the ray. Do not infer a box from text "
        "or the requested label. Return JSON only as "
        '{"objects":[{"label":"concise visible name","box_2d":[ymin,xmin,ymax,xmax]}]}. '
        'Coordinates are integers normalized 0..1000. Return {"objects":[]} when uncertain.'
    )


def query(api_key: str, camera_bgr, decision: dict) -> list[dict]:
    ok, encoded = cv2.imencode(".jpg", camera_bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        raise RuntimeError("JPEG encoding failed")
    body = {
        "model": GEMINI_MODEL,
        "store": False,
        "stream": True,
        "input": [
            {"type": "text", "text": prompt(decision)},
            {"type": "image", "data": base64.b64encode(encoded).decode("ascii"), "mime_type": "image/jpeg"},
        ],
        "response_format": {
            "type": "text",
            "mime_type": "application/json",
            "schema": {
                "type": "object",
                "properties": {
                    "objects": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string"},
                                "box_2d": {
                                    "type": "array",
                                    "items": {"type": "integer"},
                                    "minItems": 4,
                                    "maxItems": 4,
                                },
                            },
                            "required": ["label", "box_2d"],
                        },
                    }
                },
                "required": ["objects"],
            },
        },
    }
    request = urllib.request.Request(
        "https://generativelanguage.googleapis.com/v1beta/interactions?alt=sse",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
            "Api-Revision": "2026-05-20",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            chunks: list[str] = []
            event_types: list[str] = []
            terminal_status = "unknown"
            terminal_error = ""
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                event_payload = line.removeprefix("data:").strip()
                if event_payload == "[DONE]":
                    continue
                event = json.loads(event_payload)
                event_type = str(event.get("event_type", "unknown"))
                event_types.append(event_type)
                if event_type in {"interaction.failed", "interaction.completed"}:
                    interaction = event.get("interaction", {})
                    terminal_status = str(interaction.get("status", event_type))
                    terminal_error = str(interaction.get("error", event.get("error", "")))[:300]
                if event_type != "step.delta":
                    continue
                delta = event.get("delta", {})
                if delta.get("type") == "text":
                    chunks.append(str(delta.get("text", "")))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Gemini HTTP {error.code}: {detail}") from error
    text = "".join(chunks)
    if not text:
        summary = sorted(set(event_types))
        raise RuntimeError(
            f"Gemini stream completed without model text; status={terminal_status}; "
            f"events={summary}; error={terminal_error}"
        )
    return strict_boxes(text)


def query_with_retry(api_key: str, camera_bgr, decision: dict, frame_index: int) -> list[dict]:
    delays = [5, 15, 30]
    for attempt in range(len(delays) + 1):
        try:
            return query(api_key, camera_bgr, decision)
        except (RuntimeError, TimeoutError, socket.timeout) as error:
            retryable = (
                isinstance(error, (TimeoutError, socket.timeout))
                or "HTTP 429" in str(error)
                or "HTTP 503" in str(error)
                or "events=['error']" in str(error)
            )
            if not retryable or attempt >= len(delays):
                raise
            delay = delays[attempt]
            print(json.dumps({"frame": frame_index, "retry_in_seconds": delay, "error": str(error)[:180]}), flush=True)
            time.sleep(delay)


def split_frames(text: str) -> list[list[str]]:
    frames: list[list[str]] = []
    current: list[str] = []
    for line in text.splitlines():
        current.append(line)
        if line.strip() == "END":
            frames.append(current)
            current = []
    if current:
        raise ValueError("unterminated detection frame")
    return frames


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--detections", type=Path, required=True)
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--reuse-cache",
        action="store_true",
        help="reuse all actual cached results for this model without making network requests",
    )
    args = parser.parse_args()

    decisions = [json.loads(line) for line in args.decisions.read_text(encoding="utf-8").splitlines() if line.strip()]
    frames = split_frames(args.detections.read_text(encoding="utf-8"))
    if len(frames) != len(decisions):
        raise SystemExit(f"frame mismatch: detections={len(frames)} decisions={len(decisions)}")
    cache_path = args.cache or args.out.with_suffix(".grounding_cache.json")
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    if args.reuse_cache:
        prefix = f"{GEMINI_MODEL}:"
        triggers = sorted(
            int(key.removeprefix(prefix)) for key in cache
            if key.startswith(prefix) and key.removeprefix(prefix).isdigit()
        )
    else:
        triggers = stable_triggers(decisions)
    print(json.dumps({"model": GEMINI_MODEL, "trigger_count": len(triggers), "trigger_frames": triggers}))
    if args.dry_run:
        return
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not args.reuse_cache and not api_key:
        raise SystemExit("GEMINI_API_KEY is required")

    cap = cv2.VideoCapture(str(args.video))
    if not cap.isOpened():
        raise SystemExit(f"cannot open {args.video}")
    proposals: list[tuple[int, int, dict, list[dict]]] = []
    for frame_index in triggers:
        cache_key = f"{GEMINI_MODEL}:{frame_index}"
        if cache_key in cache:
            boxes = cache[cache_key]
            proposals.append((frame_index, min(len(frames), frame_index + BOX_TTL_FRAMES), decisions[frame_index], boxes))
            print(json.dumps({"frame": frame_index, "cached": True, "objects": [box["label"] for box in boxes]}), flush=True)
            continue
        if args.reuse_cache:
            continue
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
        ok, screen = cap.read()
        if not ok:
            continue
        camera = screen[VIEWPORT_TOP:VIEWPORT_BOTTOM, :CAM_W]
        boxes = query_with_retry(api_key, camera, decisions[frame_index], frame_index)
        proposals.append((frame_index, min(len(frames), frame_index + BOX_TTL_FRAMES), decisions[frame_index], boxes))
        cache[cache_key] = boxes
        cache_path.write_text(json.dumps(cache, indent=2), encoding="utf-8")
        print(json.dumps({"frame": frame_index, "objects": [box["label"] for box in boxes]}), flush=True)
    cap.release()

    additions: dict[int, list[str]] = {}
    frame_diagonal = math.hypot(CAM_W, CAM_H)
    for start, end, request_decision, boxes in proposals:
        for frame_index in range(start, end):
            current = decisions[frame_index]
            if not current.get("ray"):
                break
            direction_dot = (
                float(request_decision["dir_x"]) * float(current["dir_x"]) +
                float(request_decision["dir_y"]) * float(current["dir_y"])
            )
            tip_shift = math.hypot(
                float(request_decision["tip_x"]) - float(current["tip_x"]),
                float(request_decision["tip_y"]) - float(current["tip_y"]),
            )
            if direction_dot < 0.96 or tip_shift > frame_diagonal * 0.06:
                break
            for box in boxes:
                left, top, right, bottom = box["box"]
                label = box["label"].replace(" ", "\\u0020").replace('"', '\\"')
                additions.setdefault(frame_index, []).extend([
                    f'BOX object "{label}" 0.0000 {left:.2f} {top:.2f} {right:.2f} {bottom:.2f}',
                    "GROUNDED",
                ])
    output: list[str] = []
    for index, lines in enumerate(frames):
        output.extend(lines[:-1])
        output.extend(additions.get(index, []))
        output.append("END")
    args.out.write_text("\n".join(output) + "\n", encoding="utf-8")
    print(args.out)


if __name__ == "__main__":
    main()
