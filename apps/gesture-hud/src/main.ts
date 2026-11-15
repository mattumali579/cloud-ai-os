import { WebcamFrameSource } from "./camera";
import { HandDetector } from "./detector";
import { GlassesFrameSource } from "./framesource";
import {
  createGestureState,
  DEFAULT_GESTURE,
  resetPanel,
  stepGesture,
  type GestureConfig,
  type GestureState,
} from "./gesture";
import { labelFor, nextFilter, prevFilter, type FilterName } from "./filters";
import { drawPortals, portalPolygons, toPixels } from "./portal";
import { drawHands, sizeCanvas } from "./render";
import { thumbPinkyDistance, type TrackedHand } from "./landmarks";
import { modeLabel, type HudMode } from "./hudMode";
import { parseCamera, pinholeFromSize, serializeCamera } from "./distance/cameraModel";
import { drawMarkerOverlay } from "./distance/draw";
import { printSheetHtml, HOME_CALIBRATION_STEPS } from "./distance/markerSvg";
import { MarkerDistanceProvider, type DistanceReading } from "./distance/provider";
import { classifyPalm } from "./room/handPose";
import { createRoomState, forcePower, resetRoom, stepRoom, type RoomState } from "./room/state";
import { drawRoom } from "./room/render";
import "./style.css";

const CAMERA_STORE = "gesture-hud-camera-v1";
const PALM_STORE = "gesture-hud-palm-v1";
const HINTS: Record<HudMode, string> = {
  original:
    "Hold both hands in the camera to stretch a colored portal between them. Drop a hand to hide it. Press Next look to flip through 15 color looks.",
  distance:
    "Print the ArUco marker, measure the outer black square, then hold it in the webcam. Range is camera-to-marker only.",
  room:
    "Palm down plus a clawed hand opens ROOM. Palm up plus a claw starts Rasengan. In ROOM, three fingers at the camera, then twist your palm toward you to swap the two fake objects.",
};

const stage = document.querySelector<HTMLElement>("#stage")!;
const video = document.querySelector<HTMLVideoElement>("#cam")!;
const canvas = document.querySelector<HTMLCanvasElement>("#overlay")!;
const panel = document.querySelector<HTMLElement>("#panel")!;
const banner = document.querySelector<HTMLElement>("#banner")!;
const bannerText = document.querySelector<HTMLElement>("#bannerText")!;
const startBtn = document.querySelector<HTMLButtonElement>("#start")!;
const stopBtn = document.querySelector<HTMLButtonElement>("#stop")!;
const resetBtn = document.querySelector<HTMLButtonElement>("#reset")!;
const cameraSelect = document.querySelector<HTMLSelectElement>("#camera")!;
const mirrorToggle = document.querySelector<HTMLInputElement>("#mirror")!;
const hudMode = document.querySelector<HTMLElement>("#hudMode")!;
const hudFilter = document.querySelector<HTMLElement>("#hudFilter")!;
const hudExtra = document.querySelector<HTMLElement>("#hudExtra")!;
const nextLookBtn = document.querySelector<HTMLButtonElement>("#nextLook")!;
const prevLookBtn = document.querySelector<HTMLButtonElement>("#prevLook")!;
const hint = document.querySelector<HTMLElement>("#hint")!;
const modeOriginalBtn = document.querySelector<HTMLButtonElement>("#modeOriginal")!;
const modeDistanceBtn = document.querySelector<HTMLButtonElement>("#modeDistance")!;
const modeRoomBtn = document.querySelector<HTMLButtonElement>("#modeRoom")!;
const resetRoomBtn = document.querySelector<HTMLButtonElement>("#resetRoom")!;
const pauseRoomBtn = document.querySelector<HTMLButtonElement>("#pauseRoom")!;
const powerTools = document.querySelector<HTMLElement>("#powerTools")!;
const forceRoomBtn = document.querySelector<HTMLButtonElement>("#forceRoom")!;
const forceRasenganBtn = document.querySelector<HTMLButtonElement>("#forceRasengan")!;
const calPalmDownBtn = document.querySelector<HTMLButtonElement>("#calPalmDown")!;
const calPalmUpBtn = document.querySelector<HTMLButtonElement>("#calPalmUp")!;
const growMs = document.querySelector<HTMLInputElement>("#growMs")!;
const distanceTools = document.querySelector<HTMLElement>("#distanceTools")!;
const markerCm = document.querySelector<HTMLInputElement>("#markerCm")!;
const tapeM = document.querySelector<HTMLInputElement>("#tapeM")!;
const calibrateBtn = document.querySelector<HTMLButtonElement>("#calibrate")!;
const printMarkerBtn = document.querySelector<HTMLButtonElement>("#printMarker")!;

