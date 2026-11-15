import { describe, expect, it } from "vitest";
import { identifyBits, markerGrid, bitsFromGrid } from "./distance/arucoDict";
import {
  detectAruco,
  orderCorners,
  placeMarker,
  renderMarkerGray,
  type GrayImage,
} from "./distance/arucoDetect";
import {
  applyKnownRangeCalibration,
  focalFromKnownRange,
  meanSidePixels,
  pinholeFromSize,
} from "./distance/cameraModel";
import { projectPoint, solvePlanarPnP } from "./distance/pnp";
import { MarkerDistanceProvider } from "./distance/provider";
import { mirrorPixel } from "./coords";

function blank(width: number, height: number, fill = 255): GrayImage {
  return { data: new Uint8Array(width * height).fill(fill), width, height };
}

describe("distance calibration math", () => {
  it("recovers focal length from a known tape range", () => {
    expect(focalFromKnownRange(0.1, 80, 1)).toBeCloseTo(800);
    const camera = applyKnownRangeCalibration(pinholeFromSize(1280, 720), 80, 1);
    expect(camera.calibrated).toBe(true);
    expect(camera.fx).toBeCloseTo(800);
  });

  it("rejects invalid calibration numbers", () => {
    expect(() => focalFromKnownRange(0, 80, 1)).toThrow();
  });

  it("round-trips planar pose range for a square marker", () => {
    const camera = pinholeFromSize(640, 360, 62, 0.1);
    camera.fx = 500;
    camera.fy = 500;
    camera.cx = 320;
    camera.cy = 180;
    const rotation = [
      [1, 0, 0],
      [0, 1, 0],
      [0, 0, 1],
    ];
    const translation = { x: 0.02, y: -0.01, z: 1.2 };
    const world = [
      { x: -0.05, y: 0.05, z: 0 },
      { x: 0.05, y: 0.05, z: 0 },
      { x: 0.05, y: -0.05, z: 0 },
      { x: -0.05, y: -0.05, z: 0 },
    ];
    const corners = world.map((point) => projectPoint(point, rotation, translation, camera));
    const pose = solvePlanarPnP(corners, camera);
    expect(pose).not.toBeNull();
    expect(pose!.rangeMeters).toBeGreaterThan(1.1);
    expect(pose!.rangeMeters).toBeLessThan(1.3);
    expect(pose!.reprojectionError).toBeLessThan(1);
  });
});

describe("invalid markers", () => {
  it("does not invent a distance from an empty frame", () => {
    const provider = new MarkerDistanceProvider(pinholeFromSize(64, 64));
    const rgba = new Uint8ClampedArray(64 * 64 * 4).fill(200);
    const reading = provider.measure(rgba, 64, 64, 10);
    expect(reading.available).toBe(false);
    expect(reading.rangeMeters).toBeNull();
    expect(reading.rangeLabel).toBe("distance unavailable");
  });

  it("does not match random bits to a dictionary id", () => {
    expect(identifyBits(Array(16).fill(1))).toBeNull();
  });
});

describe("aruco detection and dictionary", () => {
  it("identifies rendered 4x4 id 0", () => {
    const bits = bitsFromGrid(markerGrid(0));
    const hit = identifyBits(bits);
    expect(hit).toEqual({ id: 0, rotation: 0 });
  });

  it("finds a printed-size marker placed on a white page", () => {
    const page = blank(240, 240, 255);
    placeMarker(page, renderMarkerGray(0, 96), 72, 72);
    const found = detectAruco(page);
    expect(found.length).toBeGreaterThanOrEqual(1);
    expect(found[0].id).toBe(0);
    expect(found[0].corners).toHaveLength(4);
  });

  it("orders corners starting near the top-left", () => {
    const ordered = orderCorners([
      { x: 10, y: 0 },
      { x: 10, y: 10 },
      { x: 0, y: 10 },
      { x: 0, y: 0 },
    ]);
    expect(ordered[0]).toEqual({ x: 0, y: 0 });
  });
});

describe("mirror mapping", () => {
  it("flips x and keeps y when the preview is mirrored", () => {
    expect(mirrorPixel({ x: 20, y: 50 }, 100, true)).toEqual({ x: 80, y: 50 });
    expect(mirrorPixel({ x: 20, y: 50 }, 100, false)).toEqual({ x: 20, y: 50 });
  });

  it("keeps mean side length unchanged after a mirror", () => {
    const corners = [
      { x: 10, y: 10 },
      { x: 50, y: 10 },
      { x: 50, y: 50 },
      { x: 10, y: 50 },
    ];
    const mirrored = corners.map((c) => mirrorPixel(c, 200, true));
    expect(meanSidePixels(mirrored)).toBeCloseTo(meanSidePixels(corners));
  });
});
