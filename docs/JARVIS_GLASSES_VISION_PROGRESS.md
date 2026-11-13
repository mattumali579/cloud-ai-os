# Jarvis Glasses Vision — Progress Ledger (Issue #25)

**Updated:** 2026-11-13 23:50Z (local 2026-11-13 ~17:50 America/Chicago)  
**Parent:** #22 / `docs/JARVIS_GLASSES_PROGRESS.md`  
**Branch / worktree:** `cursor/issue-25-glasses-vision` @ `C:\Users\Admin\cloud-ai-os-issue25-wt`  
**Dirty checkout preserved:** `C:\Users\Admin\cloud-ai-os` (master behind origin/master by 463; not pulled/reset)  
**Cloud agent:** `bc-bfe59af4-560c-488c-8c0e-e861fbff7cc5` — no branch/PR found; treated as untrusted / incomplete

## Commands / results

```text
# Preservation manifest
C:\Users\Admin\cloud-ai-os\_preservation\issue25_2026-11-13_17-33-46\PRESERVATION_MANIFEST.md

# Worktree
git worktree add -b cursor/issue-25-glasses-vision C:\Users\Admin\cloud-ai-os-issue25-wt origin/master

# Vision unit tests (worktree src)
$env:PYTHONPATH = "C:\Users\Admin\cloud-ai-os-issue25-wt\src;C:\Users\Admin\cloud-ai-os-issue25-wt"
C:\Users\Admin\cloud-ai-os\.venv\Scripts\python.exe -m pytest tests/test_jarvis_glasses_vision.py -q --tb=short
→ 16 passed

# Vision + #22 regression (dirty checkout after additive sync; bridge NOT restarted)
$env:PYTHONPATH = "C:\Users\Admin\cloud-ai-os\src;C:\Users\Admin\cloud-ai-os"
C:\Users\Admin\cloud-ai-os\.venv\Scripts\python.exe -m pytest tests/test_jarvis_glasses_vision.py tests/test_jarvis_glasses.py tests/test_jarvis_glasses_audit.py -q --tb=line
→ 94 passed
```

Runtime note: `CloudAIOS-JarvisGlasses` left **Running** (unchanged). Recent log shows Visor WebSocket handshake timeouts — read-only; no restart/stop performed.

---

## Feature matrix

| Feature | CODE IMPLEMENTED | TESTED | HARDWARE VERIFIED | BLOCKED |
|---|---|---|---|---|
| Modular `authorized_capture → detector → tracker → speech` | YES (`src/cloudos/glasses/vision/`) | YES (16 vision tests) | NO | — |
| Fixture / webcam simulation adapters | YES | YES | N/A (explicitly non-hardware) | — |
| Visor `camera.photo` / `camera.live.photo` adapter boundary | YES (`VisorCameraCaptureAdapter`) | YES (scope-denied unit) | NO | Live probe: Visor WS handshake timeout on bridge; cannot confirm token scopes without a healthy Visor session. Official VDK docs list methods under scope `camera`. |
| Object class label + confidence (≠ product identity) | YES (`BlobClassDetector`) | YES | NO | Real glasses frame not captured this session |
| Bounded tracker + cancel + backpressure + low FPS | YES (`BoundedTracker`, max 2 FPS default) | YES | NO | Needs multi-frame `camera.live` feed + worn device; continuous Live View must not be default |
| Thermal / low-battery stop + visible state | YES (`SafetyGate`, `visible_state`) | YES | NO | Glasses temperature field not confirmed in `glasses.status` schema on device; battery gate ready; stop-on-`hot` flag ready |
| Face recognition OFF by default, consent enroll, encrypted local store, unknown default, removal | YES (`FaceRegistry` + Fernet) | YES | NO | Enable only via `JARVIS_FACE_RECOGNITION=1`; no worn-device enroll |
| Voice phrases routed (not Google search) | YES (`classify_command` + bridge prepare) | YES | NO | Spoken TTS on glasses not observed this session |
| #22 desktop/voice regression | preserved | YES (78 prior + 16 vision = 94) | Cap2 still pending per #22 | Do not restart bridge without owner OK |
| Tailscale split-tunnel (Visor/Meta AI excluded) | not modified | not re-tested here | prior owner fix | Do not undo; Jarvis reachability not re-proven this session |

---

## Visor camera path — evidence (not claimed success)

**Documented supported APIs (Visor VDK / JSON-RPC, scope `camera`):**
- `camera.photo` → `Capture` (gallery `uri` on phone; network door may include base64 `data`)
- `camera.live` feed → HEVC/H.264 samples (Wi-Fi Direct; heats glasses — keep ≤2–7 FPS, ≤~20s, cancel on heat)
- `camera.live.photo` → base64 photo while Live runs
- `captures.list` / `captures.import`

**Installed client:** `visor_kit` in local venv (`Visor.supports`, `call`, `feed`).

**Exact software blocker for Windows bridge without `camera` scope:**  
Live View UI ≠ external frames. Without `camera.photo` / `camera.live.photo` on the program token, the adapter raises:

> BLOCKED: Visor token lacks camera.photo / camera.live.photo. Live View on the phone is not an external frame source for this Windows bridge. Grant scope camera in Visor → Apps → Let programs connect.

**Additional transport caveat:** phone `content://` URIs from `camera.photo` import are not readable from Windows; prefer `camera.live.photo` base64 or on-phone processing.

**This session:** no successful `camera.photo` call; Visor connectivity currently failing handshake in bridge log. **Not hardware verified.**

---

## Smallest user actions to unblock hardware proof

1. Ensure Visor is open/connected on the Samsung (bridge log currently: handshake timeout).
2. In Visor → Apps → Let programs connect: grant this Jarvis program the **`camera`** scope (in addition to existing voice scopes).
3. Wear glasses; say **“Jarvis, describe what I'm looking at.”** Confirm capture light + spoken class label (not product SKU).
4. Optional short track: **“Jarvis, track that object.”** then **“stop tracking.”** Stop immediately if glasses get hot.
5. Face ID remains off unless you set `JARVIS_FACE_RECOGNITION=1` and explicitly enroll a consenting person.

---

## Do not claim

- Do not call fixture/webcam tests **HARDWARE VERIFIED**.
- Do not claim permanent/hidden streaming (policy forbids; default is single-shot).
- Do not claim stranger identification or cloud face lookup (not implemented; refused by design).
- Do not restart `CloudAIOS-JarvisGlasses` / Docker / n8n / host worker without owner approval.
