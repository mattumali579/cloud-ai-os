export const THUMB_TIP = 4;
export const INDEX_TIP = 8;
export const WRIST = 0;

export const HAND_CONNECTIONS: ReadonlyArray<readonly [number, number]> = [
  [0, 1],
  [1, 2],
  [2, 3],
  [3, 4],
  [0, 5],
  [5, 6],
  [6, 7],
  [7, 8],
  [5, 9],
  [9, 10],
  [10, 11],
  [11, 12],
  [9, 13],
  [13, 14],
  [14, 15],
  [15, 16],
  [13, 17],
  [0, 17],
  [17, 18],
  [18, 19],
  [19, 20],
];

export type Point = { x: number; y: number; z: number };

export type Handed = "Left" | "Right";

export type TrackedHand = {
  landmarks: Point[];
  handedness: Handed;
  score: number;
};

export function pinchDistance(hand: Point[]): number {
  const thumb = hand[THUMB_TIP];
  const index = hand[INDEX_TIP];
  if (!thumb || !index) {
    return Number.POSITIVE_INFINITY;
  }
  return Math.hypot(thumb.x - index.x, thumb.y - index.y);
}

export function isPinching(hand: Point[], threshold: number): boolean {
  return pinchDistance(hand) <= threshold;
}

export function pinchAnchor(hand: Point[]): Point {
  const thumb = hand[THUMB_TIP];
  const index = hand[INDEX_TIP];
  if (!thumb || !index) {
    const wrist = hand[WRIST] ?? { x: 0.5, y: 0.5, z: 0 };
    return { ...wrist };
  }
  return {
    x: (thumb.x + index.x) / 2,
    y: (thumb.y + index.y) / 2,
    z: (thumb.z + index.z) / 2,
  };
}

export function mirrorPoint(point: Point, mirrored: boolean): Point {
  if (!mirrored) {
    return point;
  }
  return { x: 1 - point.x, y: point.y, z: point.z };
}

export function mirrorHand(hand: Point[], mirrored: boolean): Point[] {
  return hand.map((point) => mirrorPoint(point, mirrored));
}

export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

export function lerp(from: number, to: number, amount: number): number {
  return from + (to - from) * amount;
}

export function lerpAngle(from: number, to: number, amount: number): number {
  let delta = to - from;
  while (delta > Math.PI) {
    delta -= Math.PI * 2;
  }
  while (delta < -Math.PI) {
    delta += Math.PI * 2;
  }
  return from + delta * amount;
}
