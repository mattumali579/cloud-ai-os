# Visor Tracker — live overlay for Android

This experimental, free companion app draws MediaPipe detection boxes **on top of the phone's expanded Visor Live View**. It is not a HUD inside standard Ray-Ban Meta glasses (which have no display). Source project and existing Jarvis services remain intact.

## How it works

1. The user grants Android **Display over other apps** permission and starts the overlay.
2. The user opens Visor's native Live View **in full screen**. Native Visor video continues independently.
3. A user-started foreground service subscribes to Visor's token-protected `LIVE_FRAMES` Messenger service.
4. MediaPipe detects objects every processed frame; optional hand scans occur every second processed frame, and faces every fourth.
5. An Android `TYPE_APPLICATION_OVERLAY` window draws transparent labeled boxes. It does not intercept touches, replace Visor video, upload frames, or need Tailscale/Jarvis.

Visor developer JPEG frames are available **at most once every 200 ms (5 detections/second ceiling)**, plus model processing overhead. The interpolated overlay drawing updates more frequently than detection but does **not** improve the actual detector throughput. Native Visor video may run much faster; the overlay is a separate layer.

The current implementation **does not consume the VDK codec stream**. Achieving >5 fresh detections per second requires integrating a higher-rate video stream and measuring on actual phone hardware. Do not advertise 30 FPS tracking without testing.

## Android setup

1. Keep your original Visor installed; enable Visor **Settings → Advanced → Developer Mode** and copy the current token.
2. Install the **Visor Live Overlay** APK, a separate package (`com.mattumali.visortracker.overlay`); it will not replace or uninstall earlier Tracker apps.
3. Open Visor Live Overlay and select **Allow display over other apps** in Android settings.
4. Paste the token; leave **Also detect hands and faces** unchecked for the fastest object-only mode.
5. Tap **START LIVE VIEW OVERLAY**, then switch to Visor and start Live View. Leave it **expanded**, not in the stepped/windowed mode. Detection boxes should appear over the video.
6. Stop using the Android foreground notification's **Stop overlay** action, or reopen the overlay app and tap STOP.

The application remembers the token privately on-device; rotate the token in Visor if it may have been exposed. The system overlay may remain visible on top of other apps while enabled; explicitly stop it when leaving Visor. Android may require user authorization for foreground notifications.

## Build and tests

Requires JDK 17, Android SDK Platform 35 and Gradle 8.7:

```powershell
cd experiments/visor-tracker-android
gradle --no-daemon assembleDebug testDebugUnitTest
```

The workflow builds the APK and unit-tests tracking IDs and Visor's full-screen cover/crop transform. Three MediaPipe models are downloaded from Google's official hosting during build.

APK output: `app/build/outputs/apk/debug/app-debug.apk`. Artifact on GitHub Actions is a **ZIP** containing that installable APK. Download and extract the APK before installing.

### Signing and update limitation

GitHub Actions currently generates an ephemeral Android **debug signing key** on each fresh runner. APKs from separate runs cannot generally update each other in-place because Android requires matching signing certificates. Changing `versionCode` does not fix this. This new overlay uses its own app ID to **coexist with** existing installed Tracker apps, so **no uninstall of those apps is required**.

For future seamless in-place overlay updates, set up one stable private signing keystore with a release signing configuration; never commit the private key into this public repository. Until that is done, do not promise update-in-place support for this GitHub workflow.

## Verified vs still unverified

- Source: Visor developer service action, frame messages and frame interval checked against `macedonga/visor`'s `DeveloperIntents.kt` and `FrameService.kt`.
- Automated: GitHub Actions build and unit tests only, after a green run.
- Physical test **required**: Android 16 system overlay permission, frame subscription on Samsung S22 Ultra, alignment with Visor's full-screen view, true detection FPS,  thermal/battery behaviour and touch-through behavior.
- There is no supported way to render these boxes inside non-display Ray-Ban Meta glasses.

Visor source:
- https://github.com/macedonga/visor/blob/main/app/src/main/kotlin/com/macedonga/visor/outside/DeveloperIntents.kt
- https://github.com/macedonga/visor/blob/main/app/src/main/kotlin/com/macedonga/visor/outside/FrameService.kt
- https://github.com/macedonga/visor/blob/main/app/src/main/kotlin/com/macedonga/visor/media/LiveScreen.kt
