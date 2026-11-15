import { describe, expect, it } from "vitest";
import type { Point, TrackedHand } from "./landmarks";
import {
  CLAW_HOLD_MS,
  classifyPalm,
  coverRadius,
  isClaw,
  isFingerExtended,
  isFingerFolded,
  isOpenPalm,
  isThreeDigit,
  READY_HOLD_MS,
  SWAP_COOLDOWN_MS,
  SWAP_DEBOUNCE_MS,
} from "./room/handPose";
import { createRoomState, stepRoom, swapProps } from "./room/state";
import { FILTER_NAMES, nextFilter } from "./filters";

function tip(x: number, y: number, z = 0): Point {
  return { x, y, z };
}

function baseHand(handedness: TrackedHand["handedness"] = "Right"): TrackedHand {
  const landmarks: Point[] = Array.from({ length: 21 }, () => tip(0.5, 0.5, 0));
  landmarks[0] = tip(0.5, 0.62, 0);
  landmarks[5] = tip(0.6, 0.62, 0);
  landmarks[9] = tip(0.5, 0.62, 0);
  landmarks[13] = tip(0.5, 0.62, 0.08);
  landmarks[17] = tip(0.5, 0.62, 0.1);
  return { landmarks, handedness, score: 1 };
}

function bend(hand: TrackedHand, mcp: number, pip: number, t: number, folded: boolean, down: boolean): void {
  const m = hand.landmarks[mcp];
  const dir = down ? 1 : -1;
  const side = mcp % 2 === 0 ? 0.05 : -0.05;
  if (folded) {
    hand.landmarks[pip] = tip(m.x + side, m.y + 0.08 * dir, m.z);
    hand.landmarks[t] = tip(m.x + side * 0.15, m.y + 0.12 * dir, m.z);
  } else {
    hand.landmarks[pip] = tip(m.x, m.y + 0.1 * dir, m.z);
    hand.landmarks[t] = tip(m.x, m.y + 0.22 * dir, m.z - 0.08);
  }
}

function clawPalmDown(): TrackedHand {
  const hand = baseHand();
  hand.landmarks[0] = tip(0.5, 0.42, 0);
  hand.landmarks[5] = tip(0.62, 0.42, 0);
  hand.landmarks[9] = tip(0.54, 0.42, 0);
  hand.landmarks[13] = tip(0.5, 0.42, 0.08);
  hand.landmarks[17] = tip(0.5, 0.42, 0.1);
  bend(hand, 5, 6, 8, true, true);
  bend(hand, 9, 10, 12, true, true);
  bend(hand, 13, 14, 16, true, true);
  bend(hand, 17, 18, 20, true, true);
  hand.landmarks[4] = tip(0.44, 0.5, 0);
  return hand;
}

function clawPalmUp(): TrackedHand {
  const hand = baseHand();
  hand.landmarks[0] = tip(0.5, 0.58, 0);
  hand.landmarks[5] = tip(0.62, 0.58, 0);
  hand.landmarks[9] = tip(0.54, 0.58, 0);
  hand.landmarks[13] = tip(0.5, 0.58, -0.08);
  hand.landmarks[17] = tip(0.5, 0.58, -0.1);
  bend(hand, 5, 6, 8, true, false);
  bend(hand, 9, 10, 12, true, false);
  bend(hand, 13, 14, 16, true, false);
  bend(hand, 17, 18, 20, true, false);
  hand.landmarks[4] = tip(0.44, 0.5, 0);
  return hand;
}

function openPalm(): TrackedHand {
  const hand = baseHand();
  bend(hand, 5, 6, 8, false, false);
  bend(hand, 9, 10, 12, false, false);
  bend(hand, 13, 14, 16, false, false);
  bend(hand, 17, 18, 20, false, false);
  return hand;
}

function threeDigitReady(handedness: TrackedHand["handedness"] = "Right"): TrackedHand {
  const hand = baseHand(handedness);
  hand.landmarks[0] = tip(0.5, 0.55, 0);
  hand.landmarks[5] = tip(0.64, 0.55, 0);
  hand.landmarks[9] = tip(0.55, 0.55, 0);
  hand.landmarks[17] = tip(0.5, 0.4, 0);
  bend(hand, 5, 6, 8, false, false);
  bend(hand, 9, 10, 12, false, false);
  hand.landmarks[13] = tip(0.52, 0.55, 0.02);
  hand.landmarks[14] = tip(0.58, 0.57, 0.02);
  hand.landmarks[16] = tip(0.51, 0.56, 0.02);
  hand.landmarks[17] = tip(0.48, 0.4, 0);
  hand.landmarks[18] = tip(0.42, 0.58, 0.02);
  hand.landmarks[20] = tip(0.49, 0.56, 0.02);
  hand.landmarks[4] = tip(0.38, 0.48, -0.08);
  hand.landmarks[8].z = -0.1;
  hand.landmarks[12].z = -0.1;
  return hand;
}

function threeDigitSwitch(): TrackedHand {
  const hand = threeDigitReady();
  hand.landmarks[5] = tip(0.62, 0.55, 0);
  hand.landmarks[17] = tip(0.5, 0.68, 0);
  hand.landmarks[8] = tip(0.48, 0.28, 0.02);
  hand.landmarks[12] = tip(0.52, 0.26, 0.02);
  return hand;
}

function openRoom(now = 0) {
  let state = createRoomState();
  const claw = clawPalmDown();
  state = stepRoom(state, [claw], now, CLAW_HOLD_MS);
  return state;
}

