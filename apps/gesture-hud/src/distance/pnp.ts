import { undistortPoint, type CameraIntrinsics } from "./cameraModel";

export type Vec3 = { x: number; y: number; z: number };
export type Pixel = { x: number; y: number };

export type MarkerPose = {
  rotation: number[][];
  translation: Vec3;
  rangeMeters: number;
  reprojectionError: number;
};

function markerWorldCorners(sideMeters: number): Vec3[] {
  const h = sideMeters / 2;
  return [
    { x: -h, y: h, z: 0 },
    { x: h, y: h, z: 0 },
    { x: h, y: -h, z: 0 },
    { x: -h, y: -h, z: 0 },
  ];
}

function solve8(a: number[][], b: number[]): number[] | null {
  const n = 8;
  const m = a.map((row, i) => [...row, b[i]]);
  for (let col = 0; col < n; col += 1) {
    let pivot = col;
    for (let row = col + 1; row < n; row += 1) {
      if (Math.abs(m[row][col]) > Math.abs(m[pivot][col])) {
        pivot = row;
      }
    }
    if (Math.abs(m[pivot][col]) < 1e-12) {
      return null;
    }
    [m[col], m[pivot]] = [m[pivot], m[col]];
    const scale = m[col][col];
    for (let j = col; j <= n; j += 1) {
      m[col][j] /= scale;
    }
    for (let row = 0; row < n; row += 1) {
      if (row === col) {
        continue;
      }
      const factor = m[row][col];
      for (let j = col; j <= n; j += 1) {
        m[row][j] -= factor * m[col][j];
      }
    }
  }
  return m.map((row) => row[n]);
}

export function planarHomography(world: Pixel[], image: Pixel[]): number[] | null {
  const a: number[][] = [];
  const b: number[] = [];
  for (let i = 0; i < 4; i += 1) {
    const { x, y } = world[i];
    const u = image[i].x;
    const v = image[i].y;
    a.push([x, y, 1, 0, 0, 0, -u * x, -u * y]);
    b.push(u);
    a.push([0, 0, 0, x, y, 1, -v * x, -v * y]);
    b.push(v);
  }
  const h = solve8(a, b);
  if (!h) {
    return null;
  }
  return [...h, 1];
}

function normalize(v: number[]): number[] {
  const n = Math.hypot(v[0], v[1], v[2]) || 1;
  return [v[0] / n, v[1] / n, v[2] / n];
}

function cross(a: number[], b: number[]): number[] {
  return [
    a[1] * b[2] - a[2] * b[1],
    a[2] * b[0] - a[0] * b[2],
    a[0] * b[1] - a[1] * b[0],
  ];
}

function orthonormalize(r1: number[], r2: number[], r3: number[]): number[][] {
  const u = normalize(r1);
  const v = normalize([r2[0] - u[0] * (u[0] * r2[0] + u[1] * r2[1] + u[2] * r2[2]), r2[1] - u[1] * (u[0] * r2[0] + u[1] * r2[1] + u[2] * r2[2]), r2[2] - u[2] * (u[0] * r2[0] + u[1] * r2[1] + u[2] * r2[2])]);
  const w = cross(u, v);
  const w2 = r3[0] * w[0] + r3[1] * w[1] + r3[2] * w[2] < 0 ? w.map((c) => -c) : w;
  return [u, v, normalize(w2)];
}

export function projectPoint(point: Vec3, rotation: number[][], translation: Vec3, camera: CameraIntrinsics): Pixel {
  const x = rotation[0][0] * point.x + rotation[1][0] * point.y + rotation[2][0] * point.z + translation.x;
  const y = rotation[0][1] * point.x + rotation[1][1] * point.y + rotation[2][1] * point.z + translation.y;
  const z = rotation[0][2] * point.x + rotation[1][2] * point.y + rotation[2][2] * point.z + translation.z;
  return {
    x: camera.fx * (x / z) + camera.cx,
    y: camera.fy * (y / z) + camera.cy,
  };
}

export function solvePlanarPnP(
  imageCorners: Pixel[],
  camera: CameraIntrinsics,
): MarkerPose | null {
  if (imageCorners.length !== 4) {
    return null;
  }
  const undistorted = imageCorners.map((corner) => undistortPoint(corner.x, corner.y, camera));
  const world = markerWorldCorners(camera.markerSideMeters);
  const world2 = world.map((p) => ({ x: p.x, y: p.y }));
  const hImg = planarHomography(world2, undistorted);
  if (!hImg) {
    return null;
  }
  const invFx = 1 / camera.fx;
  const invFy = 1 / camera.fy;
  const h = [
    [hImg[0], hImg[1], hImg[2]],
    [hImg[3], hImg[4], hImg[5]],
    [hImg[6], hImg[7], hImg[8]],
  ];
  const kinvH = [
    [(h[0][0] - camera.cx * h[2][0]) * invFx, (h[0][1] - camera.cx * h[2][1]) * invFx, (h[0][2] - camera.cx * h[2][2]) * invFx],
    [(h[1][0] - camera.cy * h[2][0]) * invFy, (h[1][1] - camera.cy * h[2][1]) * invFy, (h[1][2] - camera.cy * h[2][2]) * invFy],
    [h[2][0], h[2][1], h[2][2]],
  ];
  const n1 = Math.hypot(kinvH[0][0], kinvH[1][0], kinvH[2][0]);
  const n2 = Math.hypot(kinvH[0][1], kinvH[1][1], kinvH[2][1]);
  const lambda = 1 / ((n1 + n2) / 2 || 1);
  let r1 = [kinvH[0][0] * lambda, kinvH[1][0] * lambda, kinvH[2][0] * lambda];
  let r2 = [kinvH[0][1] * lambda, kinvH[1][1] * lambda, kinvH[2][1] * lambda];
  let t = [kinvH[0][2] * lambda, kinvH[1][2] * lambda, kinvH[2][2] * lambda];
  if (t[2] < 0) {
    r1 = r1.map((c) => -c);
    r2 = r2.map((c) => -c);
    t = t.map((c) => -c);
  }
  const r3 = cross(r1, r2);
  const R = orthonormalize(r1, r2, r3);
  const rotation = [
    [R[0][0], R[0][1], R[0][2]],
    [R[1][0], R[1][1], R[1][2]],
    [R[2][0], R[2][1], R[2][2]],
  ];
  const translation = { x: t[0], y: t[1], z: t[2] };
  if (translation.z <= 0.02) {
    return null;
  }
  let error = 0;
  world.forEach((point, index) => {
    const projected = projectPoint(point, rotation, translation, camera);
    error += Math.hypot(projected.x - undistorted[index].x, projected.y - undistorted[index].y);
  });
  return {
    rotation,
    translation,
    rangeMeters: Math.hypot(translation.x, translation.y, translation.z),
    reprojectionError: error / 4,
  };
}

export function qualityFromError(error: number): "ok" | "weak" | "unavailable" {
  if (error > 12) {
    return "unavailable";
  }
  if (error > 4) {
    return "weak";
  }
  return "ok";
}
