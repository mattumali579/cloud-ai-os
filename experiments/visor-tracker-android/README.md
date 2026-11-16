# Visor Fast Tracking (Android)

Free on-device object/hand/face detection with labeled boxes rendered **over the phone's full-screen Visor Live View**. Compatible target: Samsung Galaxy S22 Ultra (Android 16) and Ray-Ban Meta using Visor.

**This is not an AR display inside normal Ray-Ban Meta glasses:** they have no display. Visor's existing video remains in its own app; the Android companion is a transparent, touch-through overlay.

## Architecture

- Official **Visor Development Kit (VDK) v0.1.1** from Maven Central: `com.macedonga.visor:kit:0.1.1`.
- User grants `Scope.CAMERA` from Visor's consent screen; no Developer Mode token or separate local server needed.
- `Visor.camera.live(LiveConfig { quality = LOW; frameRate = 30; audio = false })` joins the existing stream when one runs. On a new stream, 30 FPS is **requested**, not guaranteed. Visor picks the actual rate and size when Live View was already running.
- `LiveVideoDecoder` decodes HEVC/H.264 on Android MediaCodec, returning hardware-decoded YUV frames. `YuvSnapshot` copies selected frames to bounded 384px RGB for MediaPipe without blocking the video decoder.
- Object detection: MediaPipe EfficientDet-Lite0; optional hands and faces are staggered. Slow AI frames are dropped rather than queued. The detector's maximum requested cadence is approximately 15 Hz, but inference may be slower on hardware.
- `MotionTracker` uses inexpensive grayscale block matching to follow box motion on intervening decoded frames, bounded to avoid stale boxes. This is lightweight image tracking, **not** 30 AI detections per second.
- `TrackingOverlayView` draws transparent boxes with cover/crop transform matching Visor's expanded Live View. The Android user-started foreground service keeps tracking alive during app switching; the Stop notification terminates it.
- The notification displays measured **video FPS, AI FPS, box/tracking FPS**, and detection count. Results depend on heat, camera quality, movement and phone hardware.

The old 5-FPS `LIVE_FRAMES` JPEG service is no longer used by this version. VDK streaming can deliver higher frame rates; real-world alignment and sustained FPS **must still be measured on the actual phone**.

## Install / run

1. Keep **Visor** installed and set up on the glasses.
2. Install **Visor Fast Tracking** (package `com.mattumali.visortracker.stream`). This is a separate package, so earlier Tracker APKs may remain installed and need not be removed.
3. Open Visor Fast Tracking and tap **Allow display over other apps**; enable it in Android Settings.
4. Tap **Grant Visor camera access**; on Visor's consent screen allow **Camera** for the companion app.
5. For best responsiveness, leave **Also detect hands and faces** unchecked.
6. Tap **START FAST OVERLAY** and open Visor's Live View, fully expanded (not mini-window/stepped).
7. Check bounding boxes directly over the Visor live picture and read **Video / AI / boxes FPS** from the tracker foreground notification. Stop with the notification's **Stop** action or reopen the companion.

If permissions were denied, go to Visor **Settings → Apps** to review the companion app's Camera scope. The overlay itself also needs Android's permission to draw over other apps. A camera authorization failure is not fixed by pasting a Developer Mode token.

## Build

Isolated app build; the rest of the Jarvis repo is untouched:

- Android SDK API 36, JDK 17
- Gradle 8.14.4, Android Gradle Plugin 8.13.2, Kotlin 2.4.20
- Local command: `gradle --no-daemon assembleDebug testDebugUnitTest`
- APK: `app/build/outputs/apk/debug/app-debug.apk`
- CI: `.github/workflows/visor-tracker-android.yml`

GitHub Actions artifacts are ZIP files containing the APK. Extract `app-debug.apk` before installing; do not attempt to install the ZIP.

### Signing limitation (important)

GitHub Actions currently generates a temporary debug signing key per new runner. Android only accepts APK updates signed by the **same private key**. A package-name change is an install-as-new workaround, not an update fix. To support seamless future updates, the user must provide a secure persistent signing keystore through CI secrets or sign repeat builds locally with a single securely stored key. **Do not commit signing keys to GitHub.**

## Verification ladder

- **Source verification**: Visor's VDK `Camera.live`, `LiveVideoDecoder`, scope permissions, and `KitLive.watch` support this design.
- **Automated**: Gradle build plus JVM tests for box projection, tracking IDs, RGB/YUV conversion and grayscale motion. See CI result.
- **Device (not yet verified)**: real Visor Camera scope grant, VDK subscription while native Live View is shown, Android 16 overlay placement/touch-through, device FPS and thermals. Do not claim 30-FPS tracking from CI alone.

VDK source and official docs:
- https://github.com/macedonga/visor-kit/blob/main/android/src/main/kotlin/com/macedonga/visor/kit/Camera.kt
- https://github.com/macedonga/visor-kit/blob/main/android/src/main/kotlin/com/macedonga/visor/kit/LiveDecoders.kt
- https://github.com/macedonga/visor/blob/main/app/src/main/kotlin/com/macedonga/visor/outside/KitLive.kt
- https://visor.macedonga.com/developers/docs/camera-and-captures/
