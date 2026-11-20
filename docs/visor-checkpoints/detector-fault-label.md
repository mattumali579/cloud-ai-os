# Detector fault label

Overlay diagnosis for a detector-stage fault. Not a phone, Visor, or glasses verification. The rest of Visor Tracker is not claimed complete.

## Cause

`OverlayDiagnostics.diagnose` treated a failure as a detector failure only when the trimmed text started with `MediaPipe`. A detector-stage fault is stored by `StageFaults.summary()` as `detect: …`. The service records that stage as `detect: Decode/detection failed: …`. That text does not start with `MediaPipe`, so the overlay hop was `STREAM_FAILED` and the code was `STREAM FAILED`.

## Fix

A failure is now `DETECTOR_FAILED` / `DETECTOR FAILED` when it starts with `MediaPipe`, starts with `detect:`, or contains `Decode/detection`. The original failure text stays in the detail. Other messages, including `VDK stream failed: …`, stay `STREAM FAILED`.

## Files changed

- `experiments/visor-tracker-android/app/src/main/java/com/mattumali/visortracker/OverlayDiagnostics.kt`
- `experiments/visor-tracker-android/app/src/test/java/com/mattumali/visortracker/OverlayDiagnosticsTest.kt`

## Test

`OverlayDiagnosticsTest.detectorStageFaultWithoutMediaPipePrefixIsDetectorFailure` uses `detect: Decode/detection failed: model hung`. That string does not start with `MediaPipe`. The test expects hop `DETECTOR_FAILED`, code `DETECTOR FAILED`, and the same detail text. No device, network, randomness, or sleep.

## Command

`experiments/visor-tracker-android` has no `gradlew.bat`. The run used `C:\Users\Admin\AppData\Local\Temp\gradle-9.6.0\bin\gradle.bat`. `ANDROID_HOME` was set to `C:\Users\Admin\AppData\Local\Android\Sdk` for that process because `local.properties` is absent. `local.properties` was not written.

The Gradle lock `docs/visor-checkpoints/GRADLE_LOCK` was created with .NET `File.Open` `CreateNew` and exclusive share, then deleted after the process exited. `app/build` was not deleted.

```
gradle :app:testDebugUnitTest --tests com.mattumali.visortracker.OverlayDiagnosticsTest
```

## Pass/fail

Pass. 26 tests, 0 failures, 0 errors, 0 skipped. `BUILD SUCCESSFUL`.
