# Visor Live Tracker (Android)

A free, on-device companion app for Ray-Ban Meta glasses using Visor Live View. It receives Visor's token-protected JPEG frames, runs MediaPipe Tasks for hands/faces/objects, and uses OpenCV to resize frames and draw stable tracked boxes/IDs. It does not upload frames, use an inference API, or require a Windows server.

## Verified Visor camera interface

Visor's Developer Mode source defines the service action `com.macedonga.visor.action.LIVE_FRAMES`: bind it with package `com.macedonga.visor`, subscribe with message `what=1`, provide the Developer Mode `token` and optional `interval_ms` (minimum 200), set `replyTo` to the app's Messenger, and read message `what=3` with `jpeg` bytes and `time`. Message `what=4` reports refusal (`TOKEN` or `OFF`). Visor emits no frames when Live View is not running and stops frames when Developer Mode is off or the token is rotated.

Official source:
- https://github.com/macedonga/visor/blob/main/app/src/main/kotlin/com/macedonga/visor/outside/DeveloperIntents.kt
- https://github.com/macedonga/visor/blob/main/app/src/main/kotlin/com/macedonga/visor/outside/FrameService.kt

The feed is still JPEG frames, rate-limited to one every 200 ms. This app requests 250 ms (~4 fps) to leave some processing headroom on a Galaxy S22 Ultra. If model inference falls behind, it drops frames instead of accumulating delay.

## Build

Requires JDK 17 and Android SDK platform 35. On Windows PowerShell, from this folder:

```powershell
gradle --no-daemon assembleDebug testDebugUnitTest
```

The build downloads three public Google MediaPipe model files into Gradle's generated assets folder; they are not committed into the repository. The APK is:

```
app/build/outputs/apk/debug/app-debug.apk
```

This is a debug APK, suitable for local installation after allowing installs from the selected file-sharing app. No Visor or Meta token is embedded in the APK.

## Phone setup

1. In Visor, open **Settings → Advanced → Developer mode** and enable it.
2. Copy the token shown beneath Developer Mode.
3. Open Visor Tracker, paste the token, and tap **Start tracking**.
4. Start Visor **Live View**. Return to Tracker; frames should appear with labels and IDs.
5. If Visor says `TOKEN`, regenerate/copy the current token and retry. If it says `OFF`, enable Developer Mode. Keep Visor foregrounded as needed by your phone's battery/background settings.
6. Keep the phone on the temporary Wi-Fi Direct network during Live View. The frame feed is between Visor and this app on the phone; it does not need Jarvis, Tailscale, or the house Wi-Fi.

## What the detectors mean

- **Hands:** MediaPipe Hand Landmarker provides 21 landmark points per detected hand. Boxes are derived from the landmark extents.
- **Faces:** MediaPipe BlazeFace full-range detector provides face boxes. This detects faces but does **not** identify who the person is.
- **Objects:** MediaPipe EfficientDet-Lite0 detects the 80 COCO classes.
- **Tracking:** OpenCV renders the visualization; a small IoU tracker assigns temporary per-label IDs and retains them for up to eight missed frames. IDs are not biometric identities and may change after a long occlusion.

The UI stays in the foreground while running, keeps the screen awake, and stops/unbinds the camera feed when it leaves the screen. Actual frame rate, thermals, battery use, detection quality, model load time and the installed Visor version's service binding must be measured on the phone and glasses.
