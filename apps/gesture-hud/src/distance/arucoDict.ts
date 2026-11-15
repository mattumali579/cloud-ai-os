/** OpenCV DICT_4X4_50 rotation-0 byte pairs from predefined_dictionaries.hpp. */
const BYTES: ReadonlyArray<readonly [number, number]> = [
  [181, 50],
  [15, 154],
  [51, 45],
  [153, 70],
  [84, 158],
  [121, 205],
  [158, 46],
  [196, 242],
  [254, 218],
  [207, 86],
  [249, 145],
  [17, 167],
  [14, 183],
  [42, 15],
  [36, 177],
  [38, 62],
  [70, 101],
  [102, 0],
  [108, 94],
  [118, 175],
  [134, 139],
  [176, 43],
  [204, 213],
  [221, 130],
  [254, 71],
  [148, 113],
  [172, 228],
  [165, 84],
  [33, 35],
  [52, 111],
  [68, 21],
  [87, 178],
  [158, 207],
  [240, 203],
  [8, 174],
  [9, 41],
  [24, 117],
  [4, 255],
  [13, 246],
  [28, 90],
  [23, 24],
  [42, 40],
  [50, 140],
  [56, 178],
  [36, 232],
  [46, 235],
  [45, 63],
  [75, 100],
  [80, 46],
  [80, 19],
];

export const ARUCO_SIZE = 4;
export const ARUCO_MAX_CORRECTION = 1;

export function bitsFromBytes(b0: number, b1: number): number[] {
  const bits: number[] = [];
  for (let i = 0; i < 8; i += 1) {
    bits.push((b0 >> (7 - i)) & 1);
  }
  for (let i = 0; i < 8; i += 1) {
    bits.push((b1 >> (7 - i)) & 1);
  }
  return bits;
}

export function bytesFromBits(bits: number[]): [number, number] {
  let b0 = 0;
  let b1 = 0;
  for (let i = 0; i < 8; i += 1) {
    b0 = (b0 << 1) | (bits[i] ? 1 : 0);
    b1 = (b1 << 1) | (bits[8 + i] ? 1 : 0);
  }
  return [b0, b1];
}

export function gridFromBits(bits: number[]): number[][] {
  const grid: number[][] = [];
  for (let y = 0; y < ARUCO_SIZE; y += 1) {
    grid.push(bits.slice(y * ARUCO_SIZE, y * ARUCO_SIZE + ARUCO_SIZE));
  }
  return grid;
}

export function bitsFromGrid(grid: number[][]): number[] {
  return grid.flat();
}

export function rotateGridCCW(grid: number[][]): number[][] {
  const n = grid.length;
  const next: number[][] = Array.from({ length: n }, () => Array<number>(n).fill(0));
  for (let y = 0; y < n; y += 1) {
    for (let x = 0; x < n; x += 1) {
      next[n - 1 - x][y] = grid[y][x];
    }
  }
  return next;
}

export function markerGrid(id: number): number[][] {
  const pair = BYTES[id];
  if (!pair) {
    throw new Error(`ArUco 4x4_50 has no id ${id}`);
  }
  return gridFromBits(bitsFromBytes(pair[0], pair[1]));
}

export function hamming(a: number[], b: number[]): number {
  let d = 0;
  for (let i = 0; i < a.length; i += 1) {
    if (a[i] !== b[i]) {
      d += 1;
    }
  }
  return d;
}

export function identifyBits(bits: number[]): { id: number; rotation: number } | null {
  let best: { id: number; rotation: number; dist: number } | null = null;
  for (let id = 0; id < BYTES.length; id += 1) {
    let grid = gridFromBits(bitsFromBytes(BYTES[id][0], BYTES[id][1]));
    for (let rotation = 0; rotation < 4; rotation += 1) {
      const dist = hamming(bits, bitsFromGrid(grid));
      if (!best || dist < best.dist) {
        best = { id, rotation, dist };
      }
      grid = rotateGridCCW(grid);
    }
  }
  if (!best || best.dist > ARUCO_MAX_CORRECTION) {
    return null;
  }
  return { id: best.id, rotation: best.rotation };
}

export function markerCells(id: number): number[][] {
  const inner = markerGrid(id);
  const n = ARUCO_SIZE + 2;
  const cells = Array.from({ length: n }, () => Array<number>(n).fill(0));
  for (let y = 0; y < ARUCO_SIZE; y += 1) {
    for (let x = 0; x < ARUCO_SIZE; x += 1) {
      cells[y + 1][x + 1] = inner[y][x];
    }
  }
  return cells;
}
