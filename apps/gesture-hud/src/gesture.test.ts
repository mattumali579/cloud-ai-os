import { describe, expect, it } from "vitest";
import {
  createGestureState,
  DEFAULT_GESTURE,
  poseFromAnchors,
  resetPanel,
  smoothPose,
  stepGesture,
} from "./gesture";
import { GlassesFrameSource } from "./framesource";
import {
  clamp,
  isPinching,
  mirrorHand,
  pinchAnchor,
  pinchDistance,
  thumbPinkyDistance,
  type Point,
  type TrackedHand,
} from "./landmarks";
import { applyFilter } from "./filters";
import { portalPolygons } from "./portal";

function tip(x: number, y: number, z = 0): Point {
  return { x, y, z };
}

function hand(thumb: Point, index: Point, handedness: TrackedHand["handedness"]): TrackedHand {
  const landmarks: Point[] = Array.from({ length: 21 }, () => tip(0.5, 0.5, 0));
  landmarks[4] = thumb;
  landmarks[8] = index;
  landmarks[0] = tip((thumb.x + index.x) / 2, (thumb.y + index.y) / 2, 0);
  return { landmarks, handedness, score: 1 };
}

describe("pinch and mirror", () => {
  it("detects a close thumb-index pinch", () => {
    const close = hand(tip(0.2, 0.2), tip(0.22, 0.21), "Left");
    const open = hand(tip(0.2, 0.2), tip(0.4, 0.4), "Left");
    expect(pinchDistance(close.landmarks)).toBeLessThan(0.04);
    expect(isPinching(close.landmarks, 0.055)).toBe(true);
    expect(isPinching(open.landmarks, 0.055)).toBe(false);
  });

  it("measures thumb to pinky for filter switching", () => {
    const landmarks = Array.from({ length: 21 }, () => tip(0.5, 0.5, 0));
    landmarks[4] = tip(0.2, 0.2);
    landmarks[20] = tip(0.21, 0.21);
    expect(thumbPinkyDistance(landmarks)).toBeLessThan(0.03);
    landmarks[20] = tip(0.8, 0.8);
    expect(thumbPinkyDistance(landmarks)).toBeGreaterThan(0.5);
  });

  it("mirrors x and keeps y", () => {
    const original = [tip(0.2, 0.3, 0.1)];
    expect(mirrorHand(original, true)[0]).toEqual({ x: 0.8, y: 0.3, z: 0.1 });
    expect(mirrorHand(original, false)[0]).toEqual(original[0]);
  });

  it("anchors at the pinch midpoint", () => {
    const tracked = hand(tip(0.1, 0.2), tip(0.3, 0.4), "Right");
    const mid = pinchAnchor(tracked.landmarks);
    expect(mid.x).toBeCloseTo(0.2);
    expect(mid.y).toBeCloseTo(0.3);
    expect(mid.z).toBeCloseTo(0);
  });
});

describe("panel pose", () => {
  it("puts the center between the hands and scales width with separation", () => {
    const narrow = poseFromAnchors(tip(0.4, 0.5), tip(0.6, 0.5), DEFAULT_GESTURE);
    const wide = poseFromAnchors(tip(0.15, 0.5), tip(0.85, 0.5), DEFAULT_GESTURE);
    expect(narrow.cx).toBeCloseTo(0.5, 2);
    expect(narrow.cy).toBeCloseTo(0.5, 2);
    expect(wide.width).toBeGreaterThan(narrow.width);
    expect(wide.width).toBeLessThanOrEqual(DEFAULT_GESTURE.maxWidth);
  });

  it("rotates when one hand is higher", () => {
    const pose = poseFromAnchors(tip(0.3, 0.6), tip(0.7, 0.3), DEFAULT_GESTURE);
    expect(pose.rotation).toBeLessThan(0);
  });

  it("tilts from depth difference and clamps size", () => {
    const pose = poseFromAnchors(tip(0, 0, 0.4), tip(1, 0, -0.4), DEFAULT_GESTURE);
    expect(pose.width).toBe(DEFAULT_GESTURE.maxWidth);
    expect(pose.tilt).toBeGreaterThan(0);
    expect(pose.cx).toBeGreaterThan(0);
    expect(pose.cx).toBeLessThan(1);
  });

  it("clamps numbers", () => {
    expect(clamp(2, 0, 1)).toBe(1);
    expect(clamp(-1, 0, 1)).toBe(0);
    expect(clamp(0.4, 0, 1)).toBe(0.4);
  });
});