const camera = new WebcamFrameSource(video);
const glasses = new GlassesFrameSource();
void glasses;
const detector = new HandDetector();
const config: GestureConfig = { ...DEFAULT_GESTURE };
let state: GestureState = createGestureState();
function loadPalm() {
  try {
    const parsed = JSON.parse(localStorage.getItem(PALM_STORE) ?? "");
    return {
      upY: typeof parsed.upY === "number" ? parsed.upY : null,
      downY: typeof parsed.downY === "number" ? parsed.downY : null,
    };
  } catch {
    return { upY: null, downY: null };
  }
}

let room: RoomState = createRoomState(loadPalm());
let running = false;
let skip = 0;
let lastHands: TrackedHand[] = [];
let filterName: FilterName = "thermal";
let lastFilterSwitch = 0;
let mode: HudMode = "original";
let lastFrame = 0;
let distanceSkip = 0;
let lastReading: DistanceReading | null = null;
let roomAnim = 0;
const scratch = document.createElement("canvas");
const scratchCtx = scratch.getContext("2d")!;
const distance = new MarkerDistanceProvider(
  parseCamera(localStorage.getItem(CAMERA_STORE) ?? "", pinholeFromSize(1280, 720)),
);
markerCm.value = String(Math.round(distance.camera.markerSideMeters * 1000) / 10);

function setBanner(text: string, isError = false): void {
  banner.classList.remove("hidden");
  bannerText.textContent = text;
  bannerText.classList.toggle("error", isError);
}

function hideBanner(): void {
  banner.classList.add("hidden");
}

function fillCameras(devices: MediaDeviceInfo[]): void {
  const current = cameraSelect.value;
  cameraSelect.innerHTML = "";
  if (devices.length === 0) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "Default camera";
    cameraSelect.append(option);
    return;
  }
  devices.forEach((device, index) => {
    const option = document.createElement("option");
    option.value = device.deviceId;
    option.textContent = device.label || `Camera ${index + 1}`;
    cameraSelect.append(option);
  });
  if ([...cameraSelect.options].some((option) => option.value === current)) {
    cameraSelect.value = current;
  }
}

function applyMode(): void {
  hint.textContent = HINTS[mode];
  distanceTools.hidden = mode !== "distance";
  powerTools.hidden = mode !== "room";
  resetRoomBtn.hidden = mode !== "room";
  pauseRoomBtn.hidden = mode !== "room";
  nextLookBtn.disabled = mode !== "original";
  prevLookBtn.disabled = mode !== "original";
  modeOriginalBtn.classList.toggle("active", mode === "original");
  modeDistanceBtn.classList.toggle("active", mode === "distance");
  modeRoomBtn.classList.toggle("active", mode === "room");
  pauseRoomBtn.textContent = room.paused ? "Resume" : "Pause";
}

function setMode(next: HudMode): void {
  mode = next;
  if (next !== "room") {
    room = createRoomState(room.calibration);
  }
  applyMode();
}

async function start(): Promise<void> {
  startBtn.disabled = true;
  setBanner("Starting camera…");
  try {
    if (!detector.status.ready) {
      await detector.load();
    }
    await camera.start({ deviceId: cameraSelect.value || undefined });
    fillCameras(await camera.listDevices());
    running = true;
    stopBtn.disabled = false;
    resetBtn.disabled = false;
    panel.hidden = true;
    hideBanner();
    state = createGestureState(performance.now());
    lastFrame = performance.now();
    loop();
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    setBanner(
      message.includes("NotAllowedError") || message.toLowerCase().includes("permission")
        ? "Camera permission was blocked. Allow the camera for this page and press Start."
        : `Camera did not start: ${message}`,
      true,
    );
    startBtn.disabled = false;
  }
}

function stop(): void {
  running = false;
  camera.stop();
  panel.hidden = true;
  startBtn.disabled = false;
  stopBtn.disabled = true;
  setBanner("Camera stopped. Press Start to open the webcam.");
}

function reset(): void {
  if (mode === "room") {
    room = resetRoom(room);
    return;
  }
  state = resetPanel(state);
}

