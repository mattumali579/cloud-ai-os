# Gesture hologram HUD

Local webcam demo for GitHub issues #29 and #31. Isolated from Jarvis glasses work.

This copy lives at `C:\Users\mattu\cloud-ai-os\apps\gesture-hud`.

## Start

```powershell
cd C:\Users\mattu\cloud-ai-os\apps\gesture-hud
npm install
npm test
npm run build
npm run dev
```

Open http://127.0.0.1:5177/

Press **Start camera**, allow the webcam, then pick a mode.

## Original Filter

Hold both hands up. A colored portal stretches between your fingertips. Drop a hand to hide it. Press **Next look** or pinch thumb and pinky to cycle colors. This mode is the original look and is still the default.

## Distance

Print the ArUco 4x4 marker from the page, at 100% scale. Measure the outer black square with a ruler and type that size. Tape the marker to a wall or to an object. The number on screen is **camera-to-marker range only**. If the marker is gone or weak, it says **distance unavailable**. It will not guess distance from unmarked objects or from hand tracking.

Home calibration: stand at a tape-measured distance, show the marker, press **Calibrate at tape distance**.

Physical tape-measure checks (0.5 m, 1 m, 1.5 m, 2 m, five readings each) are **not done until you run them**. Unit tests are not that check.

## Powers (ROOM, Shambles, Rasengan)

On **Powers**:
- Clawed / cupped hand, palm down, held still → ROOM grows until it fills the camera view, then stays put. It does not follow your hand.
- Cupped hand, palm up, held still → Rasengan, a small spinning energy ball over the palm. It does not grow into ROOM. Close power before ROOM.
- Inside ROOM, thumb + index + middle at the camera = READY. Twist so your palm faces you = the two fake objects swap once.
- Force ROOM / Force Rasengan are backups if the camera guesses the palm wrong. Calibrate palm up/down with your real hand in view.

Nothing is recorded or uploaded. Palm tilt is a guess until you try it on the webcam.
