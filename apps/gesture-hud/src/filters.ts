export const FILTER_NAMES = [
  "thermal",
  "magma",
  "ice",
  "night",
  "dual-tone",
  "neon",
  "gold",
  "matrix",
  "rainbow",
  "invert",
  "red",
  "sketch",
  "pixelate",
  "glitch",
  "edge",
] as const;
export type FilterName = (typeof FILTER_NAMES)[number];

function gray(r: number, g: number, b: number): number {
  return 0.299 * r + 0.587 * g + 0.114 * b;
}

function clampByte(value: number): number {
  return Math.max(0, Math.min(255, value));
}

function mix(a: [number, number, number], b: [number, number, number], t: number): [number, number, number] {
  return [
    a[0] + (b[0] - a[0]) * t,
    a[1] + (b[1] - a[1]) * t,
    a[2] + (b[2] - a[2]) * t,
  ];
}

function ramp(t: number, stops: [number, number, number][]): [number, number, number] {
  const scaled = Math.max(0, Math.min(0.999, t)) * (stops.length - 1);
  const index = Math.floor(scaled);
  return mix(stops[index], stops[index + 1], scaled - index);
}

function jet(t: number): [number, number, number] {
  const r = Math.max(0, Math.min(1, 1.5 - Math.abs(4 * t - 3))) * 255;
  const g = Math.max(0, Math.min(1, 1.5 - Math.abs(4 * t - 2))) * 255;
  const b = Math.max(0, Math.min(1, 1.5 - Math.abs(4 * t - 1))) * 255;
  return [r, g, b];
}

function neighbor(pixels: Uint8ClampedArray, width: number, height: number, x: number, y: number): number {
  const sx = Math.max(0, Math.min(width - 1, x));
  const sy = Math.max(0, Math.min(height - 1, y));
  const i = (sy * width + sx) * 4;
  return gray(pixels[i], pixels[i + 1], pixels[i + 2]);
}

export function applyFilter(data: ImageData, name: FilterName, now = 0): void {
  const pixels = data.data;
  const width = data.width || Math.max(1, pixels.length / 4);
  const height = data.height || 1;
  const source = new Uint8ClampedArray(pixels);

  for (let i = 0; i < pixels.length; i += 4) {
    const r = source[i];
    const g = source[i + 1];
    const b = source[i + 2];
    const y = gray(r, g, b);
    const t = y / 255;
    const x = (i / 4) % width;
    const row = Math.floor(i / 4 / width);
    let color: [number, number, number] = [r, g, b];

    switch (name) {
      case "thermal":
        color = jet(t);
        break;
      case "magma":
        color = ramp(t, [
          [0, 0, 4],
          [80, 18, 123],
          [188, 55, 84],
          [249, 142, 9],
          [252, 253, 191],
        ]);
        break;
      case "ice":
        color = ramp(t, [
          [4, 6, 40],
          [12, 70, 160],
          [20, 180, 220],
          [180, 245, 255],
          [255, 255, 255],
        ]);
        break;
      case "night":
        color = [y * 0.08, y * 1.15, y * 0.22];
        break;
      case "dual-tone":
        color = y > 110 ? [255, 140, 10] : [220, 30, 180];
        break;
      case "neon":
        color = y > 120 ? [0, 255, 220] : [255, 0, 180];
        break;
      case "gold":
        color = ramp(t, [
          [20, 6, 0],
          [140, 40, 8],
          [230, 160, 20],
          [255, 240, 160],
        ]);
        break;
      case "matrix":
        color = [0, y * 1.2, y * 0.18];
        break;
      case "rainbow": {
        const wave = (Math.sin((x + row) * 0.05 + now * 0.005) + 1) / 2;
        color = mix(jet(t), jet(wave), 0.65);
        break;
      }
      case "invert":
        color = [255 - r, 255 - g, 255 - b];
        break;
      case "red":
        color = [r, 0, 0];
        break;
      case "sketch": {
        const left = neighbor(source, width, height, x - 1, row);
        const right = neighbor(source, width, height, x + 1, row);
        const up = neighbor(source, width, height, x, row - 1);
        const down = neighbor(source, width, height, x, row + 1);
        const edge = Math.abs(y * 4 - left - right - up - down);
        const ink = clampByte(255 - y * 0.7 - edge * 1.8);
        color = [ink, ink, ink];
        break;
      }
      case "pixelate": {
        const block = 12;
        const bx = Math.floor(x / block) * block;
        const by = Math.floor(row / block) * block;
        const si = (by * width + bx) * 4;
        color = [source[si], source[si + 1], source[si + 2]];
        break;
      }
      case "glitch": {
        const shift = ((Math.floor(row / 7) % 2) * 10 + 6) * 4;
        const ri = Math.min(source.length - 4, i + shift);
        const bi = Math.max(0, i - shift);
        color = [source[ri], g, source[bi + 2]];
        break;
      }
      case "edge": {
        const gx =
          neighbor(source, width, height, x + 1, row) - neighbor(source, width, height, x - 1, row);
        const gy =
          neighbor(source, width, height, x, row + 1) - neighbor(source, width, height, x, row - 1);
        const mag = clampByte(Math.hypot(gx, gy) * 1.6);
        color = jet(mag / 255);
        break;
      }
    }

    pixels[i] = clampByte(color[0]);
    pixels[i + 1] = clampByte(color[1]);
    pixels[i + 2] = clampByte(color[2]);
  }
}

export function nextFilter(name: FilterName): FilterName {
  const index = FILTER_NAMES.indexOf(name);
  return FILTER_NAMES[(index + 1) % FILTER_NAMES.length];
}

export function prevFilter(name: FilterName): FilterName {
  const index = FILTER_NAMES.indexOf(name);
  return FILTER_NAMES[(index - 1 + FILTER_NAMES.length) % FILTER_NAMES.length];
}

export function labelFor(name: FilterName): string {
  return name.replace("-", " ").toUpperCase();
}
