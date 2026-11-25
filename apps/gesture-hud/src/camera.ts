import type { FrameSource, StartOptions } from "./framesource";

export class WebcamFrameSource implements FrameSource {
  readonly kind = "webcam" as const;
  private stream: MediaStream | null = null;
  private video: HTMLVideoElement | null = null;

  constructor(video: HTMLVideoElement) {
    this.video = video;
  }

  async listDevices(): Promise<MediaDeviceInfo[]> {
    if (!navigator.mediaDevices?.enumerateDevices) {
      return [];
    }
    const devices = await navigator.mediaDevices.enumerateDevices();
    return devices.filter((device) => device.kind === "videoinput");
  }

  async start(options: StartOptions = {}): Promise<HTMLVideoElement> {
    if (!this.video) {
      throw new Error("No video element is attached.");
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error("This browser cannot open a camera.");
    }
    this.stop();
    const constraints: MediaStreamConstraints = {
      audio: false,
      video: {
        deviceId: options.deviceId ? { exact: options.deviceId } : undefined,
        width: { ideal: 1280 },
        height: { ideal: 720 },
        frameRate: { ideal: 30, max: 30 },
        facingMode: options.deviceId ? undefined : "user",
      },
    };
    this.stream = await navigator.mediaDevices.getUserMedia(constraints);
    this.video.srcObject = this.stream;
    this.video.muted = true;
    this.video.playsInline = true;
    await this.video.play();
    return this.video;
  }

  stop(): void {
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = null;
    if (this.video) {
      this.video.pause();
      this.video.srcObject = null;
    }
  }

  getVideo(): HTMLVideoElement | null {
    return this.video;
  }
}
