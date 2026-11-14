export const FILTER_NAMES = ["thermal", "dual-tone"] as const;
export type FilterName = (typeof FILTER_NAMES)[number];

function gray(r: number, g: number, b: number): number {
  return 0.299 * r + 0.587 * g + 0.114 * b;
}

function jet(t: number): [number, number, number] {
  const r = Math.max(0, Math.min(1, 1.5 - Math.abs(4 * t - 3))) * 255;
  const g = Math.max(0, Math.min(1, 1.5 - Math.abs(4 * t - 2))) * 255;
  const b = Math.max(0, Math.min(1, 1.5 - Math.abs(4 * t - 1))) * 255;
  return [r, g, b];
}

export function applyFilter(data: ImageData, name: FilterName): void {
  const pixels = data.data;
  for (let i = 0; i < pixels.length; i += 4) {
    const y = gray(pixels[i], pixels[i + 1], pixels[i + 2]);
    if (name === "dual-tone") {
      if (y > 110) {
        pixels[i] = 255;
        pixels[i + 1] = 140;
        pixels[i + 2] = 10;
      } else {
        pixels[i] = 220;
        pixels[i + 1] = 30;
        pixels[i + 2] = 180;
      }
    } else {
      const color = jet(y / 255);
      pixels[i] = color[0];
      pixels[i + 1] = color[1];
      pixels[i + 2] = color[2];
    }
  }
}

export function nextFilter(name: FilterName): FilterName {
  const index = FILTER_NAMES.indexOf(name);
  return FILTER_NAMES[(index + 1) % FILTER_NAMES.length];
}
