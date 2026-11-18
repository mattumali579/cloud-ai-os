# Overlay same-frame lock

Slice only. The detection that switches the lock now publishes that new id. This does not claim a phone, Visor, or glasses check, and it does not finish Visor Tracker.

## Cause

`OverlayTrackingService` posted `observeCandidates` to the main thread, then `scheduleMotion` read `selectedTrackId()` on the motion thread. On a real switch the overlay id was still the previous target, because the posted update had not run. `TrackingHandoff` focuses the box for the id it is given, so that frame published the previous box. The next detection, after the update was visible, replaced it.

`TrackingOverlayView`, `OverlayDiagnostics`, and `CoverGeometry` were already corrected. This slice does not change them. The lock types (`AimLock`, `TrackingHandoff`, and the rest of that set) were not edited.

## Fix

The same-frame choice is `OverlayFrameSelection.lockIdForFrame`. It applies the lock, then reads the id, then queues the draw. The draw does not make the id visible.

`applyDetectionLock` runs `observeCandidates` on the main looper and returns only after that call has stored the id. The detector worker waits for that. The id is saved on the pending detection. Motion pumps that id. A queued detection with no lock stays unlocked; it does not fall through to a later read. A gray step with no detection still reads the live id.

## Tests

JVM only. No device, network, randomness, or sleep.

`OverlayFrameSelectionTest` (2 tests, 0 failures):

- Reading the id before the update runs publishes track 1. Applying the lock first publishes track 2 on that same frame, and the queued draw already sees 2.
- One aimed frame that has not yet switched still publishes track 1.

`OverlayDiagnosticsTest`: 26 tests, 0 failures. That file was not edited. The suite now has 26 tests; the earlier overlay note said 25.

## Command

`experiments/visor-tracker-android` has no `gradlew.bat`. Gradle 9.6.0 was `C:\Users\Admin\AppData\Local\Temp\gradle-9.6.0\bin\gradle.bat`. `ANDROID_HOME` was `C:\Users\Admin\AppData\Local\Android\Sdk`. The Gradle lock `docs/visor-checkpoints/GRADLE_LOCK` was created with .NET `File.Open` `CreateNew` and exclusive share, then deleted after Gradle exited. `app/build` and signed APKs were not deleted.

```
gradle.bat :app:testDebugUnitTest --tests com.mattumali.visortracker.OverlayFrameSelectionTest --tests com.mattumali.visortracker.OverlayDiagnosticsTest
```

Result: **pass** (BUILD SUCCESSFUL, exit 0). 28 tests, 0 failures, 0 errors.

## Files changed

- `experiments/visor-tracker-android/app/src/main/java/com/mattumali/visortracker/OverlayTrackingService.kt`
- `experiments/visor-tracker-android/app/src/test/java/com/mattumali/visortracker/OverlayFrameSelectionTest.kt`
- `docs/visor-checkpoints/overlay-race.md`

## Leftover risks

- Not run on a phone, Visor Live View, or glasses. The unit test covers the publish order, not the screen.
- The detector worker waits until the main thread applies the lock. A busy main looper delays that inference. If the wait is interrupted, or the looper rejects the post, that frame can still keep the previous id.
- A motion step that already read the previous id can post one more old box. The detection that applied the switch is pumped with the new id after that.
- The test calls `lockIdForFrame` directly. It does not construct the service, so it does not execute the main-looper wait. A later edit that returns from `applyDetectionLock` before `observeCandidates` has run would miss this test and draw the old box again.
