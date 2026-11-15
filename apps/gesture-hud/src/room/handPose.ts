import { clamp, isPinching, pinchAnchor, type Point, type TrackedHand } from "../landmarks";

export const CLAW_HOLD_MS = 480;
export const READY_HOLD_MS = 280;
export const SWAP_DEBOUNCE_MS = 140;
export const SWAP_COOLDOWN_MS = 800;
export const READY_GRACE_MS = 700;
export const HAND_LOST_HOLD_MS = 700;
export const DRAG_RADIUS = 0.09;
export const PINCH_THRESHOLD = 0.055;
export const DEFAULT_EXPAND_MS = 1800;
export const RASENGAN_MIN = 0.12;
export const RASENGAN_MAX = 0.22;
export const RASENGAN_LIFT = 0.2;
export const FIST_HOLD_MS = 400;
export const FIST_FADE_MS = 280;
export const FIST_COOLDOWN_MS = 450;

export type PalmFacing = "up" | "down" | "camera" | "user" | "ambiguous";

export type PalmCalibration = {
  upY: number | null;
  downY: number | null;
};

export type PalmFrame = {
  facing: PalmFacing;
  pitchY: number;
  rollZ: number;
  confidence: number;
};

function pt(hand: TrackedHand, index: number, world = false): Point | undefined {
  return (world ? hand.worldLandmarks : hand.landmarks)?.[index];
}

function sub(a: Point, b: Point): Point {
  return { x: a.x - b.x, y: a.y - b.y, z: a.z - b.z };
}

function cross(a: Point, b: Point): Point {
  return {
    x: a.y * b.z - a.z * b.y,
    y: a.z * b.x - a.x * b.z,
    z: a.x * b.y - a.y * b.x,
  };
}

function mag(a: Point): number {
  return Math.hypot(a.x, a.y, a.z);
}

