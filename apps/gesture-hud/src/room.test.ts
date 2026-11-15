import { describe, expect, it } from "vitest";
import type { Point, TrackedHand } from "./landmarks";
import { isOpenHand, OPEN_HOLD_MS, SWAP_COOLDOWN_MS, SWAP_HOLD_MS } from "./room/handPose";
import { createRoomState, stepRoom, swapProps } from "./room/state";
import { FILTER_NAMES, nextFilter } from "./filters";

function tip(x: number, y: number, z = 0): Point {
  return { x, y, z };
}

function openHand(handedness: TrackedHand["handedness"], ox = 0.5, oy = 0.5): TrackedHand {
  const landmarks: Point[] = Array.from({ length: 21 }, () => tip(ox, oy, 0));
  landmarks[0] = tip(ox, oy + 0.12);
  landmarks[5] = tip(ox - 0.04, oy);
  landmarks[9] = tip(ox, oy);
  landmarks[13] = tip(ox + 0.04, oy);
  landmarks[17] = tip(ox + 0.07, oy + 0.02);
  landmarks[6] = tip(ox - 0.05, oy - 0.04);
  landmarks[10] = tip(ox, oy - 0.04);
  landmarks[14] = tip(ox + 0.05, oy - 0.04);
  landmarks[18] = tip(ox + 0.08, oy - 0.03);
  landmarks[8] = tip(ox - 0.08, oy - 0.16);
  landmarks[12] = tip(ox, oy - 0.18);
  landmarks[16] = tip(ox + 0.08, oy - 0.16);
  landmarks[20] = tip(ox + 0.12, oy - 0.12);
  landmarks[4] = tip(ox - 0.1, oy + 0.02);
  return { landmarks, handedness, score: 1 };
}

function pinchHand(handedness: TrackedHand["handedness"], x: number, y: number): TrackedHand {
  const landmarks: Point[] = Array.from({ length: 21 }, () => tip(x, y, 0));
  landmarks[4] = tip(x, y);
  landmarks[8] = tip(x + 0.01, y + 0.01);
  landmarks[12] = tip(x, y + 0.08);
  landmarks[16] = tip(x + 0.02, y + 0.09);
  landmarks[20] = tip(x + 0.04, y + 0.1);
  return { landmarks, handedness, score: 1 };
}

describe("ROOM open-hand hold", () => {
  it("detects an open palm and ignores a pinch", () => {
    expect(isOpenHand(openHand("Right"))).toBe(true);
    expect(isOpenHand(pinchHand("Right", 0.5, 0.5))).toBe(false);
  });

  it("opens only after a stable 600ms hold", () => {
    const hand = openHand("Left", 0.5, 0.5);
    let state = createRoomState();
    state = stepRoom(state, [hand], 100, 200);
    expect(state.open).toBe(false);
    state = stepRoom(state, [hand], 100 + OPEN_HOLD_MS, OPEN_HOLD_MS);
    expect(state.open).toBe(true);
    expect(state.openAmount).toBeGreaterThan(0);
  });

  it("keeps the room up through a brief hand loss", () => {
    const hand = openHand("Left", 0.48, 0.5);
    let state = createRoomState();
    state = stepRoom(state, [hand], 0, OPEN_HOLD_MS);
    expect(state.open).toBe(true);
    const radius = state.displayRadius;
    state = stepRoom(state, [], 200, 200);
    expect(state.open).toBe(true);
    expect(state.displayRadius).toBeGreaterThan(0);
    expect(Math.abs(state.displayRadius - radius)).toBeLessThan(0.3);
  });

  it("clamps and smooths radius", () => {
    const huge = openHand("Right", 0.5, 0.5);
    huge.landmarks[12] = tip(0.5, -2);
    let state = createRoomState();
    state = stepRoom(state, [huge], 0, OPEN_HOLD_MS);
    expect(state.radius).toBeLessThanOrEqual(0.62);
    expect(state.displayRadius).toBeLessThan(state.radius);
  });
});

describe("Shambles swap", () => {
  it("exchanges positions and keeps labels on the same props", () => {
    const swapped = swapProps([
      { id: "cube", label: "BLUE CUBE", x: 0.2, y: 0.2, depth: 0.1 },
      { id: "sphere", label: "PINK SPHERE", x: 0.8, y: 0.8, depth: 0.9 },
    ]);
    expect(swapped[0]).toMatchObject({ id: "cube", label: "BLUE CUBE", x: 0.8, y: 0.8, depth: 0.9 });
    expect(swapped[1]).toMatchObject({ id: "sphere", label: "PINK SPHERE", x: 0.2, y: 0.2, depth: 0.1 });
  });

  it("needs a held two-hand pinch and then cools down", () => {
    const palm = openHand("Left", 0.5, 0.5);
    let state = stepRoom(createRoomState(), [palm], 0, OPEN_HOLD_MS);
    expect(state.open).toBe(true);
    const left = pinchHand("Left", 0.3, 0.4);
    const right = pinchHand("Right", 0.7, 0.4);
    state = stepRoom(state, [left, right], 1000, SWAP_HOLD_MS - 40);
    expect(state.props[0].x).toBeCloseTo(0.38);
    state = stepRoom(state, [left, right], 1400, 80);
    expect(state.props[0].x).toBeCloseTo(0.62);
    expect(state.props[1].x).toBeCloseTo(0.38);
    expect(state.swapFlash).toBeGreaterThan(0);
    const after = state.props[0].x;
    state = stepRoom(state, [left, right], 1400 + SWAP_COOLDOWN_MS - 10, SWAP_HOLD_MS);
    expect(state.props[0].x).toBe(after);
  });

  it("drops a drag when hands disappear", () => {
    const palm = openHand("Left", 0.5, 0.5);
    let state = stepRoom(createRoomState(), [palm], 0, OPEN_HOLD_MS);
    const grab = pinchHand("Right", state.props[0].x, state.props[0].y);
    state = stepRoom(state, [grab], 800, 16);
    expect(state.draggingId).toBe("cube");
    state = stepRoom(state, [], 900, 16);
    expect(state.draggingId).toBeNull();
  });
});

describe("original mode regression", () => {
  it("still has the original 15 looks and wrap", () => {
    expect(FILTER_NAMES).toHaveLength(15);
    expect(FILTER_NAMES[0]).toBe("thermal");
    expect(nextFilter("edge")).toBe("thermal");
  });
});
