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
import { nextFilter, type FilterName } from "./filters";
import { drawPortals, portalPolygons, toPixels } from "./portal";
import { drawHands, sizeCanvas } from "./render";
import { thumbPinkyDistance, type TrackedHand } from "./landmarks";
import "./style.css";

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

const camera = new WebcamFrameSource(video);
const glasses = new GlassesFrameSource();
void glasses;
const detector = new HandDetector();
const config: GestureConfig = { ...DEFAULT_GESTURE };
let state: GestureState = createGestureState();
let running = false;
let skip = 0;
let lastHands: TrackedHand[] = [];
let filterName: FilterName = "thermal";
let lastFilterSwitch = 0;
const scratch = document.createElement("canvas");
const scratchCtx = scratch.getContext("2d")!;

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
  state = resetPanel(state);
}

function loop(): void {
  if (!running) {
    return;
  }
  const now = performance.now();
  const width = video.videoWidth || stage.clientWidth;
  const height = video.videoHeight || stage.clientHeight;
  sizeCanvas(canvas, width, height);
  sizeCanvas(scratch, width, height);
  const ctx = canvas.getContext("2d");
  if (!ctx) {
    return;
  }

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

  if (hands.some((hand) => thumbPinkyDistance(hand.landmarks) < 0.048) && now - lastFilterSwitch > 400) {
    filterName = nextFilter(filterName);
    lastFilterSwitch = now;
  }

  state = stepGesture(state, hands, now, config);
  ctx.clearRect(0, 0, width, height);
  if (state.surface > 0.02 && hands.length >= 2) {
    scratchCtx.drawImage(video, 0, 0, width, height);
    const polygons = portalPolygons(hands).map((poly) => toPixels(poly, width, height));
    drawPortals(ctx, scratchCtx, polygons, [filterName, nextFilter(filterName)], state.surface);
  }
  drawHands(ctx, hands, width, height, state.fade);
  hudMode.textContent = `MODE: 3D MESH  ${state.status.toUpperCase()}  ${detector.status.fps} fps`;
  hudFilter.textContent = `FILTER: ${filterName.toUpperCase()} · pinch thumb and pinky or press N`;
  panel.hidden = true;
  requestAnimationFrame(loop);
}

startBtn.addEventListener("click", () => {
  void start();
});
stopBtn.addEventListener("click", stop);
resetBtn.addEventListener("click", reset);
mirrorToggle.addEventListener("change", () => {
  stage.classList.toggle("mirror", mirrorToggle.checked);
});
cameraSelect.addEventListener("change", () => {
  if (running) {
    void start();
  }
});

stage.classList.toggle("mirror", mirrorToggle.checked);
stopBtn.disabled = true;
resetBtn.disabled = true;

void camera.listDevices().then(fillCameras).catch(() => undefined);

window.addEventListener("keydown", (event) => {
  if (event.key === "n" || event.key === "N") {
    filterName = nextFilter(filterName);
  }
});

window.addEventListener("beforeunload", () => {
  camera.stop();
  detector.close();
});
