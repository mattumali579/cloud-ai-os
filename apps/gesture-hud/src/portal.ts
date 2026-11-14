import type { FilterName } from "./filters";
import { applyFilter } from "./filters";
import type { Point, TrackedHand } from "./landmarks";

const TIPS = [4, 8, 12, 16, 20] as const;

export type Pixel = { x: number; y: number };

const stamp = typeof document === "undefined" ? null : document.createElement("canvas");

export function sortHands(hands: TrackedHand[]): TrackedHand[] {
  return [...hands].sort((a, b) => a.landmarks[0].x - b.landmarks[0].x);
}

export function portalPolygons(hands: TrackedHand[]): Point[][] {
  if (hands.length < 2) {
    return [];
  }
  const [left, right] = sortHands(hands);
  const L = left.landmarks;
  const R = right.landmarks;
  if (L.length < 21 || R.length < 21) {
    return [];
  }
  return [
    [L[4], L[8], L[12], R[12], R[8], R[4]],
    [L[12], L[16], L[20], R[20], R[16], R[12]],
  ];
}

export function toPixels(points: Point[], width: number, height: number): Pixel[] {
  return points.map((point) => ({ x: point.x * width, y: point.y * height }));
}

export function bounds(points: Pixel[]): { x: number; y: number; w: number; h: number } | null {
  if (points.length === 0) {
    return null;
  }
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const point of points) {
    minX = Math.min(minX, point.x);
    minY = Math.min(minY, point.y);
    maxX = Math.max(maxX, point.x);
    maxY = Math.max(maxY, point.y);
  }
  const x = Math.floor(minX);
  const y = Math.floor(minY);
  return { x, y, w: Math.ceil(maxX) - x, h: Math.ceil(maxY) - y };
}

function drawPolygonPath(ctx: CanvasRenderingContext2D, poly: Pixel[]): void {
  ctx.beginPath();
  ctx.moveTo(poly[0].x, poly[0].y);
  for (let i = 1; i < poly.length; i += 1) {
    ctx.lineTo(poly[i].x, poly[i].y);
  }
  ctx.closePath();
}

export function drawPortals(
  ctx: CanvasRenderingContext2D,
  source: CanvasRenderingContext2D,
  polygons: Pixel[][],
  filters: FilterName[],
  opacity: number,
): void {
  if (!stamp || opacity < 0.02 || polygons.length === 0) {
    return;
  }
  ctx.save();
  ctx.globalAlpha = opacity;
  polygons.forEach((poly, index) => {
    const box = bounds(poly);
    if (!box || box.w < 8 || box.h < 8) {
      return;
    }
    const x = Math.max(0, box.x);
    const y = Math.max(0, box.y);
    const w = Math.min(box.w, source.canvas.width - x);
    const h = Math.min(box.h, source.canvas.height - y);
    if (w < 8 || h < 8) {
      return;
    }
    const patch = source.getImageData(x, y, w, h);
    applyFilter(patch, filters[index] ?? filters[0]);
    stamp.width = w;
    stamp.height = h;
    stamp.getContext("2d")!.putImageData(patch, 0, 0);

    ctx.save();
    drawPolygonPath(ctx, poly);
    ctx.clip();
    ctx.drawImage(stamp, x, y);
    ctx.restore();

    drawPolygonPath(ctx, poly);
    ctx.strokeStyle = "rgba(255,255,255,0.95)";
    ctx.lineWidth = 2;
    ctx.stroke();
  });
  ctx.restore();
}

export function fingertipPixels(hand: TrackedHand, width: number, height: number): Pixel[] {
  return TIPS.map((index) => ({
    x: hand.landmarks[index].x * width,
    y: hand.landmarks[index].y * height,
  }));
}
