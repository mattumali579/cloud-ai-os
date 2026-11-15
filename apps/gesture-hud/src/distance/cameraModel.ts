export type CameraIntrinsics = {
  fx: number;
  fy: number;
  cx: number;
  cy: number;
  k1: number;
  k2: number;
  width: number;
  height: number;
  calibrated: boolean;
  markerSideMeters: number;
  source: "default-pinhole" | "known-range" | "saved";
};

export const DEFAULT_FOV_DEG = 62;
export const DEFAULT_MARKER_SIDE_M = 0.1;

export function pinholeFromSize(
  width: number,
  height: number,
  fovDeg = DEFAULT_FOV_DEG,
  markerSideMeters = DEFAULT_MARKER_SIDE_M,
): CameraIntrinsics {
  const fov = (fovDeg * Math.PI) / 180;
  const fx = (0.5 * width) / Math.tan(fov / 2);
  return {
    fx,
    fy: fx,
    cx: width / 2,
    cy: height / 2,
    k1: 0,
    k2: 0,
    width,
    height,
    calibrated: false,
    markerSideMeters,
    source: "default-pinhole",
  };
}

export function scaleIntrinsics(camera: CameraIntrinsics, width: number, height: number): CameraIntrinsics {
  if (camera.width === width && camera.height === height) {
    return camera;
  }
  const sx = width / camera.width;
  const sy = height / camera.height;
  return {
    ...camera,
    fx: camera.fx * sx,
    fy: camera.fy * sy,
    cx: camera.cx * sx,
    cy: camera.cy * sy,
    width,
    height,
  };
}

export function undistortPoint(
  x: number,
  y: number,
  camera: CameraIntrinsics,
): { x: number; y: number } {
  if (camera.k1 === 0 && camera.k2 === 0) {
    return { x, y };
  }
  let xn = (x - camera.cx) / camera.fx;
  let yn = (y - camera.cy) / camera.fy;
  for (let i = 0; i < 8; i += 1) {
    const r2 = xn * xn + yn * yn;
    const radial = 1 + camera.k1 * r2 + camera.k2 * r2 * r2;
    xn = (x - camera.cx) / camera.fx / radial;
    yn = (y - camera.cy) / camera.fy / radial;
  }
  return { x: xn * camera.fx + camera.cx, y: yn * camera.fy + camera.cy };
}

export function meanSidePixels(corners: { x: number; y: number }[]): number {
  if (corners.length < 4) {
    return 0;
  }
  let total = 0;
  for (let i = 0; i < 4; i += 1) {
    const a = corners[i];
    const b = corners[(i + 1) % 4];
    total += Math.hypot(a.x - b.x, a.y - b.y);
  }
  return total / 4;
}

export function focalFromKnownRange(
  markerSideMeters: number,
  meanPixelSide: number,
  rangeMeters: number,
): number {
  if (markerSideMeters <= 0 || meanPixelSide <= 0 || rangeMeters <= 0) {
    throw new Error("Calibration needs a real marker size, pixel size, and tape-measure range.");
  }
  return (rangeMeters * meanPixelSide) / markerSideMeters;
}

export function applyKnownRangeCalibration(
  camera: CameraIntrinsics,
  meanPixelSide: number,
  rangeMeters: number,
): CameraIntrinsics {
  const f = focalFromKnownRange(camera.markerSideMeters, meanPixelSide, rangeMeters);
  return {
    ...camera,
    fx: f,
    fy: f,
    calibrated: true,
    source: "known-range",
  };
}

export function serializeCamera(camera: CameraIntrinsics): string {
  return JSON.stringify(camera);
}

export function parseCamera(raw: string, fallback: CameraIntrinsics): CameraIntrinsics {
  try {
    const parsed = JSON.parse(raw) as Partial<CameraIntrinsics>;
    if (
      typeof parsed.fx !== "number" ||
      typeof parsed.fy !== "number" ||
      typeof parsed.cx !== "number" ||
      typeof parsed.cy !== "number" ||
      typeof parsed.width !== "number" ||
      typeof parsed.height !== "number"
    ) {
      return fallback;
    }
    return {
      fx: parsed.fx,
      fy: parsed.fy,
      cx: parsed.cx,
      cy: parsed.cy,
      k1: typeof parsed.k1 === "number" ? parsed.k1 : 0,
      k2: typeof parsed.k2 === "number" ? parsed.k2 : 0,
      width: parsed.width,
      height: parsed.height,
      calibrated: Boolean(parsed.calibrated),
      markerSideMeters:
        typeof parsed.markerSideMeters === "number" && parsed.markerSideMeters > 0
          ? parsed.markerSideMeters
          : fallback.markerSideMeters,
      source: parsed.source === "known-range" || parsed.source === "saved" ? parsed.source : "saved",
    };
  } catch {
    return fallback;
  }
}