function cycleLook(direction: 1 | -1): void {
  if (mode !== "original") {
    return;
  }
  const now = performance.now();
  if (now - lastFilterSwitch < 180) {
    return;
  }
  filterName = direction === 1 ? nextFilter(filterName) : prevFilter(filterName);
  lastFilterSwitch = now;
}

function detectHands(now: number): TrackedHand[] {
  let hands = lastHands;
  if (skip <= 0) {
    const detected = detector.detect(video, now);
    if (detected) {
      hands = detected;
      lastHands = detected;
    }
    skip = detector.status.lastMs > 70 ? 2 : detector.status.lastMs > 42 ? 1 : 0;
  } else {
    skip -= 1;
  }
  return hands;
}

function drawOriginal(ctx: CanvasRenderingContext2D, hands: TrackedHand[], now: number, width: number, height: number): void {
  if (hands.some((hand) => thumbPinkyDistance(hand.landmarks) < 0.048) && now - lastFilterSwitch > 400) {
    filterName = nextFilter(filterName);
    lastFilterSwitch = now;
  }
  state = stepGesture(state, hands, now, config);
  ctx.clearRect(0, 0, width, height);
  if (state.surface > 0.02 && hands.length >= 2) {
    scratchCtx.drawImage(video, 0, 0, width, height);
    const polygons = portalPolygons(hands).map((poly) => toPixels(poly, width, height));
    drawPortals(ctx, scratchCtx, polygons, [filterName, nextFilter(filterName)], state.surface, now);
  }
  drawHands(ctx, hands, width, height, state.fade);
  hudMode.textContent = `MODE: ${modeLabel(mode)}  3D MESH  ${state.status.toUpperCase()}  ${detector.status.fps} fps`;
  hudFilter.textContent = `LOOK: ${labelFor(filterName)} + ${labelFor(nextFilter(filterName))} · Next look or pinch thumb and pinky`;
  hudExtra.textContent =
    hands.length < 2 ? "Need both hands in view for the colored portal." : "";
}

function drawDistance(ctx: CanvasRenderingContext2D, now: number, width: number, height: number): void {
  ctx.clearRect(0, 0, width, height);
  if (distanceSkip <= 0) {
    scratchCtx.drawImage(video, 0, 0, width, height);
    const frame = scratchCtx.getImageData(0, 0, width, height);
    lastReading = distance.measure(frame.data, width, height, now);
    distanceSkip = 1;
  } else {
    distanceSkip -= 1;
  }
  const reading = lastReading;
  if (reading) {
    drawMarkerOverlay(ctx, reading, mirrorToggle.checked);
  }
  const stamp =
    reading?.lastUpdatedAt != null ? new Date(reading.lastUpdatedAt).toLocaleTimeString() : "—";
  hudMode.textContent = `MODE: ${modeLabel(mode)}  ${reading?.quality.toUpperCase() ?? "UNAVAILABLE"}  ${detector.status.fps} fps`;
  hudFilter.textContent = reading?.available
    ? `RANGE: ${reading.rangeLabel} · marker ${reading.markerId} · ${reading.calibrated ? "calibrated" : "uncalibrated"} · updated ${stamp}`
    : "RANGE: distance unavailable · print the marker and hold it in view";
  hudExtra.textContent = reading?.reason ?? HOME_CALIBRATION_STEPS[0];
}

function drawRoomMode(
  ctx: CanvasRenderingContext2D,
  hands: TrackedHand[],
  now: number,
  dt: number,
  width: number,
  height: number,
): void {
  if (!room.paused) {
    roomAnim = now;
  }
  room = stepRoom(room, hands, now, dt);
  ctx.clearRect(0, 0, width, height);
  drawRoom(ctx, room, width, height, roomAnim, mirrorToggle.checked);
  drawHands(ctx, hands, width, height, room.open ? 0.55 : 0.85);
  hudMode.textContent = `MODE: ${modeLabel(mode)}  ${room.status}  ${room.palmFacing.toUpperCase()}  ${detector.status.fps} fps`;
  hudFilter.textContent =
    room.power === "room"
      ? "ROOM is locked until Close power. Three fingers at the camera = READY. Twist palm toward you = swap. Pinch to drag."
      : room.power === "rasengan"
        ? "Small spinning ball over the palm. It will not grow into ROOM. Close power to leave."
        : "Claw + palm down = ROOM. Claw + palm up = Rasengan. Flat open palm does nothing here.";
  const seen = hands.length === 0 ? "NO HAND" : `${hands.length} HAND`;
  hudExtra.textContent =
    room.power === "room"
      ? `${seen}. ${room.props[0].label} and ${room.props[1].label} are fake.`
      : room.status === "Create ROOM first"
        ? "Create ROOM first"
        : `${seen}. ${room.status}. Palm guess: ${room.palmFacing}.`;
}

