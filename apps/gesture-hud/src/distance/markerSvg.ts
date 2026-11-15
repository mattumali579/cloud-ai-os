import { markerCells } from "./arucoDict";

export function arucoSvg(id: number, sideCm: number): string {
  const cells = markerCells(id);
  const n = cells.length;
  const bits = cells
    .map((row, y) =>
      row
        .map((bit, x) =>
          bit
            ? ""
            : `<rect x="${x}" y="${y}" width="1" height="1" fill="#000"/>`,
        )
        .join(""),
    )
    .join("");
  return `<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${n} ${n}" width="${sideCm}cm" height="${sideCm}cm">
  <rect width="${n}" height="${n}" fill="#fff"/>
  ${bits}
</svg>`;
}

export function printSheetHtml(id: number, sideCm: number): string {
  const svg = arucoSvg(id, sideCm);
  return `<!doctype html>
<html>
  <head>
    <meta charset="utf-8"/>
    <title>Gesture HUD ArUco marker</title>
    <style>
      body { font-family: Segoe UI, sans-serif; padding: 24px; color: #111; }
      .mark { display: inline-block; border: 1px dashed #999; padding: 8px; }
      p { max-width: 520px; }
    </style>
  </head>
  <body>
    <h1>Print this marker</h1>
    <p>Print at 100% scale. After printing, the outer black square must measure <strong>${sideCm.toFixed(1)} cm</strong> with a ruler. Tape it to the object you want to range. This webcam cannot measure unmarked objects.</p>
    <div class="mark">${svg}</div>
    <p>Dictionary: OpenCV DICT_4X4_50 · id ${id}</p>
  </body>
</html>`;
}

export const HOME_CALIBRATION_STEPS = [
  "Print the marker at 100% scale. Do not fit-to-page.",
  "Measure the outer black square with a ruler and type that exact size.",
  "Tape the marker to a wall or the object.",
  "Measure camera-to-marker with a tape measure.",
  "Hold the marker fully in the webcam, then press Calibrate at tape distance.",
  "If the marker leaves the frame, the app must say distance unavailable — never a guessed number.",
];