describe("smoothing and open-close", () => {
  it("moves partway toward the target", () => {
    const from = { cx: 0, cy: 0, width: 0.2, height: 0.1, rotation: 0, tilt: 0 };
    const to = { cx: 1, cy: 1, width: 0.8, height: 0.4, rotation: 1, tilt: 0.5 };
    const mid = smoothPose(from, to, 0.5);
    expect(mid.cx).toBeCloseTo(0.5);
    expect(mid.width).toBeCloseTo(0.5);
  });

  it("shows the filter with two hands and hides it when a hand drops", () => {
    const left = hand(tip(0.25, 0.5), tip(0.26, 0.5), "Left");
    const right = hand(tip(0.74, 0.5), tip(0.75, 0.5), "Right");
    let state = createGestureState(0);
    expect(state.surface).toBe(0);
    state = stepGesture(state, [left, right], 16);
    expect(state.status).toBe("gripping");
    expect(state.gripping).toBe(true);
    expect(state.surface).toBeGreaterThan(0.2);
    const shown = state.surface;
    state = stepGesture(state, [left], 32);
    expect(state.gripping).toBe(false);
    expect(state.status).toBe("one-hand");
    expect(state.surface).toBeLessThan(shown);
    state = stepGesture(state, [left, right], 48);
    expect(state.gripping).toBe(true);
    expect(state.surface).toBeGreaterThan(0.2);
  });

  it("fades indicators after both hands are gone for more than a second", () => {
    const left = hand(tip(0.3, 0.5), tip(0.31, 0.5), "Left");
    let state = stepGesture(createGestureState(0), [left], 10);
    expect(state.indicatorsVisible).toBe(true);
    state = stepGesture(state, [], 20);
    expect(state.indicatorsVisible).toBe(true);
    state = stepGesture(state, [], 20 + DEFAULT_GESTURE.lostMs + 1);
    expect(state.indicatorsVisible).toBe(false);
    expect(state.panel.width).toBeGreaterThan(0);
  });

  it("reset restores the default pose", () => {
    let state = createGestureState();
    state.panel.cx = 0.2;
    state.displayPanel.cx = 0.2;
    state = resetPanel(state);
    expect(state.panel.cx).toBe(0.5);
    expect(state.displayPanel.width).toBeCloseTo(0.46);
  });
});

describe("portal look", () => {
  it("builds two 3D bands between the hands", () => {
    const left = fullHand(0.25);
    const right = fullHand(0.75);
    const polys = portalPolygons([left, right]);
    expect(polys).toHaveLength(2);
    expect(polys[0]).toHaveLength(6);
    expect(polys[1]).toHaveLength(6);
  });

  it("paints thermal colors hotter on bright pixels", () => {
    const data = { data: new Uint8ClampedArray([10, 10, 10, 255, 250, 250, 250, 255]) } as ImageData;
    applyFilter(data, "thermal");
    const darkRed = data.data[0];
    const hotRed = data.data[4];
    expect(hotRed).toBeGreaterThan(darkRed);
  });
});

function fullHand(x: number): TrackedHand {
  const landmarks: Point[] = Array.from({ length: 21 }, (_, index) =>
    tip(x, 0.3 + (index % 5) * 0.05, 0),
  );
  return { landmarks, handedness: x < 0.5 ? "Left" : "Right", score: 1 };
}

describe("frame sources", () => {
  it("refuses the glasses adapter in this webcam-only build", async () => {
    const source = new GlassesFrameSource();
    await expect(source.start()).rejects.toThrow(/webcam/i);
    expect(source.kind).toBe("glasses-placeholder");
    expect(source.getVideo()).toBeNull();
  });
});
