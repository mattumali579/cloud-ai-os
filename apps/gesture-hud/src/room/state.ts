import { clamp, lerp, type Point, type TrackedHand } from "../landmarks";
import {
  bothPinching,
  DRAG_RADIUS,
  fingerSpan,
  HAND_LOST_HOLD_MS,
  OPEN_HOLD_MS,
  OPEN_STABLE,
  openHands,
  palmCenter,
  pinchPoints,
  SWAP_COOLDOWN_MS,
  SWAP_HOLD_MS,
} from "./handPose";

export type RoomProp = {
  id: "cube" | "sphere";
  label: string;
  x: number;
  y: number;
  depth: number;
};

export type RoomState = {
  open: boolean;
  holdMs: number;
  lastPalm: Point | null;
  lastPalmAt: number | null;
  center: Point;
  radius: number;
  displayCenter: Point;
  displayRadius: number;
  openAmount: number;
  props: RoomProp[];
  draggingId: RoomProp["id"] | null;
  swapHoldMs: number;
  swapCooldownUntil: number;
  swapFlash: number;
  swapTrail: { from: Point; to: Point; otherFrom: Point; otherTo: Point } | null;
  paused: boolean;
};

export const DEFAULT_PROPS: RoomProp[] = [
  { id: "cube", label: "BLUE CUBE", x: 0.38, y: 0.46, depth: 0.35 },
  { id: "sphere", label: "PINK SPHERE", x: 0.62, y: 0.52, depth: 0.62 },
];

export function createRoomState(): RoomState {
  return {
    open: false,
    holdMs: 0,
    lastPalm: null,
    lastPalmAt: null,
    center: { x: 0.5, y: 0.48, z: 0 },
    radius: 0.42,
    displayCenter: { x: 0.5, y: 0.48, z: 0 },
    displayRadius: 0,
    openAmount: 0,
    props: DEFAULT_PROPS.map((prop) => ({ ...prop })),
    draggingId: null,
    swapHoldMs: 0,
    swapCooldownUntil: 0,
    swapFlash: 0,
    swapTrail: null,
    paused: false,
  };
}

export function resetRoom(state: RoomState): RoomState {
  return {
    ...createRoomState(),
    paused: state.paused,
  };
}

function near(a: Point, b: { x: number; y: number }, radius: number): boolean {
  return Math.hypot(a.x - b.x, a.y - b.y) <= radius;
}

function clampRadius(value: number): number {
  return clamp(value, 0.26, 0.62);
}

export function swapProps(props: RoomProp[]): RoomProp[] {
  if (props.length < 2) {
    return props.map((prop) => ({ ...prop }));
  }
  const a = props[0];
  const b = props[1];
  return [
    { ...a, x: b.x, y: b.y, depth: b.depth },
    { ...b, x: a.x, y: a.y, depth: a.depth },
  ];
}

export function stepRoom(
  state: RoomState,
  hands: TrackedHand[],
  now: number,
  dt: number,
): RoomState {
  if (state.paused) {
    return { ...state, props: state.props.map((prop) => ({ ...prop })) };
  }

  const next: RoomState = {
    ...state,
    props: state.props.map((prop) => ({ ...prop })),
    swapFlash: Math.max(0, state.swapFlash - dt / 280),
  };
  if (next.swapFlash <= 0) {
    next.swapTrail = null;
  }

  const opened = openHands(hands);
  const palmHand = opened[0] ?? null;
  if (palmHand) {
    const palm = palmCenter(palmHand);
    const stable =
      !state.lastPalm ||
      Math.hypot(palm.x - state.lastPalm.x, palm.y - state.lastPalm.y) < OPEN_STABLE;
    next.lastPalm = palm;
    next.lastPalmAt = now;
    next.holdMs = stable ? state.holdMs + dt : 0;
    next.center = palm;
    next.radius = clampRadius(fingerSpan(palmHand) * 3.6);
    if (!state.open && next.holdMs >= OPEN_HOLD_MS) {
      next.open = true;
    }
  } else if (state.lastPalmAt != null && now - state.lastPalmAt < HAND_LOST_HOLD_MS) {
    next.holdMs = state.holdMs;
  } else {
    next.holdMs = 0;
    next.draggingId = null;
  }

  const targetOpen = next.open ? 1 : 0;
  next.openAmount = lerp(state.openAmount, targetOpen, next.open ? 0.18 : 0.12);
  next.displayCenter = {
    x: lerp(state.displayCenter.x, next.center.x, 0.22),
    y: lerp(state.displayCenter.y, next.center.y, 0.22),
    z: lerp(state.displayCenter.z, next.center.z, 0.22),
  };
  next.displayRadius = lerp(state.displayRadius, next.open ? next.radius : 0, 0.16);

  if (!next.open) {
    next.swapHoldMs = 0;
    next.draggingId = null;
    return next;
  }

  const pinches = pinchPoints(hands);
  if (pinches.length === 1) {
    const pinch = pinches[0];
    if (next.draggingId) {
      const prop = next.props.find((item) => item.id === next.draggingId);
      if (prop) {
        prop.x = pinch.x;
        prop.y = pinch.y;
      }
    } else {
      const hit = next.props.find((prop) => near(pinch, prop, DRAG_RADIUS));
      next.draggingId = hit?.id ?? null;
    }
  } else {
    next.draggingId = null;
  }

  const canSwap = now >= next.swapCooldownUntil && !next.draggingId;
  if (canSwap && bothPinching(hands)) {
    next.swapHoldMs = state.swapHoldMs + dt;
    if (next.swapHoldMs >= SWAP_HOLD_MS && state.swapHoldMs < SWAP_HOLD_MS) {
      const before = next.props.map((prop) => ({ ...prop }));
      next.props = swapProps(next.props);
      next.swapCooldownUntil = now + SWAP_COOLDOWN_MS;
      next.swapHoldMs = 0;
      next.swapFlash = 1;
      next.swapTrail = {
        from: { x: before[0].x, y: before[0].y, z: 0 },
        to: { x: next.props[0].x, y: next.props[0].y, z: 0 },
        otherFrom: { x: before[1].x, y: before[1].y, z: 0 },
        otherTo: { x: next.props[1].x, y: next.props[1].y, z: 0 },
      };
    }
  } else if (!bothPinching(hands)) {
    next.swapHoldMs = 0;
  }

  return next;
}
