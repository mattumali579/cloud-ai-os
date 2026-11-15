import { drawReadableText } from "../coords";
import type { DistanceReading } from "./provider";

export function drawMarkerOverlay(
  ctx: CanvasRenderingContext2D,
  reading: DistanceReading,
  mirrored: boolean,
): void {
  if (!reading.corners || reading.corners.length !== 4) {
    return;
  }
  const corners = reading.corners;
  ctx.save();
  ctx.beginPath();
  ctx.moveTo(corners[0].x, corners[0].y);
  for (let i = 1; i < corners.length; i += 1) {
    ctx.lineTo(corners[i].x, corners[i].y);
  }
  ctx.closePath();
  ctx.strokeStyle = reading.available ? "rgba(80,255,170,0.95)" : "rgba(255,180,80,0.9)";
  ctx.lineWidth = 3;
  ctx.stroke();
  ctx.fillStyle = reading.available ? "rgba(40,255,170,0.12)" : "rgba(255,180,80,0.08)";
  ctx.fill();
  const mid = {
    x: corners.reduce((s, p) => s + p.x, 0) / 4,
    y: corners.reduce((s, p) => s + p.y, 0) / 4,
  };
  ctx.font = "16px ui-monospace, Consolas, monospace";
  ctx.fillStyle = "#ffffff";
  ctx.textAlign = "center";
  drawReadableText(
    ctx,
    reading.available ? reading.rangeLabel : "distance unavailable",
    mid.x,
    mid.y,
    mirrored,
  );
  ctx.restore();
}
