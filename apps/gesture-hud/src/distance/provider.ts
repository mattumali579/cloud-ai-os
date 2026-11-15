import {
  applyKnownRangeCalibration,
  meanSidePixels,
  pinholeFromSize,
  scaleIntrinsics,
  type CameraIntrinsics,
} from "./cameraModel";
import { detectArucoRgba } from "./arucoDetect";
import { qualityFromError, solvePlanarPnP } from "./pnp";

export type DistanceReading = {
  available: boolean;
  rangeMeters: number | null;
  rangeLabel: string;
  quality: "ok" | "weak" | "unavailable";
  markerId: number | null;
  lastUpdatedAt: number | null;
  reason: string;
  corners: { x: number; y: number }[] | null;
  calibrated: boolean;
};

export interface DistanceProvider {
  readonly kind: string;
  measure(rgba: Uint8ClampedArray, width: number, height: number, now: number): DistanceReading;
}

export const DISTANCE_UNAVAILABLE: DistanceReading = {
  available: false,
  rangeMeters: null,
  rangeLabel: "distance unavailable",
  quality: "unavailable",
  markerId: null,
  lastUpdatedAt: null,
  reason: "No calibrated marker in view.",
  corners: null,
  calibrated: false,
};

export function formatRange(meters: number): string {
  if (meters < 1) {
    return `${(meters * 100).toFixed(1)} cm`;
  }
  return `${meters.toFixed(2)} m`;
}

export class MarkerDistanceProvider implements DistanceProvider {
  readonly kind = "aruco-pnp";
  camera: CameraIntrinsics;
  last: DistanceReading = { ...DISTANCE_UNAVAILABLE };

  constructor(camera: CameraIntrinsics) {
    this.camera = camera;
  }

  setMarkerSideCm(cm: number): void {
    if (cm <= 0) {
      return;
    }
    this.camera = { ...this.camera, markerSideMeters: cm / 100 };
  }

  calibrateFromTape(rangeMeters: number): DistanceReading {
    if (!this.last.corners || this.last.corners.length !== 4) {
      return {
        ...DISTANCE_UNAVAILABLE,
        calibrated: this.camera.calibrated,
        reason: "Show the printed marker, then calibrate.",
      };
    }
    const mean = meanSidePixels(this.last.corners);
    this.camera = applyKnownRangeCalibration(this.camera, mean, rangeMeters);
    const pose = solvePlanarPnP(this.last.corners, this.camera);
    if (!pose) {
      return {
        ...DISTANCE_UNAVAILABLE,
        corners: this.last.corners,
        calibrated: true,
        reason: "Calibration saved, but pose could not be rebuilt.",
      };
    }
    this.last = {
      available: true,
      rangeMeters: pose.rangeMeters,
      rangeLabel: formatRange(pose.rangeMeters),
      quality: qualityFromError(pose.reprojectionError),
      markerId: this.last.markerId,
      lastUpdatedAt: this.last.lastUpdatedAt,
      reason: "Calibrated marker range (camera to marker).",
      corners: this.last.corners,
      calibrated: true,
    };
    return this.last;
  }

  measure(rgba: Uint8ClampedArray, width: number, height: number, now: number): DistanceReading {
    this.camera = scaleIntrinsics(this.camera, width, height);
    const markers = detectArucoRgba(rgba, width, height);
    if (markers.length === 0) {
      this.last = {
        ...DISTANCE_UNAVAILABLE,
        calibrated: this.camera.calibrated,
        reason: "No ArUco marker found. Print the 4x4 marker and hold it in view.",
      };
      return this.last;
    }
    const marker = markers[0];
    const pose = solvePlanarPnP(marker.corners, this.camera);
    if (!pose) {
      this.last = {
        ...DISTANCE_UNAVAILABLE,
        markerId: marker.id,
        corners: marker.corners,
        calibrated: this.camera.calibrated,
        reason: "Marker seen, but pose could not be solved.",
      };
      return this.last;
    }
    const quality = qualityFromError(pose.reprojectionError);
    if (quality === "unavailable") {
      this.last = {
        ...DISTANCE_UNAVAILABLE,
        markerId: marker.id,
        corners: marker.corners,
        calibrated: this.camera.calibrated,
        reason: "Marker tracking is too unstable.",
      };
      return this.last;
    }
    this.last = {
      available: true,
      rangeMeters: pose.rangeMeters,
      rangeLabel: formatRange(pose.rangeMeters),
      quality,
      markerId: marker.id,
      lastUpdatedAt: now,
      reason:
        quality === "weak"
          ? "Tracking is weak. Flatten the paper and face the camera."
          : this.camera.calibrated
            ? "Calibrated marker range (camera to marker)."
            : "Uncalibrated pinhole guess. Set the printed size and calibrate at a tape-measured distance.",
      corners: marker.corners,
      calibrated: this.camera.calibrated,
    };
    return this.last;
  }
}

export function defaultMarkerProvider(width = 1280, height = 720): MarkerDistanceProvider {
  return new MarkerDistanceProvider(pinholeFromSize(width, height));
}
