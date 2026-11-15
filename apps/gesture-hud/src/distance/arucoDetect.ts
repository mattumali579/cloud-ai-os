import { identifyBits, markerCells, ARUCO_SIZE } from "./arucoDict";

export type GrayImage = {
  data: Uint8Array | Uint8ClampedArray;
  width: number;
  height: number;
};

export type DetectedMarker = {
  id: number;
  corners: { x: number; y: number }[];
  rotation: number;
};

function grayAt(image: GrayImage, x: number, y: number): number {
  const sx = Math.max(0, Math.min(image.width - 1, x | 0));
  const sy = Math.max(0, Math.min(image.height - 1, y | 0));
  return image.data[sy * image.width + sx];
}

export function rgbaToGray(rgba: Uint8ClampedArray, width: number, height: number): GrayImage {
  const data = new Uint8Array(width * height);
  for (let i = 0; i < data.length; i += 1) {
    const o = i * 4;
    data[i] = (0.299 * rgba[o] + 0.587 * rgba[o + 1] + 0.114 * rgba[o + 2]) | 0;
  }
  return { data, width, height };
}

export function downsampleGray(image: GrayImage, maxWidth = 480): { image: GrayImage; scale: number } {
  if (image.width <= maxWidth) {
    return { image, scale: 1 };
  }
  const scale = image.width / maxWidth;
  const width = maxWidth;
  const height = Math.max(1, Math.round(image.height / scale));
  const data = new Uint8Array(width * height);
  for (let y = 0; y < height; y += 1) {
    const sy = Math.min(image.height - 1, Math.floor(y * scale));
    for (let x = 0; x < width; x += 1) {
      const sx = Math.min(image.width - 1, Math.floor(x * scale));
      data[y * width + x] = image.data[sy * image.width + sx];
    }
  }
  return { image: { data, width, height }, scale };
}

function integral(image: GrayImage): Float64Array {
  const w = image.width;
  const h = image.height;
  const ii = new Float64Array((w + 1) * (h + 1));
  for (let y = 1; y <= h; y += 1) {
    let row = 0;
    for (let x = 1; x <= w; x += 1) {
      row += image.data[(y - 1) * w + (x - 1)];
      ii[y * (w + 1) + x] = ii[(y - 1) * (w + 1) + x] + row;
    }
  }
  return ii;
}

function rectSum(ii: Float64Array, stride: number, x0: number, y0: number, x1: number, y1: number): number {
  return ii[y1 * stride + x1] - ii[y0 * stride + x1] - ii[y1 * stride + x0] + ii[y0 * stride + x0];
}

export function adaptiveBinary(image: GrayImage, radius = 12): Uint8Array {
  const ii = integral(image);
  const w = image.width;
  const h = image.height;
  const stride = w + 1;
  const out = new Uint8Array(w * h);
  for (let y = 0; y < h; y += 1) {
    const y0 = Math.max(0, y - radius);
    const y1 = Math.min(h, y + radius + 1);
    for (let x = 0; x < w; x += 1) {
      const x0 = Math.max(0, x - radius);
      const x1 = Math.min(w, x + radius + 1);
      const area = (x1 - x0) * (y1 - y0);
      const mean = rectSum(ii, stride, x0, y0, x1, y1) / area;
      out[y * w + x] = image.data[y * w + x] < mean * 0.94 ? 1 : 0;
    }
  }
  return out;
}

function convexHull(points: { x: number; y: number }[]): { x: number; y: number }[] {
  const sorted = [...points].sort((a, b) => (a.x === b.x ? a.y - b.y : a.x - b.x));
  if (sorted.length <= 3) {
    return sorted;
  }
  const cross = (o: { x: number; y: number }, a: { x: number; y: number }, b: { x: number; y: number }) =>
    (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x);
  const lower: { x: number; y: number }[] = [];
  for (const p of sorted) {
    while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], p) <= 0) {
      lower.pop();
    }
    lower.push(p);
  }
  const upper: { x: number; y: number }[] = [];
  for (let i = sorted.length - 1; i >= 0; i -= 1) {
    const p = sorted[i];
    while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], p) <= 0) {
      upper.pop();
    }
    upper.push(p);
  }
  lower.pop();
  upper.pop();
  return lower.concat(upper);
}

