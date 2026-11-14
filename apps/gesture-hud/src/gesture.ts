import {
  clamp,
  isPinching,
  lerp,
  lerpAngle,
  pinchAnchor,
  pinchDistance,
  type Point,
  type TrackedHand,
} from "./landmarks";

export type GripStatus = "idle" | "one-hand" | "two-hands" | "gripping";

export type PanelPose = {
  cx: number;
  cy: number;
  width: number;
  height: number;
  rotation: number;
  tilt: number;
};

export type GestureConfig = {
  pinchThreshold: number;
  minWidth: number;
  maxWidth: number;
  minHeight: number;
  maxHeight: number;
  aspect: number;
  smooth: number;
  lostMs: number;
};

export const DEFAULT_PANEL: PanelPose = {
  cx: 0.5,
  cy: 0.48,
  width: 0.46,
  height: 0.28,
  rotation: 0,
  tilt: 0.12,
};

export const DEFAULT_GESTURE: GestureConfig = {
  pinchThreshold: 0.055,
  minWidth: 0.18,
  maxWidth: 0.92,
  minHeight: 0.12,
  maxHeight: 0.72,
  aspect: 16 / 9,
  smooth: 0.32,
  lostMs: 1000,
};

export type GestureState = {
  panel: PanelPose;
  displayPanel: PanelPose;
  status: GripStatus;
  gripping: boolean;
  surface: number;
  lastSeenAt: number | null;
  indicatorsVisible: boolean;
  fade: number;
};

export function createGestureState(now = 0): GestureState {
  return {
    panel: { ...DEFAULT_PANEL },
    displayPanel: { ...DEFAULT_PANEL },
    status: "idle",
    gripping: false,
    surface: 0,
    lastSeenAt: now || null,
    indicatorsVisible: false,
    fade: 0,
  };
}

export function resetPanel(state: GestureState): GestureState {
  return {
    ...state,
    panel: { ...DEFAULT_PANEL },
    displayPanel: { ...DEFAULT_PANEL },
    gripping: false,
    surface: 0,
    status: state.status === "idle" ? "idle" : state.status,
  };
}

function handedAnchor(hands: TrackedHand[]): { left?: Point; right?: Point } {
  const byX = [...hands].sort((a, b) => pinchAnchor(a.landmarks).x - pinchAnchor(b.landmarks).x);
  if (byX.length === 0) {
    return {};
  }
  if (byX.length === 1) {
    const label = byX[0].handedness;
    const point = pinchAnchor(byX[0].landmarks);
    return label === "Left" ? { left: point } : { right: point };
  }
  return {
    left: pinchAnchor(byX[0].landmarks),
    right: pinchAnchor(byX[byX.length - 1].landmarks),
  };
}

export function poseFromAnchors(left: Point, right: Point, config: GestureConfig): PanelPose {
  const dx = right.x - left.x;
  const dy = right.y - left.y;
  const span = Math.hypot(dx, dy);
  const width = clamp(span * 1.08, config.minWidth, config.maxWidth);
  const height = clamp(width / config.aspect, config.minHeight, config.maxHeight);
  const rotation = Math.atan2(dy, dx);
  const tilt = clamp((left.z - right.z) * 2.4 + dy * 1.6, -0.72, 0.72);
  return {
    cx: clamp((left.x + right.x) / 2, width / 2 + 0.02, 1 - width / 2 - 0.02),
    cy: clamp((left.y + right.y) / 2, height / 2 + 0.04, 1 - height / 2 - 0.04),
    width,
    height,
    rotation,
    tilt,
  };
}

export function smoothPose(from: PanelPose, to: PanelPose, amount: number): PanelPose {
  const t = clamp(amount, 0, 1);
  return {
    cx: lerp(from.cx, to.cx, t),
    cy: lerp(from.cy, to.cy, t),
    width: lerp(from.width, to.width, t),
    height: lerp(from.height, to.height, t),
    rotation: lerpAngle(from.rotation, to.rotation, t),
    tilt: lerp(from.tilt, to.tilt, t),
  };
}

export function stepGesture(
  state: GestureState,
  hands: TrackedHand[],
  now: number,
  config: GestureConfig = DEFAULT_GESTURE,
): GestureState {
  const next: GestureState = {
    ...state,
    panel: { ...state.panel },
    displayPanel: { ...state.displayPanel },
  };
  const release = config.pinchThreshold * 1.7;
  const closed = hands.filter((hand) => {
    const distance = pinchDistance(hand.landmarks);
    return state.gripping ? distance <= release : isPinching(hand.landmarks, config.pinchThreshold);
  });

  if (hands.length === 0) {
    next.status = "idle";
    next.gripping = false;
    next.surface = lerp(state.surface, 0, 0.45);
    if (state.lastSeenAt != null && now - state.lastSeenAt > config.lostMs) {
      next.indicatorsVisible = false;
      next.fade = lerp(state.fade, 0, 0.2);
    } else {
      next.fade = lerp(state.fade, state.indicatorsVisible ? 0.35 : 0, 0.12);
    }
    next.displayPanel = next.panel;
    return next;
  }

  next.lastSeenAt = now;
  next.indicatorsVisible = true;
  next.fade = lerp(state.fade, 1, 0.35);

  if (closed.length >= 2) {
    const { left, right } = handedAnchor(closed);
    if (left && right) {
      next.status = "gripping";
      next.gripping = true;
      next.surface = lerp(state.surface, 1, 0.5);
      next.panel = poseFromAnchors(left, right, config);
      next.displayPanel = smoothPose(state.displayPanel, next.panel, config.smooth);
      return next;
    }
  }

  next.gripping = false;
  next.surface = lerp(state.surface, 0, 0.5);
  next.status = hands.length >= 2 ? "two-hands" : "one-hand";
  next.displayPanel = next.panel;
  return next;
}
