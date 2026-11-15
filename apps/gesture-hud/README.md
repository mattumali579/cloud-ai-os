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

## ROOM and Shambles

Hold an open palm still for about half a second. A blue energy volume grows around the hand. Two labeled virtual props sit inside it (blue cube, pink sphere). Pinch one hand near a prop to drag it. Pinch both index fingers to thumbs at once to swap their places. **Reset ROOM** clears it. **Pause** freezes ROOM. **Original Filter** returns to the portal.

Nothing is recorded or uploaded.