function rdp(points: { x: number; y: number }[], epsilon: number): { x: number; y: number }[] {
  if (points.length <= 4) {
    return points;
  }
  let maxDist = 0;
  let index = 0;
  const a = points[0];
  const b = points[points.length - 1];
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const mag = Math.hypot(dx, dy) || 1;
  for (let i = 1; i < points.length - 1; i += 1) {
    const p = points[i];
    const dist = Math.abs(dy * p.x - dx * p.y + b.x * a.y - b.y * a.x) / mag;
    if (dist > maxDist) {
      maxDist = dist;
      index = i;
    }
  }
  if (maxDist <= epsilon) {
    return [a, b];
  }
  const left = rdp(points.slice(0, index + 1), epsilon);
  const right = rdp(points.slice(index), epsilon);
  return left.slice(0, -1).concat(right);
}

function triangleArea(
  prev: { x: number; y: number },
  point: { x: number; y: number },
  next: { x: number; y: number },
): number {
  return Math.abs((point.x - prev.x) * (next.y - prev.y) - (point.y - prev.y) * (next.x - prev.x));
}

function reduceToQuad(hull: { x: number; y: number }[]): { x: number; y: number }[] | null {
  const pts = hull.map((p) => ({ x: p.x, y: p.y }));
  while (pts.length > 4) {
    let worst = 0;
    let minArea = Infinity;
    for (let i = 0; i < pts.length; i += 1) {
      const area = triangleArea(pts[(i + pts.length - 1) % pts.length], pts[i], pts[(i + 1) % pts.length]);
      if (area < minArea) {
        minArea = area;
        worst = i;
      }
    }
    pts.splice(worst, 1);
  }
  if (pts.length !== 4) {
    return null;
  }
  const area = triangleArea(pts[0], pts[1], pts[2]) + triangleArea(pts[0], pts[2], pts[3]);
  if (area < 64) {
    return null;
  }
  return orderCorners(pts);
}

function toQuad(hull: { x: number; y: number }[]): { x: number; y: number }[] | null {
  if (hull.length < 4) {
    return null;
  }
  const closed = hull.concat([hull[0]]);
  const perimeter = closed.reduce((sum, p, i) => {
    if (i === 0) {
      return 0;
    }
    const q = closed[i - 1];
    return sum + Math.hypot(p.x - q.x, p.y - q.y);
  }, 0);
  for (let step = 0; step < 18; step += 1) {
    const approx = rdp(closed, (0.5 + step) * (perimeter * 0.008));
    const pts = approx[0].x === approx[approx.length - 1].x && approx[0].y === approx[approx.length - 1].y
      ? approx.slice(0, -1)
      : approx;
    if (pts.length === 4) {
      return orderCorners(pts);
    }
  }
  return reduceToQuad(hull);
}

export function orderCorners(corners: { x: number; y: number }[]): { x: number; y: number }[] {
  const cx = corners.reduce((s, p) => s + p.x, 0) / corners.length;
  const cy = corners.reduce((s, p) => s + p.y, 0) / corners.length;
  const sorted = [...corners].sort((a, b) => Math.atan2(a.y - cy, a.x - cx) - Math.atan2(b.y - cy, b.x - cx));
  let start = 0;
  let best = Infinity;
  sorted.forEach((p, i) => {
    const score = p.x + p.y;
    if (score < best) {
      best = score;
      start = i;
    }
  });
  return [0, 1, 2, 3].map((i) => sorted[(start + i) % 4]);
}

function rotateCorners(corners: { x: number; y: number }[], rotation: number): { x: number; y: number }[] {
  const n = rotation % 4;
  return corners.map((_, i) => corners[(i + n) % 4]);
}

function samplePerspective(image: GrayImage, corners: { x: number; y: number }[], u: number, v: number): number {
  const x =
    corners[0].x * (1 - u) * (1 - v) +
    corners[1].x * u * (1 - v) +
    corners[2].x * u * v +
    corners[3].x * (1 - u) * v;
  const y =
    corners[0].y * (1 - u) * (1 - v) +
    corners[1].y * u * (1 - v) +
    corners[2].y * u * v +
    corners[3].y * (1 - u) * v;
  return grayAt(image, x, y);
}

function decodeQuad(image: GrayImage, corners: { x: number; y: number }[]): DetectedMarker | null {
  const n = ARUCO_SIZE + 2;
  const cells: number[][] = [];
  for (let y = 0; y < n; y += 1) {
    const row: number[] = [];
    for (let x = 0; x < n; x += 1) {
      const u = (x + 0.5) / n;
      const v = (y + 0.5) / n;
      row.push(samplePerspective(image, corners, u, v) < 110 ? 0 : 1);
    }
    cells.push(row);
  }
  for (let i = 0; i < n; i += 1) {
    if (cells[0][i] !== 0 || cells[n - 1][i] !== 0 || cells[i][0] !== 0 || cells[i][n - 1] !== 0) {
      return null;
    }
  }
  const bits: number[] = [];
  for (let y = 0; y < ARUCO_SIZE; y += 1) {
    for (let x = 0; x < ARUCO_SIZE; x += 1) {
      bits.push(cells[y + 1][x + 1]);
    }
  }
  const match = identifyBits(bits);
  if (!match) {
    return null;
  }
  return {
    id: match.id,
    rotation: match.rotation,
    corners: rotateCorners(corners, (4 - match.rotation) % 4),
  };
}

