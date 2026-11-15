import { clamp, lerp, type Point, type TrackedHand } from "../landmarks";
import {
  CLAW_HOLD_MS,
  classifyPalm,
  coverRadius,
  DEFAULT_EXPAND_MS,
  DRAG_RADIUS,
  FIST_COOLDOWN_MS,
  FIST_FADE_MS,
  FIST_HOLD_MS,
  HAND_LOST_HOLD_MS,
  isClosedFist,
  isCuppedUp,
  isHangingDown,
  isPowerHand,
  isShamblesArmedPose,
  isShamblesSwitchPose,
  isThreeDigit,
  pickActingHand,
  RASENGAN_LIFT,
  RASENGAN_MAX,
  RASENGAN_MIN,
  palmCenter,
  palmHang,
  pinchPoints,
  pulledBack,
  READY_GRACE_MS,
  READY_HOLD_MS,
  SWAP_COOLDOWN_MS,
  SWAP_DEBOUNCE_MS,
  type PalmCalibration,
  type PalmFacing,
} from "./handPose";

export type PowerKind = "none" | "room" | "rasengan";
export type ShamblesPhase = "idle" | "ready" | "rotate" | "swapped";

export type RoomProp = {
  id: "cube" | "sphere";
  label: string;
  x: number;
  y: number;
  depth: number;
};

export type RoomState = {
  power: PowerKind;
  open: boolean;
  holdMs: number;
  lastPalm: Point | null;
  lastPalmAt: number | null;
  center: Point;
  frozenCenter: Point | null;
  maxRadius: number;
  expandMs: number;
  expandElapsed: number;
  displayCenter: Point;
  displayRadius: number;
  openAmount: number;
  rasenganCenter: Point;
  rasenganRadius: number;
  rasenganAmount: number;
  props: RoomProp[];
  draggingId: RoomProp["id"] | null;
  shambles: ShamblesPhase;
  readyMs: number;
  swapHoldMs: number;
  swapCooldownUntil: number;
  lastReadyAt: number | null;
  lastArmedRollZ: number | null;
  fistMs: number;
  fistLock: boolean;
  fistCooldownUntil: number;
  dismissing: boolean;
  swapFlash: number;
  swapTrail: { from: Point; to: Point; otherFrom: Point; otherTo: Point } | null;
  status: string;
  palmFacing: PalmFacing;
  paused: boolean;
  calibration: PalmCalibration;
};

export const DEFAULT_PROPS: RoomProp[] = [
  { id: "cube", label: "BLUE CUBE", x: 0.38, y: 0.46, depth: 0.35 },
  { id: "sphere", label: "PINK SPHERE", x: 0.62, y: 0.52, depth: 0.62 },
];

export function createRoomState(calibration?: PalmCalibration): RoomState {
  return {
    power: "none",
    open: false,
    holdMs: 0,
    lastPalm: null,
    lastPalmAt: null,
    center: { x: 0.5, y: 0.48, z: 0 },
    frozenCenter: null,
    maxRadius: coverRadius(0.5, 0.48),
    expandMs: DEFAULT_EXPAND_MS,
    expandElapsed: 0,
    displayCenter: { x: 0.5, y: 0.48, z: 0 },
    displayRadius: 0,
    openAmount: 0,
    rasenganCenter: { x: 0.5, y: 0.42, z: 0 },
    rasenganRadius: 0.18,
    rasenganAmount: 0,
    props: DEFAULT_PROPS.map((prop) => ({ ...prop })),
    draggingId: null,
    shambles: "idle",
    readyMs: 0,
    swapHoldMs: 0,
    swapCooldownUntil: 0,
    lastReadyAt: null,
    lastArmedRollZ: null,
    fistMs: 0,
    fistLock: false,
    fistCooldownUntil: 0,
    dismissing: false,
    swapFlash: 0,
    swapTrail: null,
    status: "idle",
    palmFacing: "ambiguous",
    paused: false,
    calibration: calibration ?? { upY: null, downY: null },
  };
}

export function resetRoom(state: RoomState): RoomState {
  return {
    ...createRoomState(state.calibration),
    paused: state.paused,
    expandMs: state.expandMs,
  };
}

function near(a: Point, b: { x: number; y: number }, radius: number): boolean {
  return Math.hypot(a.x - b.x, a.y - b.y) <= radius;
}

export function swapProps(props: RoomProp[]): RoomProp[] {
  const cube = props.find((prop) => prop.id === "cube");
  const sphere = props.find((prop) => prop.id === "sphere");
  if (!cube || !sphere) {
    return props.map((prop) => ({ ...prop }));
  }
  const cubeSpot = { x: cube.x, y: cube.y, depth: cube.depth };
  const sphereSpot = { x: sphere.x, y: sphere.y, depth: sphere.depth };
  return props.map((prop) => {
    if (prop.id === "cube") {
      return { ...prop, ...sphereSpot };
    }
    if (prop.id === "sphere") {
      return { ...prop, ...cubeSpot };
    }
    return { ...prop };
  });
}