describe("claw vs open palm", () => {
  it("treats a curled claw as a claw and a flat palm as open", () => {
    expect(isClaw(clawPalmDown())).toBe(true);
    expect(isOpenPalm(clawPalmDown())).toBe(false);
    expect(isOpenPalm(openPalm())).toBe(true);
    expect(isClaw(openPalm())).toBe(false);
  });

  it("classifies palm down vs palm up", () => {
    expect(classifyPalm(clawPalmDown()).facing).toBe("down");
    expect(classifyPalm(clawPalmUp()).facing).toBe("up");
  });
});

describe("ROOM expansion", () => {
  it("opens from a stable claw palm-down hold, not from an open palm", () => {
    let state = stepRoom(createRoomState(), [openPalm()], 0, CLAW_HOLD_MS);
    expect(state.power).toBe("none");
    state = stepRoom(createRoomState(), [clawPalmDown()], 0, CLAW_HOLD_MS);
    expect(state.power).toBe("room");
    expect(state.open).toBe(true);
  });

  it("grows by time and ignores later hand size or position", () => {
    let state = openRoom(0);
    const startCenter = { ...state.displayCenter };
    const startRadius = state.displayRadius;
    const moved = clawPalmDown();
    moved.landmarks[0] = tip(0.1, 0.1, 0);
    moved.landmarks[12] = tip(0.9, 0.9, 0);
    state = stepRoom(state, [moved], 400, 400);
    expect(state.displayCenter).toEqual(startCenter);
    expect(state.displayRadius).toBeGreaterThan(startRadius);
    expect(state.displayRadius).toBeCloseTo(state.maxRadius * (state.expandElapsed / state.expandMs));
    const later = stepRoom(state, [moved], 400 + state.expandMs, state.expandMs);
    expect(later.displayRadius).toBeCloseTo(later.maxRadius);
    expect(coverRadius(0.5, 0.5)).toBeGreaterThan(0.7);
  });

  it("stays open if the hand vanishes briefly", () => {
    let state = openRoom(0);
    state = stepRoom(state, [], 200, 200);
    expect(state.power).toBe("room");
    expect(state.open).toBe(true);
  });
});

describe("Rasengan lock", () => {
  it("starts Rasengan from claw palm-up and will not replace an open ROOM", () => {
    let state = stepRoom(createRoomState(), [clawPalmUp()], 0, CLAW_HOLD_MS);
    expect(state.power).toBe("rasengan");
    state = openRoom(0);
    state = stepRoom(state, [clawPalmUp()], 800, CLAW_HOLD_MS);
    expect(state.power).toBe("room");
    expect(state.rasenganAmount).toBe(0);
  });
});

describe("Shambles three-digit twist", () => {
  it("needs thumb index middle out and ring pinky in", () => {
    const right = threeDigitReady("Right");
    expect(isFingerExtended(right, 8, 6, 5)).toBe(true);
    expect(isFingerExtended(right, 12, 10, 9)).toBe(true);
    expect(isFingerFolded(right, 16, 14, 13)).toBe(true);
    expect(isFingerFolded(right, 20, 18, 17)).toBe(true);
    expect(isThreeDigit(right)).toBe(true);
    expect(isThreeDigit(threeDigitReady("Left"))).toBe(true);
    expect(isThreeDigit(openPalm())).toBe(false);
  });

  it("says Create ROOM first if the twist happens with no ROOM", () => {
    const state = stepRoom(createRoomState(), [threeDigitReady()], 0, 300);
    expect(state.status).toBe("Create ROOM first");
    expect(state.props[0].x).toBeCloseTo(0.38);
  });

  it("arms READY then swaps once after a palm-toward-user twist and cools down", () => {
    let state = openRoom(0);
    state = stepRoom(state, [threeDigitReady()], 1000, READY_HOLD_MS);
    expect(state.shambles).toBe("ready");
    state = stepRoom(state, [threeDigitSwitch()], 1500, SWAP_DEBOUNCE_MS);
    expect(state.props[0].x).toBeCloseTo(0.62);
    expect(state.props[1].x).toBeCloseTo(0.38);
    expect(state.swapFlash).toBeGreaterThan(0);
    const x = state.props[0].x;
    state = stepRoom(state, [threeDigitSwitch()], 1500 + SWAP_COOLDOWN_MS - 20, SWAP_DEBOUNCE_MS);
    expect(state.props[0].x).toBe(x);
  });

  it("does not swap after tracked hands disappear", () => {
    let state = openRoom(0);
    state = stepRoom(state, [threeDigitReady()], 1000, READY_HOLD_MS);
    state = stepRoom(state, [], 1000 + 800, 800);
    expect(state.shambles).toBe("idle");
    expect(state.props[0].x).toBeCloseTo(0.38);
  });

  it("keeps labels on the same props when positions swap", () => {
    const swapped = swapProps([
      { id: "cube", label: "BLUE CUBE", x: 0.2, y: 0.2, depth: 0.1 },
      { id: "sphere", label: "PINK SPHERE", x: 0.8, y: 0.8, depth: 0.9 },
    ]);
    expect(swapped[0].label).toBe("BLUE CUBE");
    expect(swapped[0].x).toBe(0.8);
  });
});

describe("original mode regression", () => {
  it("still has the original 15 looks and wrap", () => {
    expect(FILTER_NAMES).toHaveLength(15);
    expect(FILTER_NAMES[0]).toBe("thermal");
    expect(nextFilter("edge")).toBe("thermal");
  });
});