function dist(a: Point, b: Point): number {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

export function palmCenter(hand: TrackedHand): Point {
  const ids = [0, 5, 9, 13, 17];
  const pts = ids.map((id) => hand.landmarks[id]).filter(Boolean);
  const n = pts.length || 1;
  return {
    x: pts.reduce((s, p) => s + p.x, 0) / n,
    y: pts.reduce((s, p) => s + p.y, 0) / n,
    z: pts.reduce((s, p) => s + p.z, 0) / n,
  };
}

export function fingerCurl(hand: TrackedHand, tip: number, pip: number, mcp: number): number {
  const t = hand.landmarks[tip];
  const p = hand.landmarks[pip];
  const m = hand.landmarks[mcp];
  if (!t || !p || !m) {
    return 0;
  }
  const a = { x: m.x - p.x, y: m.y - p.y, z: m.z - p.z };
  const b = { x: t.x - p.x, y: t.y - p.y, z: t.z - p.z };
  const denom = mag(a) * mag(b) + 1e-6;
  const cos = clamp((a.x * b.x + a.y * b.y + a.z * b.z) / denom, -1, 1);
  return (cos + 1) / 2;
}

export function isFingerExtended(hand: TrackedHand, tip: number, pip: number, mcp: number): boolean {
  return fingerCurl(hand, tip, pip, mcp) < 0.38;
}

export function isFingerFolded(hand: TrackedHand, tip: number, pip: number, mcp: number): boolean {
  return fingerCurl(hand, tip, pip, mcp) > 0.42;
}

export function fingerSpread(hand: TrackedHand): number {
  const index = hand.landmarks[8];
  const pinky = hand.landmarks[20];
  if (!index || !pinky) {
    return 0;
  }
  return dist(index, pinky);
}

export function isOpenPalm(hand: TrackedHand): boolean {
  return (
    isFingerExtended(hand, 8, 6, 5) &&
    isFingerExtended(hand, 12, 10, 9) &&
    isFingerExtended(hand, 16, 14, 13) &&
    isFingerExtended(hand, 20, 18, 17) &&
    fingerSpread(hand) > 0.18
  );
}

export function meanFingerCurl(hand: TrackedHand): number {
  const curls = [
    fingerCurl(hand, 8, 6, 5),
    fingerCurl(hand, 12, 10, 9),
    fingerCurl(hand, 16, 14, 13),
    fingerCurl(hand, 20, 18, 17),
  ];
  return curls.reduce((s, v) => s + v, 0) / curls.length;
}

export function isThumbAcrossFist(hand: TrackedHand): boolean {
  const thumb = hand.landmarks[4];
  const indexPip = hand.landmarks[6];
  const middlePip = hand.landmarks[10];
  const indexMcp = hand.landmarks[5];
  const indexTip = hand.landmarks[8];
  if (!thumb || !indexPip || !middlePip || !indexMcp || !indexTip) {
    return false;
  }
  const across = Math.min(dist(thumb, indexPip), dist(thumb, middlePip), dist(thumb, indexMcp));
  return across < 0.12 && dist(thumb, indexTip) < 0.16;
}

export function isClosedFist(hand: TrackedHand): boolean {
  if (isThreeDigit(hand) || isOpenPalm(hand)) {
    return false;
  }
  const folded =
    isFingerFolded(hand, 8, 6, 5) &&
    isFingerFolded(hand, 12, 10, 9) &&
    isFingerFolded(hand, 16, 14, 13) &&
    isFingerFolded(hand, 20, 18, 17);
  const palm = palmCenter(hand);
  const tips = [8, 12, 16, 20].map((id) => hand.landmarks[id]).filter(Boolean) as Point[];
  const meanTip = tips.length ? tips.reduce((s, tip) => s + dist(tip, palm), 0) / tips.length : 1;
  const hanging = palmHang(hand) > 0.04;
  return (
    folded &&
    isThumbAcrossFist(hand) &&
    !hanging &&
    meanFingerCurl(hand) > 0.48 &&
    fingerSpread(hand) < 0.16 &&
    meanTip < 0.13
  );
}

export function isClaw(hand: TrackedHand): boolean {
  if (isClosedFist(hand) || isThreeDigit(hand) || isOpenPalm(hand) || isPinching(hand.landmarks, PINCH_THRESHOLD)) {
    return false;
  }
  const mean = meanFingerCurl(hand);
  return mean > 0.34 && mean < 0.97;
}

export function isCupped(hand: TrackedHand): boolean {
  if (isClosedFist(hand) || isThreeDigit(hand) || isOpenPalm(hand) || isPinching(hand.landmarks, PINCH_THRESHOLD)) {
    return false;
  }
  const mean = meanFingerCurl(hand);
  return mean > 0.12 && mean < 0.7 && fingerSpread(hand) < 0.22;
}

export function isPowerHand(hand: TrackedHand): boolean {
  return isClaw(hand) || isCupped(hand) || isHangingDown(hand) || isCuppedUp(hand);
}

export function palmHang(hand: TrackedHand): number {
  const palm = palmCenter(hand);
  const tips = [8, 12, 16, 20].map((id) => hand.landmarks[id]).filter(Boolean) as Point[];
  if (tips.length === 0) {
    return 0;
  }
  const meanTipY = tips.reduce((s, p) => s + p.y, 0) / tips.length;
  return meanTipY - palm.y;
}

export function isHangingDown(hand: TrackedHand): boolean {
  return !isClosedFist(hand) && !isThreeDigit(hand) && !isOpenPalm(hand) && palmHang(hand) > 0.03;
}

export function isCuppedUp(hand: TrackedHand): boolean {
  return !isClosedFist(hand) && !isThreeDigit(hand) && !isOpenPalm(hand) && palmHang(hand) < -0.03 && fingerSpread(hand) < 0.28;
}

export function pickActingHand(hands: TrackedHand[]): TrackedHand | null {
  if (hands.length === 0) {
    return null;
  }
  const ranked = [...hands].sort((a, b) => {
    const score = (hand: TrackedHand) =>
      (isClosedFist(hand) ? 5 : 0) +
      (isThreeDigit(hand) ? 4 : 0) +
      (isHangingDown(hand) ? 3 : 0) +
      (isCuppedUp(hand) ? 3 : 0) +
      (isPowerHand(hand) ? 2 : 0) +
      hand.score;
    return score(b) - score(a);
  });
  return ranked[0];
}

function fingerReach(hand: TrackedHand, tip: number, mcp: number): number {
  const wrist = hand.landmarks[0];
  const t = hand.landmarks[tip];
  const m = hand.landmarks[mcp];
  if (!wrist || !t || !m) {
    return 0;
  }
  return dist(t, wrist) / (dist(m, wrist) + 1e-6);
}

export function isThumbExtended(hand: TrackedHand): boolean {
  const thumb = hand.landmarks[4];
  const index = hand.landmarks[8];
  const wrist = hand.landmarks[0];
  const mcp = hand.landmarks[2] ?? hand.landmarks[1];
  if (!thumb || !index || !wrist || !mcp) {
    return false;
  }
  return dist(thumb, index) > 0.045 && dist(thumb, wrist) > dist(mcp, wrist) * 1.08;
}

export function isThreeDigit(hand: TrackedHand): boolean {
  const indexOut = isFingerExtended(hand, 8, 6, 5) || fingerReach(hand, 8, 5) > 1.35;
  const middleOut = isFingerExtended(hand, 12, 10, 9) || fingerReach(hand, 12, 9) > 1.35;
  const ringIn = isFingerFolded(hand, 16, 14, 13) || fingerReach(hand, 16, 13) < fingerReach(hand, 8, 5) * 0.78;
  const pinkyIn = isFingerFolded(hand, 20, 18, 17) || fingerReach(hand, 20, 17) < fingerReach(hand, 8, 5) * 0.78;
  return Boolean(isThumbExtended(hand) && indexOut && middleOut && ringIn && pinkyIn);
}

export function isShamblesArmedPose(hand: TrackedHand, facing: PalmFacing, rollZ = 0): boolean {
  return isThreeDigit(hand) && facing !== "user" && (facing === "camera" || rollZ < -0.1);
}

export function isShamblesSwitchPose(hand: TrackedHand, facing: PalmFacing, rollZ = 0): boolean {
  return isThreeDigit(hand) && (facing === "user" || rollZ > 0.1);
}

export function palmNormal(hand: TrackedHand): Point {
  const useWorld = Boolean(hand.worldLandmarks && hand.worldLandmarks.length >= 18);
  const wrist = pt(hand, 0, useWorld);
  const index = pt(hand, 5, useWorld);
  const pinky = pt(hand, 17, useWorld);
  if (!wrist || !index || !pinky) {
    return { x: 0, y: 0, z: -1 };
  }
  let n = cross(sub(index, wrist), sub(pinky, wrist));
  const len = mag(n) || 1;
  n = { x: n.x / len, y: n.y / len, z: n.z / len };
  if (hand.handedness === "Left") {
    n = { x: -n.x, y: -n.y, z: -n.z };
  }
  return n;
}

export function classifyPalm(hand: TrackedHand, calibration: PalmCalibration = { upY: null, downY: null }): PalmFrame {
  const n = palmNormal(hand);
  const tips = [8, 12, 16, 20]
    .map((id) => hand.landmarks[id])
    .filter(Boolean) as Point[];
  const mcps = [5, 9, 13, 17]
    .map((id) => hand.landmarks[id])
    .filter(Boolean) as Point[];
  const tipZ = tips.reduce((s, p) => s + p.z, 0) / (tips.length || 1);
  const mcpZ = mcps.reduce((s, p) => s + p.z, 0) / (mcps.length || 1);
  const fingersAtCamera = tipZ < mcpZ - 0.012;
  let downCut = 0.32;
  let upCut = -0.32;
  if (calibration.upY != null && calibration.downY != null) {
    const mid = (calibration.upY + calibration.downY) / 2;
    downCut = mid + 0.08;
    upCut = mid - 0.08;
  }
  const palm = palmCenter(hand);
  const meanTipY =
    ([8, 12, 16, 20].map((id) => hand.landmarks[id]).filter(Boolean) as Point[]).reduce((s, p) => s + p.y, 0) / 4;
  const hang = meanTipY - palm.y;
  let facing: PalmFacing = "ambiguous";
  if (isThreeDigit(hand)) {
    if (n.z < -0.12) {
      facing = "camera";
    } else if (n.z > 0.12) {
      facing = "user";
    } else if (fingersAtCamera) {
      facing = "camera";
    }
  } else if (hang > 0.02) {
    facing = "down";
  } else if (hang < -0.02) {
    facing = "up";
  } else if (n.y > downCut && Math.abs(n.y) >= Math.abs(n.z) * 0.7) {
    facing = "down";
  } else if (n.y < upCut && Math.abs(n.y) >= Math.abs(n.z) * 0.7) {
    facing = "up";
  } else if (n.z < -0.35 || (fingersAtCamera && n.z < 0 && Math.abs(n.z) > Math.abs(n.y))) {
    facing = "camera";
  } else if (n.z > 0.28 && Math.abs(n.z) > Math.abs(n.y)) {
    facing = "user";
  }
  const confidence = Math.max(Math.abs(n.y), Math.abs(n.z));
  return { facing, pitchY: n.y, rollZ: n.z, confidence };
}

export function fingersTowardCamera(hand: TrackedHand): boolean {
  const tips = [4, 8, 12].map((id) => hand.landmarks[id]).filter(Boolean) as Point[];
  const mcps = [1, 5, 9].map((id) => hand.landmarks[id]).filter(Boolean) as Point[];
  if (tips.length < 3 || mcps.length < 3) {
    return false;
  }
  const tipZ = tips.reduce((s, p) => s + p.z, 0) / 3;
  const mcpZ = mcps.reduce((s, p) => s + p.z, 0) / 3;
  return tipZ < mcpZ - 0.01;
}

export function coverRadius(cx: number, cy: number): number {
  return Math.max(Math.hypot(cx, cy), Math.hypot(1 - cx, cy), Math.hypot(cx, 1 - cy), Math.hypot(1 - cx, 1 - cy)) * 1.08;
}

export function pinchPoints(hands: TrackedHand[]): Point[] {
  return hands.filter((hand) => isPinching(hand.landmarks, PINCH_THRESHOLD)).map((hand) => pinchAnchor(hand.landmarks));
}

export function pulledBack(from: Point, to: Point): boolean {
  return to.z - from.z > 0.045 || dist(from, to) > 0.11;
}
