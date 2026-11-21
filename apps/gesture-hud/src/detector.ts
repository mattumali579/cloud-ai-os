import { FilesetResolver, HandLandmarker } from "@mediapipe/tasks-vision";
import type { TrackedHand } from "./landmarks";

const WASM = "/mediapipe/wasm";
const MODEL = "/models/hand_landmarker.task";

export type DetectorStatus = {
  ready: boolean;
  fps: number;
  lastMs: number;
  delegate: "GPU" | "CPU";
  error: string | null;
};

export class HandDetector {
  private landmarker: HandLandmarker | null = null;
  private lastVideoTime = -1;
  private lastStamp = 0;
  private frames = 0;
  status: DetectorStatus = {
    ready: false,
    fps: 0,
    lastMs: 0,
    delegate: "CPU",
    error: null,
  };

  async load(): Promise<void> {
    const vision = await FilesetResolver.forVisionTasks(WASM);
    const options = {
      baseOptions: {
        modelAssetPath: MODEL,
        delegate: "GPU" as const,
      },
      runningMode: "VIDEO" as const,
      numHands: 2,
      minHandDetectionConfidence: 0.5,
      minHandPresenceConfidence: 0.5,
      minTrackingConfidence: 0.5,
    };
    try {
      this.landmarker = await HandLandmarker.createFromOptions(vision, options);
      this.status.delegate = "GPU";
    } catch {
      this.landmarker = await HandLandmarker.createFromOptions(vision, {
        ...options,
        baseOptions: { ...options.baseOptions, delegate: "CPU" },
      });
      this.status.delegate = "CPU";
    }
    this.status.ready = true;
  }

  detect(video: HTMLVideoElement, now: number): TrackedHand[] | null {
    if (!this.landmarker || video.readyState < 2) {
      return null;
    }
    if (video.currentTime === this.lastVideoTime) {
      return null;
    }
    this.lastVideoTime = video.currentTime;
    const started = performance.now();
    const result = this.landmarker.detectForVideo(video, now);
    this.status.lastMs = performance.now() - started;
    this.frames += 1;
    if (now - this.lastStamp >= 1000) {
      this.status.fps = this.frames;
      this.frames = 0;
      this.lastStamp = now;
    }
    const hands: TrackedHand[] = [];
    const landmarks = result.landmarks ?? [];
    const handedness = result.handedness ?? [];
    for (let i = 0; i < landmarks.length; i += 1) {
      const label = (handedness[i]?.[0]?.categoryName ?? "").toLowerCase().startsWith("left")
        ? "Left"
        : "Right";
      const score = handedness[i]?.[0]?.score ?? 0;
      hands.push({
        handedness: label,
        score,
        landmarks: landmarks[i].map((point) => ({ x: point.x, y: point.y, z: point.z })),
      });
    }
    return hands;
  }

  close(): void {
    this.landmarker?.close();
    this.landmarker = null;
    this.status.ready = false;
  }
}