function fireSwap(state: RoomState, now: number): RoomState {
  const before = state.props.map((prop) => ({ ...prop }));
  const props = swapProps(state.props);
  const cubeBefore = before.find((prop) => prop.id === "cube") ?? before[0];
  const sphereBefore = before.find((prop) => prop.id === "sphere") ?? before[1];
  const cubeAfter = props.find((prop) => prop.id === "cube") ?? props[0];
  const sphereAfter = props.find((prop) => prop.id === "sphere") ?? props[1];
  return {
    ...state,
    props,
    shambles: "swapped",
    swapCooldownUntil: now + SWAP_COOLDOWN_MS,
    swapHoldMs: 0,
    readyMs: 0,
    swapFlash: 1,
    status: "SWAPPED",
    swapTrail: {
      from: { x: cubeBefore.x, y: cubeBefore.y, z: 0 },
      to: { x: cubeAfter.x, y: cubeAfter.y, z: 0 },
      otherFrom: { x: sphereBefore.x, y: sphereBefore.y, z: 0 },
      otherTo: { x: sphereAfter.x, y: sphereAfter.y, z: 0 },
    },
  };
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

  let next: RoomState = {
    ...state,
    props: state.props.map((prop) => ({ ...prop })),
    swapFlash: Math.max(0, state.swapFlash - dt / 280),
  };
  if (next.swapFlash <= 0) {
    next.swapTrail = null;
  }

  const hand = pickActingHand(hands);
  const palm = hand ? palmCenter(hand) : state.lastPalm;
  const frame = hand ? classifyPalm(hand, state.calibration) : { facing: "ambiguous" as const, pitchY: 0, rollZ: 0, confidence: 0 };
  next.palmFacing = frame.facing;
  const closedFist = Boolean(hand && isClosedFist(hand));
  const wantRoom = Boolean(
    hand && !closedFist && (isHangingDown(hand) || (isPowerHand(hand) && frame.facing === "down")),
  );
  const wantRasengan = Boolean(
    hand && !closedFist && (isCuppedUp(hand) || (isPowerHand(hand) && frame.facing === "up")),
  );
  if (hand && palm) {
    next.lastPalm = palm;
    next.lastPalmAt = now;
  } else if (state.lastPalmAt != null && now - state.lastPalmAt > HAND_LOST_HOLD_MS) {
    next.holdMs = 0;
    next.readyMs = 0;
    next.draggingId = null;
    if (next.shambles === "ready" || next.shambles === "rotate") {
      next.shambles = "idle";
    }
  }

  if (!hand) {
    next.fistMs = 0;
  }

  const powerActive = next.power === "room" || next.power === "rasengan";
  if (state.dismissing || (closedFist && powerActive && state.fistMs + dt >= FIST_HOLD_MS)) {
    next.dismissing = true;
    next.status = "CLOSING";
    next.shambles = "idle";
    next.draggingId = null;
    const fade = clamp(dt / FIST_FADE_MS, 0, 1);
    next.openAmount = state.openAmount * (1 - fade);
    next.rasenganAmount = state.rasenganAmount * (1 - fade);
    next.displayRadius = state.displayRadius * (1 - fade);
    if (next.openAmount < 0.05 && next.rasenganAmount < 0.05) {
      return {
        ...resetRoom(next),
        status: "CLOSED",
        fistLock: closedFist,
        fistCooldownUntil: now + FIST_COOLDOWN_MS,
      };
    }
    return next;
  }

  if (closedFist && powerActive) {
    next.fistMs = state.fistMs + dt;
    next.status = "CLOSING";
    return next;
  }
  next.fistMs = 0;

  if (!closedFist) {
    next.fistLock = false;
  }
  const blocked =
    next.fistLock || closedFist || now < next.fistCooldownUntil;

  if (next.power === "none" && !blocked && hand && (wantRoom || wantRasengan) && !(wantRoom && wantRasengan)) {
    const same =
      !state.lastPalm ||
      (palm != null && Math.hypot(palm.x - state.lastPalm.x, palm.y - state.lastPalm.y) < 0.12);
    next.holdMs = same ? state.holdMs + dt : Math.min(dt, state.holdMs * 0.5 + dt);
    next.status = wantRoom ? "ARMING ROOM" : "ARMING RASENGAN";
    if (next.holdMs >= CLAW_HOLD_MS && palm) {
      if (wantRoom) {
        next.power = "room";
        next.open = true;
        next.frozenCenter = { ...palm };
        next.center = { ...palm };
        next.maxRadius = coverRadius(palm.x, palm.y);
        next.expandElapsed = 0;
        next.status = "ROOM";
      } else {
        next.power = "rasengan";
        next.rasenganCenter = { x: palm.x, y: palm.y - RASENGAN_LIFT, z: palm.z };
        next.rasenganRadius = 0.18;
        next.status = "RASENGAN";
      }
      next.holdMs = 0;
    }
  } else if (next.power === "none") {
    next.holdMs = 0;
    if (blocked && closedFist) {
      next.status = "RELEASE FIST";
    } else if (hand && isThreeDigit(hand)) {
      next.status = "Create ROOM first";
    } else {
      next.status = hand && isPowerHand(hand) ? "CUPPED · need palm up or down" : "idle";
    }
  }

  if (next.power === "room") {
    next.expandElapsed =
      state.power === "room" ? Math.min(next.expandMs, state.expandElapsed + dt) : 0;
    const grow = next.expandElapsed / next.expandMs;
    const frozen = next.frozenCenter ?? next.center;
    next.displayCenter = frozen;
    next.center = frozen;
    next.displayRadius = next.maxRadius * grow;
    next.openAmount = 1;
    next.rasenganAmount = 0;
    next.open = true;

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
        const hit = next.props.find((item) => near(pinch, item, DRAG_RADIUS));
        next.draggingId = hit?.id ?? null;
      }
    } else {
      next.draggingId = null;
    }

    const three = Boolean(hand && isThreeDigit(hand));
    const armed = Boolean(hand && isShamblesArmedPose(hand, frame.facing, frame.rollZ));
    const switchPose = Boolean(hand && isShamblesSwitchPose(hand, frame.facing, frame.rollZ));
    const wasReady =
      next.shambles === "ready" ||
      next.shambles === "rotate" ||
      (state.lastReadyAt != null && now - state.lastReadyAt < READY_GRACE_MS);
    const flippedFromRoll =
      three &&
      state.lastArmedRollZ != null &&
      frame.rollZ - state.lastArmedRollZ > 0.18;
    const switching = Boolean(
      switchPose ||
        flippedFromRoll ||
        (three && wasReady && state.lastPalm && palm && pulledBack(state.lastPalm, palm)),
    );
    if (now < next.swapCooldownUntil) {
      next.status = "SHAMBLES COOLDOWN";
      if (!armed) {
        next.shambles = "idle";
      }
    } else if (armed) {
      next.readyMs = state.shambles === "ready" || state.shambles === "rotate" ? state.readyMs + dt : dt;
      next.lastReadyAt = now;
      next.lastArmedRollZ = frame.rollZ;
      if (next.readyMs >= READY_HOLD_MS) {
        next.shambles = "ready";
        next.status = "ARMED";
      } else {
        next.status = "SHAMBLES ARMING";
      }
      next.swapHoldMs = 0;
    } else if (wasReady && switching) {
      next.shambles = "rotate";
      next.swapHoldMs = state.swapHoldMs + dt;
      next.status = "ROTATE";
      if (next.swapHoldMs >= SWAP_DEBOUNCE_MS) {
        next = fireSwap(next, now);
      }
    } else if (next.shambles === "swapped" && !armed) {
      next.shambles = "idle";
      next.status = "ROOM";
    } else if (!three) {
      next.readyMs = 0;
      next.swapHoldMs = 0;
      if (next.shambles !== "swapped") {
        next.shambles = "idle";
        next.status = "ROOM";
      }
    }
  } else if (next.power === "rasengan") {
    const target = palm ?? next.rasenganCenter;
    const extraLift = hand ? Math.max(0, -palmHang(hand)) : 0;
    const lift = RASENGAN_LIFT + extraLift * 0.35;
    next.rasenganCenter = {
      x: lerp(state.rasenganCenter.x, target.x, 0.28),
      y: lerp(state.rasenganCenter.y, target.y - lift, 0.28),
      z: lerp(state.rasenganCenter.z, target.z, 0.28),
    };
    const palmSpan = hand
      ? Math.hypot(
          (hand.landmarks[5]?.x ?? 0) - (hand.landmarks[17]?.x ?? 0),
          (hand.landmarks[5]?.y ?? 0) - (hand.landmarks[17]?.y ?? 0),
        )
      : 0.16;
    const targetRadius = clamp(palmSpan * 1.7, RASENGAN_MIN, RASENGAN_MAX);
    next.rasenganRadius = lerp(state.rasenganRadius, targetRadius, 0.22);
    next.rasenganAmount = lerp(state.rasenganAmount, 1, 0.22);
    next.openAmount = 0;
    next.displayRadius = 0;
    next.status = "RASENGAN";
    if (hand && isThreeDigit(hand)) {
      next.status = "Create ROOM first";
    }
  } else {
    next.openAmount = lerp(state.openAmount, 0, 0.2);
    next.displayRadius = lerp(state.displayRadius, 0, 0.2);
    next.rasenganAmount = lerp(state.rasenganAmount, 0, 0.25);
  }

  return next;
}

export function forcePower(state: RoomState, power: PowerKind): RoomState {
  const next = resetRoom(state);
  if (power === "room") {
    const center = { x: 0.5, y: 0.48, z: 0 };
    return {
      ...next,
      power: "room",
      open: true,
      frozenCenter: center,
      center,
      maxRadius: coverRadius(center.x, center.y),
      status: "ROOM",
    };
  }
  if (power === "rasengan") {
    return {
      ...next,
      power: "rasengan",
      rasenganAmount: 1,
      rasenganRadius: 0.18,
      status: "RASENGAN",
    };
  }
  return next;
}
