# 10917 replay (issue #39)

One-command local replay of the owner’s composited Live View recording.

Footage stays under `test-recordings/` (gitignored). Do not publish the MP4.

```powershell
cd experiments/visor-tracker-android
python scripts/replay/run_replay.py
```

Uses the production EfficientDet Lite0 and Hand Landmarker models from
`app/build/generated/visionAssets`. Writes:

- `test-recordings/10917/replay/10917_replay.mp4`
- `test-recordings/10917/replay/frames.jsonl`
- `test-recordings/10917/replay/metrics.json`

This is a screen recording with the shipped HUD baked in. The harness masks
that HUD before inference and does not claim raw camera input.

## Gates (issue #39)

A passing local run must report `composited_screen_recording` and:

- `hand_recall_while_pointing` ≥ 0.70
- `phone_steal_rate` ≤ 0.25
- `person_fp_rate` ≤ 0.35

Synthetic Kotlin boxes are not a ship gate. Do not tag a new APK until this
command has been run on the owner’s `10917.mp4`.

## Latest local run (2026-11-20, zero-lock follow-up)

Same 424-frame composited clip. A forearm guess may lock again, but only the
box its ray keeps entering, and it aims across the middle of the frame instead
of up at the chair. A run with zero locks fails even if phone steal is zero.

| metric | zero-lock run | this run | denominator |
| --- | --- | --- | --- |
| lock recall | 0.000 | 0.059 | 341 pointing frames |
| lock precision | n/a (0 locks) | 0.235 | frames with a lock while pointing |
| phone steal, other targets | 0.000 | 0.000 | non-phone pointing frames |
| phone lock recall | 0.000 | 0.000 | 87 phone frames |
| cylinder lock recall | 0.000 | 0.351 | 57 tune frames |
| geometric ray hit | 0.405 | 0.481 | 341 |
| no-target false lock | low | 0.301 | 83 open-palm frames, mostly MediaPipe |
| holdout chair/optics lock recall | 0 | 0 | 104, not used to tune |

## Previous run (2026-11-20, issue #39 follow-up)

Footage: owner `10917.mp4` (composited Live View, HUD v0.36.0 #37). 424 frames, 341 pointing.
Annotated output stays gitignored: `test-recordings/10917/replay/10917_replay.mp4`.
Chair and optics segments are holdout. Lite2 was sampled and not made the default:
it returned fewer boxes and the same wrong COCO names.

| metric | before | after | denominator |
| --- | --- | --- | --- |
| any-hand recall while pointing | 0.997 | 0.997 | 341 pointing frames |
| MediaPipe rate while pointing | (not split) | 0.188 | 341 |
| arm-box rate while pointing | (not split) | 0.625 | 341 |
| phone steal rate | 0.056 | 0.000 | 341 pointing frames |
| ray present while pointing | 0.912 | 0.912 | 341 |
| geometric ray hit (line enters the labeled region) | not measured | 0.405 | 341 aimed frames |
| geometric ray hit, tune / holdout | | 0.354 / 0.519 | 237 / 104 |
| geometric lock hit | 0.088 name-in-region | 0.000 | 341 |
| person false-positive | 0.005 | 0.005 | 424 frames |

The 0.997 hand recall counts arm-box skeletons. It is not fingertip accuracy.
A forearm guess draws a ray and does not acquire a lock. Touching a small
object selects that object. Semantic names are not rewritten (`book` is not
scored as `phone`). No APK was tagged.
