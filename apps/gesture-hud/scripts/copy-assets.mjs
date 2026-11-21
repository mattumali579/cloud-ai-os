import { cpSync, existsSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const wasmSrc = join(root, "node_modules", "@mediapipe", "tasks-vision", "wasm");
const wasmDest = join(root, "public", "mediapipe", "wasm");
const modelDest = join(root, "public", "models", "hand_landmarker.task");
const modelUrl =
  "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task";

if (!existsSync(wasmSrc)) {
  console.error("Install npm packages first: @mediapipe/tasks-vision wasm folder is missing.");
  process.exit(1);
}

mkdirSync(wasmDest, { recursive: true });
cpSync(wasmSrc, wasmDest, { recursive: true });

if (!existsSync(modelDest)) {
  mkdirSync(dirname(modelDest), { recursive: true });
  const response = await fetch(modelUrl);
  if (!response.ok) {
    throw new Error(`Could not download Hand Landmarker model: HTTP ${response.status}`);
  }
  writeFileSync(modelDest, Buffer.from(await response.arrayBuffer()));
  console.log("Downloaded hand_landmarker.task");
}

console.log("Copied MediaPipe wasm and model into public/");
