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

Python only runs EfficientDet Lite0 and the Hand Landmarker. Gesture, ray,
and lock come from `ProductionTracker` (the Kotlin classes the overlay uses).
An arm box is drawn as an arm. It has no landmarks and cannot acquire a lock.

## Gates (issue #39)

A passing local run must report `composited_screen_recording` and
`decision_source: ProductionTracker`. The script exits 1 when lock frames are
zero, lock recall is zero, phone steal on other targets is above 0.08, or at
least six targets have 0% lock recall. Phone steal of zero is not a pass.

`hand_recall_while_pointing` counts a real hand (`POINTING_CONFIRMED` or
`HAND_VISIBLE`). An arm marker is not a hand. Do not tag a new APK from this
replay. It is a composited screen recording, not glasses, heat, or on-device
proof.

## Latest local run (2026-11-20, confirmed-finger only)

Same 424-frame composited clip. The previous run manufactured a fingertip
from the forearm box. This run does not. The script exited 1: all seven
targets have 0% lock recall. 23 frames held a lock and none of those locks
sat in the labeled region. 17 of those were open-palm frames where MediaPipe
landmarks still counted as pointing.

| metric | forearm-ray run | confirmed finger | denominator |
| --- | --- | --- | --- |
| lock recall | 0.059 | 0.000 | 342 pointing frames |
| lock frames in the labeled region | 20 of 110 | 0 of 23 | locks anywhere |
| phone lock recall | 0/87 | 0/88 | phone frames |
| phone proposal / ray hit | not split | 0.818 / 0.011 | 88 phone frames |
| cylinder lock recall | 0.351 | 0.000 | 57 |
| cylinder proposal / ray hit | not split | 0.912 / 0.018 | 57 |
| geometric ray hit | 0.481 | 0.085 | 342 |
| confirmed pointing | not split | 52 | 342 ground-truth pointing frames |
| open-palm false lock | 0.301 of 83 | 0.207 of 82 | all 17 still `POINTING_CONFIRMED` |
| phone steal, other targets | 0.000 | 0.000 | not a pass |

Confirmed pointing is 52 frames. On those frames the ray sometimes enters the
labeled region (phone 1/7, cylinder 1/1, barrel 6/9) and the lock still does
not land in that region. Holster has a box in the region on 2 of 32 frames.
Lite2 was not retried.

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
