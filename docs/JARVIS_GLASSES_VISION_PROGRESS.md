# Jarvis Glasses Vision — Progress Ledger

**Updated:** 2026-11-13
**Issue:** https://github.com/mattumali579/cloud-ai-os/issues/25
**Parent:** https://github.com/mattumali579/cloud-ai-os/issues/22
**Branch scope:** GitHub `cloud-ai-os` only. The dirty Windows checkout at `C:\Users\Admin\cloud-ai-os` was not opened, reset, or overwritten.

Statuses below are separate on purpose. `CODE IMPLEMENTED` means the module exists. `TESTED` means `pytest` exercised it here. `HARDWARE VERIFIED` means a person used the Ray-Ban glasses. `BLOCKED` is the next physical or decode step.

## Camera API finding (Visor 3.0.4)

Visor **does** expose authorized frames beyond its own Live View screen. This is from the tags that match the installed app, not from a later unreleased kit.

| Source | What it gives external code | Pixels on this PC |
|---|---|---|
| VDK `0.1.1` (pinned by Visor `v3.0.4` `gradle/libs.versions.toml`) `camera.photo` | Capture metadata and a `content://` gallery URI | No. The URI stays on the phone. |
| `camera.live.photo` | `mime` plus Base64 `data` over adb or the network door | Only if the bytes are PPM, or JPEG/PNG with Pillow. Glasses stills are often HEIC, and no HEIC decoder is bundled. Fails with `busy` unless Live is already running. |
| `camera.live` feed | Encoded `video/hevc` or `video/avc`, optional AAC. Rates 2, 7, 15, 24, 30. | No. There is no decoder in this pipeline. A reader that stalls 5 seconds is dropped. `reason vpn` means a phone VPN stole the Wi-Fi Direct link. |
| Developer mode `LIVE_FRAMES` (`FrameService.kt` at tag `v3.0.4`) | JPEG to an Android binder, `interval_ms` at least 200, only while Live is running | No. Wrong token or mode off returns message 4 (`TOKEN` or `OFF`). |
| `TAKE_PHOTO` / `NEW_MEDIA` | Broadcast `uri` and `path` after import | No, until a phone-side copy is made on purpose. |

The capture light stays on for photos and for Live. Nothing here starts a hidden or permanent camera.

`python -m cloudos.glasses.vision capability` prints the same audit.

## Feature ledger

| Feature | Code | Automated test | Hardware | Blocker |
|---|---|---|---|---|
| Authorized capture adapter | IMPLEMENTED | TESTED with a scripted VDK client, a PPM file, and a refusal for `content://` and HEIC | NOT VERIFIED | Phone must run Visor 3.0.4 with Developer mode or a network-door client that holds the `camera` scope, then one real `camera.live.photo` or gallery file must be passed in. |
| Object labels | IMPLEMENTED (`coarse-region-v1`, offline, no download) | TESTED on synthetic red/blue regions | NOT VERIFIED | This labels color regions. It does not identify a product model. A heavier local model can be added later; none is downloaded here. |
| Short object tracking | IMPLEMENTED for decoded frames, 2 fps, 20 s, cancel, drop, heat, battery, timeout | TESTED on prerecorded frames | NOT VERIFIED | `camera.live` decode is BLOCKED (HEVC/AVC). Tracking a real object needs stills or an on-phone JPEG path. |
| Face detection | IMPLEMENTED as "no boxes" when no detector is installed | TESTED: default path refuses to guess | NOT VERIFIED | No OpenCV/MediaPipe cascade is bundled. Skin-color guessing was not used. |
| Opt-in face template | IMPLEMENTED, local JSON embeddings, consent flag required, delete removes the person | TESTED unknown vs enrolled, no-consent, restart, no raw image files | NOT VERIFIED | Template match is not a biometric system. Speech says "resembles" and "not certain". |
| Spoken result / Visor voice | IMPLEMENTED as `voice.reply` parameters with `listen: true` | TESTED the parameter object | NOT VERIFIED | This process cannot open the phone WebSocket. The existing Jarvis bridge should send `voice.reply` and must not paste the phrase into search. |
| Jarvis desktop commands | UNCHANGED on GitHub for the local bridge (that code is not in this clone) | TESTED that vision routing returns unhandled for Chrome, Notepad, search, "click that", and "Stop Jarvis" | NOT VERIFIED | Reconcile with the dirty Windows tree before install. Do not replace `src/cloudos/glasses/` bridge files that exist only there. |
| Live View vs Tailscale | NO CHANGE to the split tunnel | NOT TESTED against a phone | NOT VERIFIED | VDK documents `camera.live` failure `vpn`. Leave Visor and Meta AI excluded from the Android Tailscale tunnel. Jarvis reachability over the private bridge is a separate check and was not run. |

## How to run

```text
python -m pytest tests/test_glasses_vision.py -q
python -m cloudos.glasses.vision capability
```

Observed on this cloud agent, 2026-11-13:

```text
/opt/cloudos-venv/bin/python -m pytest tests/test_glasses_vision.py tests/test_api.py -q
66 passed, 1 warning in 0.68s
```

`tests/test_api.py` is the existing phone and agent API suite, included so the new route and the vision hold in `execute_phone_task` do not change desktop-task responses.

Python 3.11, pytest, and FastAPI are enough. Optional and not fetched here: `visor-kit` (Apache-2.0) for the network door, Pillow for JPEG/PNG, plus a local HEIC decoder and a local HEVC decoder. No paid API key is read.

Authenticated route: `POST /v1/glasses/vision` with `Authorization: Bearer $AGENT_API_TOKEN`. Optional `GLASSES_VISION_DEVICE_TOKEN` must match `device_token` when set. Frames are stored only when `GLASSES_VISION_RETAIN_FRAMES=1` and the request also asks. Default store is `~/.local/share/cloud-ai-os/glasses-vision` (or `GLASSES_VISION_STORE`). Face templates and frames are gitignored.

## Hook for the existing bridge

Before web search, the local Jarvis router can call:

```python
from cloudos.glasses.vision.service import VisionService, is_vision_utterance

if is_vision_utterance(text):
    result = VisionService(store).handle(text, frame, request_id=request_id)
    # result["search"] is false. Speak result["speech"] with result["voice_reply"].
```

`execute_phone_task` now returns that hold message for vision phrases instead of calling Claude or Codex.

## Hardware proof still open

- [ ] Phone receives an actual user-authorized glasses/Visor image.
- [ ] A label from that image is spoken on the glasses.
- [ ] A bounded multi-frame session tracks a real object and stops.
- [ ] Enrollment, unknown, and deletion are checked on device.
- [ ] Live View still works and the private Jarvis bridge still answers with the current split tunnel.
- [ ] Existing glasses voice commands still run after the local tree is reconciled.

Smallest next action on the Windows machine: do not git-reset the dirty tree. Copy this package in beside the local `src/cloudos/glasses/` bridge, install nothing paid, and run one Developer-mode or VDK `camera.live.photo` while wearing the glasses.
