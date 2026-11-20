# Overlay slice

The pointed object could fail to become pixels. This slice fixes that draw path. It does not claim the glasses, a phone, or the rest of Visor Tracker are done.

## Bugs

1. **Stale sequence dropped a real detection.** `TrackingOverlayView.updateFrame` returned immediately when `frame.sequence < shownSequence`. A newer empty frame (gray capture failed, or a clear) advanced that watermark. The in-flight detection, which still held the lock, was older and never drawn. A blank overlay stayed blank. A box for a different track was left on screen after the lock had moved.

2. **A swallowed handoff never reached the view.** `TrackingHandoff.pump` returns null when it still believes a box is showing (`tryWalk` drops a stale anchor in that case, and `focus` returns null when the lock id is missing). `OverlayTrackingService` only called `updateFrame` for a non-null event, so that detection was discarded. `TrackingHandoff` is not owned here, so the service now publishes the raw detection and the overlay decides whether it may paint.

3. **Held boxes were projected in the wrong pixel space.** An empty follow-up frame kept the previous rectangles through `BoxHold`, then `updateFrame` copied them onto the new frame with `frame.copy(boxes = kept)`. Those rectangles were measured in the detection frame (for example 1024×768). The later frame can be the small motion image (192×144). Cover-cropping the old pixels with the new size throws the lock off the screen. The same box is on a 1080×1920 portrait screen in detection space and fully off it in the 192×144 space.

4. **Diagnostics reported TRACKING when nothing was painted.** `drawnBoxes == 0` with `freshBoxes > 0` or `rawDetections > 0` still took the TRACKING hop, ahead of BOXES HIDDEN. The corner mark and the notification said the overlay was fine. `frameAgeMs` also narrowed a `Long` with `toInt()`, which wraps after about 24 days of uptime and then clamped to 0, and a held box was aged against the empty frame's newer timestamp, so a stale hold looked new.

5. **The first draw could return before the window had a size.** `onDraw` returned when `width` or `height` was 0 and did not schedule another draw when layout arrived. The boxes stayed in `latest` and were not painted.

## Fix

- `OverlayDiagnostics.acceptFrame` draws a newer frame, rejects an older clear, fills a blank overlay from a recent older detection, and lets that older detection replace a painted box only when the painted track is not the current lock. `nextShownSequence` does not move the watermark backward.
- `frameToDraw` keeps the measurement frame's width, height, and capture time while a box is held.
- `ageMs` clamps a negative or overflowing age instead of wrapping it.
- TRACKING requires `drawnBoxes > 0`. A detected or published box with nothing painted is BOXES HIDDEN, including when an old stream failure is still set.
- `onSizeChanged`, plus one posted invalidate if `onDraw` runs before layout, paints the stored boxes once the view has a size.
- Once the view is measured, `drawnBoxCount` counts only boxes that still overlap the screen after cover crop.
- The service publishes a paintable detection when the handoff returns null, and sets `lastBoxCount` from the count the overlay actually kept.

Cover scale is unchanged: center crop, matching the existing geometry tests.

## Files changed

- `experiments/visor-tracker-android/app/src/main/java/com/mattumali/visortracker/TrackingOverlayView.kt`
- `experiments/visor-tracker-android/app/src/main/java/com/mattumali/visortracker/OverlayTrackingService.kt`
- `experiments/visor-tracker-android/app/src/main/java/com/mattumali/visortracker/OverlayDiagnostics.kt`
- `experiments/visor-tracker-android/app/src/main/java/com/mattumali/visortracker/CoverGeometry.kt`
- `experiments/visor-tracker-android/app/src/test/java/com/mattumali/visortracker/OverlayDiagnosticsTest.kt`
- `experiments/visor-tracker-android/app/src/test/java/com/mattumali/visortracker/CoverGeometryTest.kt`

Not changed: `MainActivity.kt`, `PipelineStats.kt`, `TrackerQuickTile.kt`, `DetectorOverlaySimulationTest.kt`. No shared file outside this slice was edited.

## Tests

JVM unit tests, no randomness and no sleeps.

- `OverlayDiagnosticsTest`: 25 tests, 0 failures. Draw decision, hold geometry, age clamp, and the hidden-versus-tracking hop.
- `CoverGeometryTest`: 5 tests, 0 failures. Center crop, a centered object on the portrait screen, and a box that lands entirely in the cropped margin.
- `PipelineStatsTest`: 4 tests, 0 failures. Unchanged report formatting.

`DetectorOverlaySimulationTest` was not run. It needs an emulator, and this slice was told not to start one.

## Command

`experiments/visor-tracker-android` has no `gradlew.bat`. CI uses Gradle 9.6.0 (`gradle --no-daemon`). The same test filters were run with the local Gradle 9.6.0 binary, under `docs/visor-checkpoints/GRADLE_LOCK` (exclusive `File.Open` CreateNew). The lock was deleted after Gradle exited.

```
C:\Users\Admin\AppData\Local\gradle-dist\gradle-9.6.0\bin\gradle.bat :app:testDebugUnitTest --tests com.mattumali.visortracker.OverlayDiagnosticsTest --tests com.mattumali.visortracker.PipelineStatsTest --tests com.mattumali.visortracker.CoverGeometryTest
```

`ANDROID_HOME` was `C:\Users\Admin\AppData\Local\Android\Sdk` for that process. There is no `local.properties` in the tree.

Result: **pass** (BUILD SUCCESSFUL, exit 0).

## Leftover risks

- Not verified on glasses, Visor Live View, or a phone. Center-crop math matches the unit tests. If expanded Live View letterboxes or rotates the camera image, the box will still miss the object.
- The locked stroke is still white. On a bright scene it can be painted and still hard to see. The unlocked object stroke stays green.
- `TrackingHandoff` still returns null while its private `showing` flag is true. The service workaround republishes the raw detection. An older frame cannot move the current lock backward. A newer raw frame is still applied, so a missing lock can switch to the center object, which is the existing one-object rule. A tighter fix would live in `TrackingHandoff.tryWalk`: call `publishDetached` when the anchor has left history and the overlay's drawn count is 0. That file was not edited.
- The 2 second hold timer still clears the overlay if no later frame is accepted. The next accepted detection has to paint it again.
- Samsung stacking of a translucent overlay over Visor was not rechecked.
