# Visor Fast Tracking (Android)

Free on-device object/hand/face detection with labeled boxes rendered **over the phone's full-screen Visor Live View**. Compatible target: Samsung Galaxy S22 Ultra (Android 16) and Ray-Ban Meta using Visor.

**This is not an AR display inside normal Ray-Ban Meta glasses:** they have no display. Visor's existing video remains in its own app; the Android companion is a transparent, touch-through overlay.

## Architecture

- Official **Visor Development Kit (VDK) v0.1.1** from Maven Central: `com.macedonga.visor:kit:0.1.1`.
- User grants `Scope.CAMERA` from Visor's consent screen; no Developer Mode token or separate local server needed.
- `Visor.camera.live(LiveConfig { quality = LOW; frameRate = 30; audio = false })` joins the existing stream when one runs. On a new stream, 30 FPS is **requested**, not guaranteed. Visor picks the actual rate and size when Live View was already running.
- `LiveVideoDecoder` decodes HEVC/H.264 on Android MediaCodec, returning hardware-decoded YUV frames. `YuvSnapshot` copies selected frames to bounded 384px (Fast) or 512px (Higher accuracy) RGB for MediaPipe without blocking the video decoder.
- Object detection: **Fast mode** (EfficientDet-Lite0, 320×320 model) or **Higher accuracy** (EfficientDet-Lite2, 448×448 model). Both run within the same app package. Optional hands and faces are staggered. Slow AI frames are dropped rather than queued. The detector's maximum requested cadence is approximately 15 Hz, but inference may be slower on hardware.
- `MotionTracker` uses inexpensive grayscale block matching to follow box motion on intervening decoded frames, bounded to avoid stale boxes. This is lightweight image tracking, **not** 30 AI detections per second.
- `TrackingOverlayView` draws transparent boxes with cover/crop transform matching Visor's expanded Live View. The Android user-started foreground service keeps tracking alive during app switching; the Stop notification terminates it.
- The notification displays measured **video FPS, AI FPS, box/tracking FPS**, and detection count. Results depend on heat, camera quality, movement and phone hardware.

The old 5-FPS `LIVE_FRAMES` JPEG service is no longer used by this version. VDK streaming can deliver higher frame rates; real-world alignment and sustained FPS **must still be measured on the actual phone**.

## Install / run

1. Keep **Visor** installed and set up on the glasses.
2. Continue using the existing **Visor Fast Tracking** app package (`com.mattumali.visortracker.stream`). No new app package is created.
3. Open Visor Fast Tracking and tap **Allow display over other apps**; enable it in Android Settings.
4. Tap **Grant Visor camera access**; on Visor's consent screen allow **Camera** for the companion app.
5. For best responsiveness, leave **Also detect hands and faces** unchecked. Toggle **Higher accuracy** if you want Lite2's increased recognition quality, understanding that AI FPS and battery life may be worse. The setting takes effect at the next tracking start.
6. Tap **START FAST OVERLAY** and open Visor's Live View, fully expanded (not mini-window/stepped).
7. Add **Visor Tracker** to Samsung Quick Settings once (swipe down twice, tap Edit, add Visor Tracker). Start Live View inside Visor, then toggle tracking from Quick Settings without switching apps. Check bounding boxes directly over the Visor live picture and read **Video / AI / boxes FPS** from the tracker foreground notification. Stop with the notification's **Stop** action or reopen the companion.

If permissions were denied, go to Visor **Settings → Apps** to review the companion app's Camera scope. The overlay itself also needs Android's permission to draw over other apps. A camera authorization failure is not fixed by pasting a Developer Mode token.

## Build

Isolated app build; the rest of the Jarvis repo is untouched:

- Android SDK API 37.0 (canary SDK channel), JDK 17
- Gradle 9.6.0, Android Gradle Plugin 9.4.1, Kotlin Gradle Plugin 2.4.20 (aligned with the VDK)
- Local command: `gradle --no-daemon assembleDebug testDebugUnitTest`
- APK: `app/build/outputs/apk/debug/app-debug.apk`
- CI: `.github/workflows/visor-tracker-android.yml`

GitHub Actions artifacts are ZIP files containing the APK. Extract `app-debug.apk` before installing; do not attempt to install the ZIP.

### Signing limitation (important: installed app update identity)

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


## Accuracy / stability upgrade

Version 0.4 preserves the same app ID as 0.3, adds EfficientDet-Lite2 optional
accuracy mode, the lower-confidence detection and stable IDs from the preceding
commits, and the one-tap Quick Settings toggle. It does not magically recognize
arbitrary unsupported categories; both EfficientDet models know COCO's 80 classes.
Try the higher accuracy mode when objects are missed; return to Fast when the phone
gets hot or AI FPS becomes too low.

**Do not uninstall v0.3 merely because this source changed.** GitHub Actions
debug builds use runner-specific signing keys, which Android cannot use for an
in-place update over a differently signed v0.3 installation. The original private
signing key cannot be recovered from an APK. A stable signer is required before
we can promise seamless updates; if the old private key is lost, a one-time
signing migration is unavoidable. Never publish a signing keystore.


## Future automatic updates (v0.5+)

- Keep the exact Android package ID `com.mattumali.visortracker.stream`.
- Sign each shipped version using the SAME private Android signing key (protected GitHub Actions secrets).
- A GitHub Actions tag `visor-tracker-vN` (N = Android `versionCode`) publishes an APK asset named `Visor-Fast-Tracking.apk` to the public GitHub Release **only after Android tests pass** and stable signing is configured.
- The app has a built-in updater: on app launch and periodically on **unmetered Wi-Fi**, it queries only published `visor-tracker-vN` releases, verifies the APK size, optional GitHub SHA-256 digest, package identity, versionCode, and lets Android PackageInstaller verify the signature. It never accepts an APK with a different package ID.
- One-time: enable **Allow from this source** in Android settings when prompted. The app requests Android's supported self-update without user interaction, but will display a system approval notification if Samsung requires confirmation. No root, USB, separate updater APK, or arbitrary Android permission bypass.
- Automatic updates are toggleable in the Tracker app. The updater delays installation while tracking is active to avoid interrupting Live View. Android may defer periodic checks for network and battery.
- **One-time migration only**: installed v0.3 was signed with a lost ephemeral debug key. A v0.5+ package built with a different permanent key cannot install over v0.3; the old APK must be replaced once. After that use only the permanent signing key, never reinstall for routine updates.

Security: GitHub Releases must be created from tested source and the protected signing key must NEVER be committed to the repository or bundled inside the APK. GitHub Releases from other components of cloud-ai-os are ignored by the Tracker updater.
