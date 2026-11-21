# VISOR 10942 — human visual pointing reference for AGY

**Status: provisional screenshot annotations, not verified ground truth.** Source `10942.mp4`, 42.284 seconds. Private image references and footage stay in user's local `test-recordings/10942/` (not GitHub). This file supplements [docs/VISOR_END_TO_END.md](VISOR_END_TO_END.md) and issue #40.

User requested an actual screenshot **for every item** pointed to so Antigravity can compare its overlay with the original room footage. The ChatGPT conversation produced a 16-image local ZIP: `Visor_10942_Pointing_Target_Screenshots.zip`, and `storyboard_1.jpg` / `storyboard_2.jpg`. These are **not currently present in the Windows checkout**; ask only for the private ZIP or locate it in Downloads, then copy to ignored `test-recordings/10942/reference/`. Do NOT invent or upload these files to public GitHub.

The screenshots use a **GREEN box** to mark the *visual candidate object* and a **YELLOW circle** to mark the approximate index fingertip. They are visual examples, **not** production tracker outputs, measured 3D point-of-aim, or proof of user intent. Never train/test on unconfirmed labels as ground truth; get user confirmation for ambiguous ones. Do not just overlay these boxes onto every frame — infer them from actual sensor/video imagery.

| Video time (s) | Provisional object under pointing | Status |
|---:|---|---|
| 0.8 | Laundry basket | CHECK: finger/box overlap |
| 3.0 | Open closet / clothing | CHECK: may mean shoes or garment |
| 5.0 | Portable air conditioner | CHECK: parallax |
| 7.0 | Portable air conditioner | LIKELY |
| 9.5 | Window sill with containers | LIKELY, object-level granularity unknown |
| 12.0 | Conical hat | LIKELY |
| 15.5 | Laptop | LIKELY |
| 18.0 | Paper roll on table | CHECK: nearby objects |
| 21.0 | NO TARGET, hand curled | REJECT |
| 24.0 | White plastic-covered item | CHECK: occlusion |
| 26.5 | Whiteboard | LIKELY |
| 28.0 | Black backpack on chair | CHECK: chair vs bag |
| 30.5 | Gray garment draped on chair | LIKELY |
| 33.0 | Chair lower frame | CHECK |
| 36.0 | Books on shelf | LIKELY |
| 38.5 | NO CERTAIN TARGET (between shelf and whiteboard) | REJECT unless validated otherwise |

**Execute in local Antigravity project:** decode full video at correct timestamps, compare actual MediaPipe landmark 8 + forward ray + candidates to these private visual reference screenshots, correct hand pose/identity/camera transform/3D limitations, generate a playable `10942_overlay_replay.mp4`, and place before/after frames side by side. Never force a lock from an uncertain screenshot. Run actual production Android/Kotlin tracker, not manually copied box animations. Report false positives, misses, and calibrated or uncertain point-of-aim. Real device gate remains separate.

**Do not claim:** that the user's intended object is verified from this camera video alone, that these screenshot boxes are proof of a working 3D ray, that this text starts an Antigravity process, or that the full physical glasses gate passed.