function loop(): void {
  if (!running) {
    return;
  }
  const now = performance.now();
  const dt = Math.min(48, now - lastFrame);
  lastFrame = now;
  const width = video.videoWidth || stage.clientWidth;
  const height = video.videoHeight || stage.clientHeight;
  sizeCanvas(canvas, width, height);
  sizeCanvas(scratch, width, height);
  const ctx = canvas.getContext("2d");
  if (!ctx) {
    return;
  }

  const hands = detectHands(now);
  if (mode === "original") {
    drawOriginal(ctx, hands, now, width, height);
  } else if (mode === "distance") {
    drawDistance(ctx, now, width, height);
  } else {
    drawRoomMode(ctx, hands, now, dt, width, height);
  }
  panel.hidden = true;
  requestAnimationFrame(loop);
}

startBtn.addEventListener("click", () => {
  void start();
});
stopBtn.addEventListener("click", stop);
resetBtn.addEventListener("click", reset);
nextLookBtn.addEventListener("click", () => cycleLook(1));
prevLookBtn.addEventListener("click", () => cycleLook(-1));
mirrorToggle.addEventListener("change", () => {
  stage.classList.toggle("mirror", mirrorToggle.checked);
});
cameraSelect.addEventListener("change", () => {
  if (running) {
    void start();
  }
});
modeOriginalBtn.addEventListener("click", () => setMode("original"));
modeDistanceBtn.addEventListener("click", () => setMode("distance"));
modeRoomBtn.addEventListener("click", () => setMode("room"));
resetRoomBtn.addEventListener("click", () => {
  room = resetRoom(room);
});
pauseRoomBtn.addEventListener("click", () => {
  room = { ...room, paused: !room.paused };
  applyMode();
});
forceRoomBtn.addEventListener("click", () => {
  room = forcePower(room, "room");
  if (!running) {
    void start();
  }
});
forceRasenganBtn.addEventListener("click", () => {
  room = forcePower(room, "rasengan");
  if (!running) {
    void start();
  }
});
calPalmDownBtn.addEventListener("click", () => {
  const hand = lastHands[0];
  if (!hand) {
    return;
  }
  room = {
    ...room,
    calibration: { ...room.calibration, downY: classifyPalm(hand, room.calibration).pitchY },
  };
  localStorage.setItem(PALM_STORE, JSON.stringify(room.calibration));
});
calPalmUpBtn.addEventListener("click", () => {
  const hand = lastHands[0];
  if (!hand) {
    return;
  }
  room = {
    ...room,
    calibration: { ...room.calibration, upY: classifyPalm(hand, room.calibration).pitchY },
  };
  localStorage.setItem(PALM_STORE, JSON.stringify(room.calibration));
});
growMs.addEventListener("input", () => {
  room = { ...room, expandMs: Number(growMs.value) };
});
markerCm.addEventListener("change", () => {
  distance.setMarkerSideCm(Number(markerCm.value));
  localStorage.setItem(CAMERA_STORE, serializeCamera(distance.camera));
});
calibrateBtn.addEventListener("click", () => {
  const meters = Number(tapeM.value);
  lastReading = distance.calibrateFromTape(meters);
  localStorage.setItem(CAMERA_STORE, serializeCamera(distance.camera));
});
printMarkerBtn.addEventListener("click", () => {
  const popup = window.open("", "_blank");
  if (!popup) {
    return;
  }
  popup.document.write(printSheetHtml(0, Number(markerCm.value) || 10));
  popup.document.close();
});

stage.classList.toggle("mirror", mirrorToggle.checked);
stopBtn.disabled = true;
resetBtn.disabled = true;
applyMode();

void camera.listDevices().then(fillCameras).catch(() => undefined);

window.addEventListener("keydown", (event) => {
  if (event.key === "n" || event.key === "N") {
    cycleLook(1);
  }
  if (event.key === "p" || event.key === "P") {
    cycleLook(-1);
  }
  if (event.key === "1") {
    setMode("original");
  }
  if (event.key === "2") {
    setMode("distance");
  }
  if (event.key === "3") {
    setMode("room");
  }
});

window.addEventListener("beforeunload", () => {
  camera.stop();
  detector.close();
});
