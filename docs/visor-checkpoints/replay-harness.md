# Visor replay harness

Deterministic JVM replay of fixed frames through the production tracker. Not a phone, Visor, or glasses run. MediaPipe was not executed.

## Scenarios

| Scenario | Result | What ran |
| --- | --- | --- |
| Moving object | Pass. A textured patch shifts 4 px. The published box left increases, and the cover-projected left increases with it. | Real `TrackingHandoff.pump` and `MotionTracker`. Real `LabelPolicy.selection` on the fixed score 0.9. Gray bytes are fixed. |
| Target switch A to B, then A-B-A | Pass. The first lock, the switched lock, and the return to the first lock each publish that same id on the frame that applies it. | Real `TrackIds.update`, `AimLock` (picture center), `OverlayFrameSelection.lockIdForFrame`, `TrackingHandoff`, `NearbyCandidates` inside the handoff, `LabelPolicy`. |
| Disappearing object | Pass. A blank gray frame publishes nothing. A detection that drops the locked id does not publish the other object. | Real `TrackingHandoff` and `MotionTracker`. |
| Invalid YUV | Pass. Zero-size, empty, short, overflowing stride, and use-after-`releasePlanes` all throw. None returns a decoded frame. | Real `YuvSnapshot.outputSize`, `writeArgb`, `retainedForTest`, `releasePlanes`, `toBitmap`. |
| Overlay alignment | Pass. The moved box stays visible on a 1080×1920 cover crop. A box in the cropped side margin is not visible. The projected edge is `CoverGeometry.project`, which `TrackingOverlayView.onDraw` uses before it draws. | Real `CoverGeometry.project` and `isVisible`. Canvas and the view are not constructed. |
| Detector input | Pass. A fixed 16×8 limited-range white frame decodes to 16×8 and the first pixel is white. | Real `YuvSnapshot.writeArgb`. This is the bitmap-sized input. The model is not run on it. |
| Hand fault notification | Pass. `hands: model hung` is copied into the notification extra. Object boxes from the successful optional call are kept. | Real `VisionPipeline.runOptional`, `OptionalModelFault`, `StageFaults`. |

## Boundaries

- **Real production code:** `YuvSnapshot`, `LabelPolicy`, `VisionPipeline.runOptional`, `OptionalModelFault`, `AimLock.pointedObject`, `OverlayFrameSelection`, `TrackIds`, `TrackingHandoff`, `MotionTracker`, `NearbyCandidates`, `CoverGeometry`.
- **Mocked:** `VisionPipeline.detect`. `ObjectDetector`, `HandLandmarker`, and `FaceDetector` were not called. The host JVM and the GitHub Actions unit-test job cannot load `libmediapipe_tasks_vision`. Category scores in the script are not model output. `TrackingOverlayView.onDraw` is not run; there is no window. The rectangle stops at `CoverGeometry.project`.
- **UNVERIFIED:** Visor Development Kit `camera.live`, the Ray-Ban Meta glasses, and a physical phone. That is the only integration left unverified. The JVM pieces above were exercised.

Picture-center aim is unchanged. The replay does not use a hand landmark. The white stroke, center-crop math, and the 0.45 accurate-mode floor were not changed.

## Command

Working directory: `experiments/visor-tracker-android`. No `gradlew.bat`. Gradle 9.6.0 at `C:\Users\Admin\AppData\Local\Temp\gradle-9.6.0\bin\gradle.bat`. `ANDROID_HOME` was set to `C:\Users\Admin\AppData\Local\Android\Sdk` for the process. `local.properties` was not written. No emulator was started.

```
$env:ANDROID_HOME = "C:\Users\Admin\AppData\Local\Android\Sdk"
$env:ANDROID_SDK_ROOT = $env:ANDROID_HOME
& "C:\Users\Admin\AppData\Local\Temp\gradle-9.6.0\bin\gradle.bat" --no-daemon :app:testDebugUnitTest assembleDebug assembleRelease
```

The unit-test job in `.github/workflows/visor-tracker-android.yml` runs `testDebugUnitTest` and requires `PipelineReplayTest` to be present with zero failures. Push and pull request both run that job when the Android tree or the workflow changes. No new emulator job was added.

## Pass/fail

**Pass.** `BUILD SUCCESSFUL`, exit 0. 151 tests, 0 failures, 0 errors, 0 skipped. `PipelineReplayTest` is 7 of those.

`assembleDebug` and `assembleRelease` succeeded. They were up to date because this harness is test-only; the APK bytes are from the earlier successful package of the same main sources.

## APK paths

Not installed on a phone. `VISOR_SIGNING_STORE_FILE` is unset, so the signed CI name `app-release.apk` was not produced.

- Debug: `experiments/visor-tracker-android/app/build/outputs/apk/debug/app-debug.apk` (53,623,322 bytes). Local debug signing. Not the stable phone certificate.
- Unsigned release: `experiments/visor-tracker-android/app/build/outputs/apk/release/app-release-unsigned.apk` (35,070,513 bytes).

## Resume

If Actions for the harness commit is not recorded yet:

```
cd C:\Users\Admin\cloud-ai-os-visor-tracker
git fetch origin feat/visor-overlay-phase1
git rev-parse HEAD
git rev-parse origin/feat/visor-overlay-phase1
gh run list --repo mattumali579/cloud-ai-os --commit <SHA>
```

Do not reset or discard the replay files. Re-run `:app:testDebugUnitTest` from `experiments/visor-tracker-android` only if source changed after this note.
