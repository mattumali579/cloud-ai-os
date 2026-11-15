import { isPinching, pinchAnchor, type Point, type TrackedHand } from "../landmarks";

export const OPEN_HOLD_MS = 600;
export const SWAP_HOLD_MS = 320;
export const SWAP_COOLDOWN_MS = 750;
export const OPEN_STABLE = 0.038;
export const PINCH_THRESHOLD = 0.055;
export const DRAG_RADIUS = 0.09;
export const HAND_LOST_HOLD_MS = 700;

const TIPS = [8, 12, 16, 20] as const;
const PIPS = [6, 10, 14, 18] as const;

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

export function fingerSpan(hand: TrackedHand): number {
  const wrist = hand.landmarks[0];
  const middle = hand.landmarks[12];
  if (!wrist || !middle) {
    return 0.12;
  }
  return Math.hypot(wrist.x - middle.x, wrist.y - middle.y);
}

export function isOpenHand(hand: TrackedHand): boolean {
  const wrist = hand.landmarks[0];
  if (!wrist) {
    return false;
  }
  let extended = 0;
  for (let i = 0; i < TIPS.length; i += 1) {
    const tip = hand.landmarks[TIPS[i]];
    const pip = hand.landmarks[PIPS[i]];
    if (!tip || !pip) {
      continue;
    }
    const tipDist = Math.hypot(tip.x - wrist.x, tip.y - wrist.y);
    const pipDist = Math.hypot(pip.x - wrist.x, pip.y - wrist.y);
    if (tipDist > pipDist * 1.18) {
      extended += 1;
    }
  }
  const spread = Math.hypot(
    (hand.landmarks[8]?.x ?? 0) - (hand.landmarks[20]?.x ?? 0),
    (hand.landmarks[8]?.y ?? 0) - (hand.landmarks[20]?.y ?? 0),
  );
  return extended >= 4 && spread > 0.08 && !isPinching(hand.landmarks, PINCH_THRESHOLD);
}

export function openHands(hands: TrackedHand[]): TrackedHand[] {
  return hands.filter(isOpenHand);
}

export function bothPinching(hands: TrackedHand[]): boolean {
  if (hands.length < 2) {
    return false;
  }
  return hands.filter((hand) => isPinching(hand.landmarks, PINCH_THRESHOLD)).length >= 2;
}

export function pinchPoints(hands: TrackedHand[]): Point[] {
  return hands.filter((hand) => isPinching(hand.landmarks, PINCH_THRESHOLD)).map((hand) => pinchAnchor(hand.landmarks));
}
