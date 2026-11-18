# Detection regression

Version name stayed 0.23.0. Package id stayed `com.mattumali.visortracker.stable`. No USB or wireless ADB is required to run or to read diagnostics.

## Last implementation that could display a finished detection

`7a42926` is version **0.22.0**. The phone observation recorded in `a122186` is that video was already arriving at about 29 fps and the detector finished zero passes, so no box was drawn. `a122186` moved object detection onto the CPU delegate. `7a42926` starts that detector even when grayscale capture fails. `40acc67` is version **0.23.0** and keeps that camera, detector, and publish path, adding only `DetectorSchedule`.

There is no commit that records a later glasses check where a box was seen. Hardware display is still unverified.

## What changed after that and hid the box

`9d51f6a` (still labeled 0.23.0) made two display changes:

- `YuvSnapshot.writeArgb` threw on the first sample outside the copied plane. Version 0.22 kept converting. A MediaCodec buffer whose row stride does not fit the last row then failed the whole frame.
- The detector thread waited with no timeout for the main thread to store the lock id. If that wait did not apply, the detection was not queued and was not drawn. Version 0.22 and 0.23 posted the box without that gate.

## Fix

- A packed frame whose row stride is longer than the buffer still decodes. A frame that misses half or more of its samples still fails.
- A finished detection with a gray anchor is drawn directly when the lock wait times out or does not apply.
- `TrackerLog` appends `detect`, `publish`, and failures to `files/visor-tracker-diagnostics.txt`. EXPORT DIAGNOSTICS on the Tracker screen shows the tail and shares the file. The file stays on the phone when Live View switches Wi-Fi.
