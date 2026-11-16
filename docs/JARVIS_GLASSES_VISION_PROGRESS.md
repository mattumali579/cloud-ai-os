# Jarvis Glasses Vision — Progress Ledger

**Updated:** 2026-11-16
**Issue:** https://github.com/mattumali579/cloud-ai-os/issues/25
**Parent:** https://github.com/mattumali579/cloud-ai-os/issues/22
**Branch:** `cursor/glasses-vision-7cc5` (PR #26, draft). The dirty Windows checkout at `C:\Users\Admin\cloud-ai-os` was not opened, reset, or overwritten.

Four labels are separate on purpose.

| Label | Meaning |
|---|---|
| CODED | The module is in this branch. |
| SIMULATED TESTED | `pytest` ran it on synthetic frames, scripted landmarks, or a synthetic tensor. No glasses. |
| PC TESTED | The owner's Windows host ran it on a decoded frame. |
| PHYSICAL GLASSES VERIFIED | Glasses → phone → this Python process → saved event → spoken result on the glasses. |

`hardware_verified` in code stays false. This process never sets it.

## Cloud archive (2026-11-16)

The private Drive folder named Jarvis Vision Archive exists and its permission list has a single owner grant. The folder id is not recorded here.

| Hop | Result |
|---|---|
| Windows Drive for desktop or an existing rclone remote | Not inspected. This process is not that PC, and rclone is not on this machine. |
| Encrypted session bundle, local spool, copy, retry, checksum, index | CODED and SIMULATED TESTED. Local suite: 97 passed, 1 warning in 0.72s, including the archive tests. Scripted transport only. No OpenAI call. |
| Authenticated upload to Drive | NOT RUN. Needs the owner's browser OAuth and a crypt remote. |
| Physical glasses to archive to speech | NOT VERIFIED. |

Events are on by default. Frames are off unless a session opts in. The drain job is `glasses.archive.drain` on the existing host worker. Setup, restore, the 64 MiB spool, and the lost-passphrase warning are in `docs/JARVIS_GLASSES_STORAGE.md`. No new storage vendor and no OpenAI call.

## Completion report — target selection (2026-11-16)

Intended build, written before the code: voice disambiguation as the default, a phone/PC numbered preview with no lens HUD, a debounced 2D pointing pinch that does not claim gaze, and a lock that follows one session track id until it is lost or cancelled. Live View, the Tailscale split tunnel, and Live AI stay untouched. "Control" means the log, not a vehicle.

### A. Built

- `SelectionSession` scans detector boxes, assigns session track ids, thirds (left/center/right), and a hue hint.
- Ambiguous matches ask for a number and do not auto-select.
- After a lock, a missing track stops the session and does not hop to another box of the same class.
- `selection-log.jsonl` stores kind, track id, label, time, bbox trajectory, selection method, confidence. No landmark coordinates and no image bytes.
- Preview PPM draws a yellow box and a number. `lens_hud` is false. Surface is `phone_or_pc`.
- Pointing uses the wrist-to-index-tip ray in image pixels. Pinch must repeat on the next accepted frame. One pinch does not lock. An open palm clears the streak.
- `control the truck` answers that only software tracking and logging exist.

### B. Commands and gestures

| Utterance or gesture | What it does |
|---|---|
| `scan objects` | Lists numbered candidates. Does not lock. |
| `track the truck on the left` | Locks the one match. Two on that side: asks, does not lock. |
| `select object 3` | Locks the number from the last scan. |
| `switch to the other truck` | Switches only when exactly one other match exists. |
| `what am I tracking?` | Reports the locked id, or says nothing is tracked. |
| `stop tracking` | Stops the color tracker and the lock. Speech stays "Stopped tracking." |
| `log this object` | Appends the trajectory for the locked id. |
| `point at an object` | 2D ray. Pinch on two accepted frames confirms. |
| open palm between pinches | Clears the pinch streak. |
| `control the truck` / `control object` | Software-only refusal. No hardware interface. |
| pinch, open palm, fist on `watch my hand` | Earlier gesture path. Unchanged. |

### C. Tests

Local, Python 3.12.3:

```text
python -m pytest tests/test_glasses_vision.py tests/test_glasses_gesture.py tests/test_glasses_selection.py tests/test_api.py -q
87 passed, 1 warning in 0.75s
```

GitHub Actions on the selection commit `d4db70d`: https://github.com/mattumali579/cloud-ai-os/actions/runs/43845683289

```text
87 passed, 1 warning in 0.95s
```

Python 3.11.18. `OPENAI_API_KEY` and the other model keys were empty. JUnit artifact: https://github.com/mattumali579/cloud-ai-os/actions/runs/43845683289/artifacts/14215720176

The 66-pass run (https://github.com/mattumali579/cloud-ai-os/actions/runs/43842665105) is the pre-selection baseline. The 78-pass run (https://github.com/mattumali579/cloud-ai-os/actions/runs/43844641209) covers gestures and a generated HEIC, not this crowd-selection suite. There is no screenshot of glasses. The preview check is a PPM pixel assertion in `tests/test_glasses_selection.py`.

### D. Status

| Check | Result |
|---|---|
| CODED | Selection, preview, voice, debounced pinch, log. |
| SIMULATED TESTED | 9 new tests on scripted trucks and sample hands. |
| PC TESTED | No. This cloud VM is not the Windows host. |
| PHYSICAL GLASSES VERIFIED | No. |
| Live View | Owner says it already works. This change does not touch the tunnel and did not retest it. |
| Live AI | Not used. Not a dependency. |
| Concurrent camera and microphone | Not verified. |

### E. Not built

- A real MediaPipe hand landmarker run (package and task file still absent).
- A real YOLO weight inference (no `.onnx` / `.pt` on disk; AGPL weights not downloaded).
- Gaze, depth, or a calibrated camera matrix. The ray is image-plane only (`intrinsics_calibrated` false).
- Any drawing inside the Ray-Ban lenses.
- Any interface that moves a physical object.
- A phone-screen app. The preview is a PPM plus JSON on `POST /v1/glasses/vision`.
- Proof that Visor voice and Live View can run at the same time.

### F. Next physical test

On the Windows host, do not git-reset the dirty tree and do not change the Tailscale split tunnel. Wear the glasses with Live View already up. Send one authorized `camera.live.photo` (Base64) or a JPEG copied on purpose. Say `scan objects`, then `select object` and a number, and confirm the phone or PC preview shows numbers while the lenses do not. Then say `log this object` and `stop tracking`. Do not ask the normal Meta or ChatGPT assistant. If the still is a HEVC-tile HEIC and pillow-heif fails, decode it on the phone with ImageDecoder and pass a JPEG. Blocker: no glasses frame has reached this process.

### G. Paid models and risks

OpenAI, ChatGPT, Anthropic, and Gemini calls during these tests: **zero**. The phone route holds `scan objects` before Claude or Codex. Risks: a scripted truck is not a detector; two similar objects only disambiguate when the detector's label and the third of the frame differ; a single missed frame can end the lock when `lost_after_misses` is 1; the preview is not a UI the phone is serving yet.

## CI

The workflow the owner committed (`.github/workflows/glasses-vision-regression.yml`, commit `467b92a2856f1bf60a8825590b7f18295e7f54fc`) did execute.

| Run | Event | Result | Evidence |
|---|---|---|---|
| https://github.com/mattumali579/cloud-ai-os/actions/runs/43844641209 | `pull_request` on `3622f20` | success | Job log: `78 passed, 1 warning in 0.80s` on Python 3.11.18. Installed `pillow-heif` 1.8.1. `OPENAI_API_KEY` and the other model keys were empty. |
| https://github.com/mattumali579/cloud-ai-os/actions/runs/43844629043 | `push` of the same commit | success | Same workflow file. |
| https://github.com/mattumali579/cloud-ai-os/actions/runs/43842665105 | `pull_request` on `467b92a` | success | Earlier color-region suite only: `66 passed, 1 warning in 0.80s`. |

No OpenAI, Anthropic, or Gemini key is used. The daily cron runs only on the default branch after merge. The 78-test run includes the gesture and synthetic HEIC tests. It does not install MediaPipe or a YOLO weight file.

Local result on this cloud agent, Python 3.12.3, same count as the GitHub run above:

```text
/opt/cloudos-venv/bin/python -m pytest tests/test_glasses_vision.py tests/test_glasses_gesture.py tests/test_api.py -q
78 passed, 1 warning in 0.64s
```

32 of those are the glasses suites. 46 are the existing API suite. No network call and no paid model.

## What is not object recognition

`CoarseRegionDetector` (`coarse-region-v1`) still groups saturated pixels by hue and says "red object" or "blue object". That is a color region. It is not MediaPipe, not YOLO, and not a product id. Describe-speech for that detector still says it is a coarse color match.

## Camera API finding (Visor 3.0.4)

| Source | What it gives external code | Pixels on this PC |
|---|---|---|
| VDK `0.1.1` `camera.photo` | Capture metadata and a `content://` gallery URI | No. The URI stays on the phone. Reading it would be a new transfer, and this pipeline still refuses it. |
| `camera.live.photo` | `mime` plus Base64 `data` over adb or the network door | A normal HEIC decodes when `pillow-heif` is installed. A glasses grid of HEVC tiles is not proven. |
| `camera.live` | Encoded `video/hevc` or `video/avc` | No on this PC. On the phone, `LiveVideoDecoder` turns the same feed into a YUV `Image`. This process cannot call that Android decoder. `reason vpn` still means a phone VPN stole Wi-Fi Direct. |
| Developer mode `LIVE_FRAMES` | JPEG on an Android binder while Live is running | No, until an on-phone subscriber copies bytes out on purpose. |
| `TAKE_PHOTO` / `NEW_MEDIA` | Gallery `uri` and `path` | No, until a deliberate copy. |

`python -m cloudos.glasses.vision capability` prints the same audit. The capture light stays on for photos and for Live.

## Feature ledger

| Feature | CODED | SIMULATED TESTED | PC TESTED | PHYSICAL GLASSES VERIFIED | Notes |
|---|---|---|---|---|---|
| Authorized capture adapters | yes | yes: scripted VDK client, PPM, refusal of `content://` and of invalid HEIC | no | no | |
| Synthetic HEIC still | yes, optional `pillow-heif`  (BSD-3-Clause; bundles libheif) | yes: an 8×6 generated HEIC round-tripped to RGB `(220, 30, 30)` and through `camera.live.photo` Base64 | no | no | This was not a glasses file. |
| Coarse color regions | yes | yes, red/blue blocks | no | no | Not objects. |
| 21-point gestures (pinch, open palm, fist) | yes, geometry on MediaPipe indices | yes, sample poses | no | no | Not the MediaPipe gesture recognizer. |
| MediaPipe hand landmarker | yes, optional adapter | the missing-file path only | no | no | Apache-2.0. Needs a local `hand_landmarker.task`. This branch does not download it. `mediapipe` was not installed here. |
| YOLO categories | yes: original YOLOv8 head decode, optional ONNX (MIT) or Ultralytics `.pt` (AGPL-3.0) | yes, one synthetic head: cup kept, overlapping cup suppressed, bottle kept | no | no | No weights file. Nothing was downloaded. A class name is not an identity. |
| Tracking id vs durable id | yes | yes: `trk-0001` follows IoU; `named-blue-mug` is stored only with consent and is not copied onto a new cup track | no | no | Ledger sets `auto_match` false and stores no image. |
| Gesture chain | yes | yes: frame → 21 landmarks → pinch selects cup (not the other box) → open palm clears → fist confirms → timestamped event → `voice.reply` | no | no | Evidence label on that run is `simulated`. |
| Bounds | yes | yes: 2 fps drop, heat, battery, cancel | no | no | Same 20 s / 2 fps caps as the color tracker. |
| Event log privacy | yes | yes: disk log only when retention is opted in, and the file has counts, not landmark coordinates | no | no | |
| `voice.reply` without an assistant | yes | yes: method is `voice.reply`; `assistant_dispatched` and `paid_inference` are false; "watch my hand" never reaches Claude or Codex | no | no | This process does not open the phone socket and does not issue a glasses assistant prompt. |
| Face enrollment | yes | yes, from the earlier suite | no | no | Still "resembles", never certain. |
| Live View / Tailscale | no code change | no | no | no | Leave Visor and Meta AI excluded from the Android tunnel. |

## Failure log

1. **Phone `content://` still does not reach this PC.** Evidence: `VisorStillAdapter` raises `CaptureError` code `blocked` for `content://media/external/images/media/7`. Visor 3.0.4 docs: `camera.photo` returns a gallery URI; over adb or the network door, `camera.live.photo` puts the bytes in Base64 `data` instead. Next action: one authorized `camera.live.photo` on the phone, with the Base64 or a file copy the owner makes on purpose. Do not add a new transfer service.

2. **`camera.live` is still encoded video on this PC.** Evidence: `LiveFeedAdapter.open_session` raises `blocked` and the message names HEVC. The VDK's `LiveVideoDecoder` is an Android `Image` callback, not a Python package. Next action: decode on the phone with that existing decoder (or Developer-mode `LIVE_FRAMES` JPEG) and pass stills. A second PC HEVC backend was not added.

3. **A real Ray-Ban HEIC grid was not decoded.** Evidence: pillow-heif 1.8.1 decoded a one-image HEIC this process generated (`ftypheic`, 8×6). Visor documents live photos as HEIC, and the glasses grid (many HEVC tiles) was not in hand. Invalid HEIC bytes still fail and the error contains `HEIC`. Next action: decode one real `camera.live.photo` file with pillow-heif. If libheif rejects the grid, use the phone `ImageDecoder` and copy a JPEG.

4. **MediaPipe did not run.** Evidence: `MediaPipeHandLandmarker` on a missing `.task` file raises `HandLandmarkerUnavailable` and does not download. `hand_model_status` reports `mediapipe_importable` from the environment; the package was not installed in this run. Next action: the owner places the Apache-2.0 `hand_landmarker.task` locally and installs `mediapipe`. Until then the service says it did not invent a gesture.

5. **YOLO did not run on a weight file.** Evidence: `YoloObjectDetector` on a missing `.onnx` raises `YoloUnavailable`. Ultralytics pretrained weights are AGPL-3.0 and are not vendored. The tested path is `decode_yolov8` on a hand-built head. Next action: if the owner accepts that license, point the detector at a local `.onnx` (onnxruntime, MIT) or `.pt`. Do not auto-download.

6. **No physical loop.** No glasses, phone, Live View, or Tailscale session was available. The Windows tree was not modified. Next action on that machine: do not git-reset the dirty tree. Copy this package beside the local bridge, keep the Tailscale split tunnel, and run one Developer-mode or VDK still while wearing the glasses. Do not send a normal assistant prompt to test it. Speak with `voice.reply`.

## How to run

```text
python -m pip install -e ".[dev,glasses]"
python -m pytest tests/test_glasses_vision.py tests/test_glasses_gesture.py tests/test_api.py -q
python -m cloudos.glasses.vision capability
```

`glasses` installs Pillow and pillow-heif only. It does not install MediaPipe, onnxruntime, or Ultralytics, and it does not fetch weights.

Authenticated route: `POST /v1/glasses/vision` with `Authorization: Bearer $AGENT_API_TOKEN`. Vision phrases sent to `POST /v1/phone/task`, including "watch my hand", stay off Claude and Codex. "Open Chrome" is still unhandled here so the local Jarvis route can keep it.

Optional later, still with no paid API:

```text
# Apache-2.0 task file the owner downloads once. This repo does not fetch it.
# mediapipe is Apache-2.0.
# YOLO .onnx via onnxruntime (MIT), or .pt via ultralytics (AGPL-3.0).
```
