# Lock / point selection

Slice only. Not a full-project or glasses verification.

## Bugs

1. **The pointed object was the box whose center was nearest the middle, even when that box did not contain the aim point.** A smaller box beside the center could beat the object the picture was actually pointing at. An overlapping neighbor with a closer center, or an equal-distance box listed first, could also take the lock after two frames while the original box still covered the point.

2. **A deliberate new lock was dropped when the detector anchor had already left the gray history.** `TrackingHandoff` treated every stale detection as “do not move the live box” while something was showing. Inference slower than the 8-frame history therefore kept publishing the old track after `AimLock` had switched. The old id stayed on screen.

## Fix

- The aim point is the center of the picture. The pointed object is the box that contains that point. If several do, the smallest is the one in front. A current lock within twice that area stays the same target. If nothing contains the point, the nearest center is used. Held rectangles and hands are not targets.
- A stale detector result still does not jump the track already on screen. If the focused track id is different, that result replaces the lock and the old id is not published again.

## Files changed

- `experiments/visor-tracker-android/app/src/main/java/com/mattumali/visortracker/AimLock.kt`
- `experiments/visor-tracker-android/app/src/main/java/com/mattumali/visortracker/NearbyCandidates.kt`
- `experiments/visor-tracker-android/app/src/main/java/com/mattumali/visortracker/RelativeDistanceEstimator.kt`
- `experiments/visor-tracker-android/app/src/main/java/com/mattumali/visortracker/TrackingHandoff.kt`
- `experiments/visor-tracker-android/app/src/test/java/com/mattumali/visortracker/AimLockTest.kt`
- `experiments/visor-tracker-android/app/src/test/java/com/mattumali/visortracker/NearbyCandidatesTest.kt`
- `experiments/visor-tracker-android/app/src/test/java/com/mattumali/visortracker/BoxHoldTest.kt`
- `experiments/visor-tracker-android/app/src/test/java/com/mattumali/visortracker/RelativeDistanceEstimatorTest.kt`
- `experiments/visor-tracker-android/app/src/test/java/com/mattumali/visortracker/LockPerformanceTest.kt`

`MotionTracker.kt`, `BoxHold.kt`, `TrackIds.kt`, `LockMetrics.kt`, and `TrackerUpdater.kt` were not changed. Their owned tests were still run.

## Tests

New coverage: several candidates, the object under the point is chosen; that lock stays while the point remains on it; a clear move onto another object replaces it and does not keep the old id; a smaller object under the point replaces a much larger lock; a stale detection of a new lock replaces the shown track.

## Command

`gradlew.bat` is not in the tree (never committed). The same task was run with Gradle 9.6.0, which is the version already in this project’s `.gradle` cache, and `ANDROID_HOME` set to `%LOCALAPPDATA%\Android\Sdk`. No `local.properties` was written. `app/build` was not deleted.

```
.\gradlew.bat :app:testDebugUnitTest --tests com.mattumali.visortracker.AimLockTest --tests com.mattumali.visortracker.NearbyCandidatesTest --tests com.mattumali.visortracker.BoxHoldTest --tests com.mattumali.visortracker.LockPerformanceTest --tests com.mattumali.visortracker.RelativeDistanceEstimatorTest --tests com.mattumali.visortracker.TrackIdsTest --tests com.mattumali.visortracker.MotionTrackerTest
```

Gradle lock: `docs/visor-checkpoints/GRADLE_LOCK` (CreateNew exclusive). Removed after exit.

## Result

**Pass.** `BUILD SUCCESSFUL`. 66 tests, 0 failures:

| Class | Tests | Failures |
| --- | ---: | ---: |
| AimLockTest | 6 | 0 |
| NearbyCandidatesTest | 10 | 0 |
| BoxHoldTest | 3 | 0 |
| LockPerformanceTest | 14 | 0 |
| RelativeDistanceEstimatorTest | 15 | 0 |
| TrackIdsTest | 8 | 0 |
| MotionTrackerTest | 10 | 0 |

## Leftover risks

- Not run on a phone, Visor, or glasses. No claim that a physical point locks the right object.
- `OverlayTrackingService` posts `observeCandidates` to the main thread, then `scheduleMotion()` reads `selectedTrackId()` immediately. The same detection can be pumped with the old id. The next detection replaces it, including a stale anchor, but that one frame can still draw the old box. Exact change, not applied (file is not in this slice): after `observeCandidates` returns, pass that track id into `handoff.pump` (call `observeCandidates` before scheduling motion, or store the id on `DetectionBaseline`). `invalidate()` can stay on the main thread.
- A new box under the point does not replace the lock while the old box still covers the point unless the new box is less than half the area. That is the jitter guard. A modestly smaller box will not switch until the point leaves the old box.
- Selection is the picture center, not a hand landmark. Hands stay out of the lock.
- `BoxHold` still repeats the last box for 2 seconds when the incoming list is empty. A published replacement removes it. An empty motion result does not.