function blobQuads(binary: Uint8Array, width: number, height: number): { x: number; y: number }[][] {
  const seen = new Uint8Array(binary.length);
  const quads: { x: number; y: number }[][] = [];
  const minArea = Math.max(80, width * height * 0.0008);
  const maxArea = width * height * 0.45;
  for (let i = 0; i < binary.length; i += 1) {
    if (!binary[i] || seen[i]) {
      continue;
    }
    const stack = [i];
    seen[i] = 1;
    const border: { x: number; y: number }[] = [];
    let area = 0;
    while (stack.length) {
      const idx = stack.pop()!;
      area += 1;
      const x = idx % width;
      const y = (idx / width) | 0;
      let edge = false;
      if (x > 0) {
        const n = idx - 1;
        if (binary[n]) {
          if (!seen[n]) {
            seen[n] = 1;
            stack.push(n);
          }
        } else {
          edge = true;
        }
      } else {
        edge = true;
      }
      if (x + 1 < width) {
        const n = idx + 1;
        if (binary[n]) {
          if (!seen[n]) {
            seen[n] = 1;
            stack.push(n);
          }
        } else {
          edge = true;
        }
      } else {
        edge = true;
      }
      if (y > 0) {
        const n = idx - width;
        if (binary[n]) {
          if (!seen[n]) {
            seen[n] = 1;
            stack.push(n);
          }
        } else {
          edge = true;
        }
      } else {
        edge = true;
      }
      if (y + 1 < height) {
        const n = idx + width;
        if (binary[n]) {
          if (!seen[n]) {
            seen[n] = 1;
            stack.push(n);
          }
        } else {
          edge = true;
        }
      } else {
        edge = true;
      }
      if (edge) {
        border.push({ x, y });
      }
    }
    if (area < minArea || area > maxArea || border.length < 4) {
      continue;
    }
    const step = Math.max(1, Math.floor(border.length / 180));
    const sampled = border.filter((_, index) => index % step === 0);
    const hull = convexHull(sampled);
    const quad = toQuad(hull);
    if (quad) {
      quads.push(quad);
    }
  }
  return quads;
}

export function detectAruco(image: GrayImage): DetectedMarker[] {
  const binary = adaptiveBinary(image);
  const quads = blobQuads(binary, image.width, image.height);
  const found: DetectedMarker[] = [];
  const used = new Set<number>();
  for (const quad of quads) {
    const marker = decodeQuad(image, quad);
    if (!marker || used.has(marker.id)) {
      continue;
    }
    used.add(marker.id);
    found.push(marker);
  }
  return found;
}

export function detectArucoRgba(
  rgba: Uint8ClampedArray,
  width: number,
  height: number,
): DetectedMarker[] {
  const gray = rgbaToGray(rgba, width, height);
  const scaled = downsampleGray(gray);
  return detectAruco(scaled.image).map((marker) => ({
    ...marker,
    corners: marker.corners.map((corner) => ({
      x: corner.x * scaled.scale,
      y: corner.y * scaled.scale,
    })),
  }));
}

export function renderMarkerGray(id: number, pixelSize: number): GrayImage {
  const cells = 6;
  const cell = Math.max(4, Math.floor(pixelSize / cells));
  const width = cell * cells;
  const data = new Uint8Array(width * width);
  const grid = markerCells(id);
  for (let y = 0; y < width; y += 1) {
    const cy = Math.min(cells - 1, (y / cell) | 0);
    for (let x = 0; x < width; x += 1) {
      const cx = Math.min(cells - 1, (x / cell) | 0);
      data[y * width + x] = grid[cy][cx] ? 255 : 0;
    }
  }
  return { data, width, height: width };
}

export function placeMarker(canvas: GrayImage, marker: GrayImage, left: number, top: number): void {
  for (let y = 0; y < marker.height; y += 1) {
    const dy = top + y;
    if (dy < 0 || dy >= canvas.height) {
      continue;
    }
    for (let x = 0; x < marker.width; x += 1) {
      const dx = left + x;
      if (dx < 0 || dx >= canvas.width) {
        continue;
      }
      canvas.data[dy * canvas.width + dx] = marker.data[y * marker.width + x];
    }
  }
}
