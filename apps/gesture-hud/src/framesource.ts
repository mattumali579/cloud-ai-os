/** Camera adapters. Later glasses/phone sources reuse this without changing gesture math. */

export type FrameSourceKind = "webcam" | "file" | "glasses-placeholder";

export interface StartOptions {
  deviceId?: string;
}

export interface FrameSource {
  readonly kind: FrameSourceKind;
  listDevices(): Promise<MediaDeviceInfo[]>;
  start(options?: StartOptions): Promise<HTMLVideoElement>;
  stop(): void;
  getVideo(): HTMLVideoElement | null;
}

export class GlassesFrameSource implements FrameSource {
  readonly kind = "glasses-placeholder" as const;

  async listDevices(): Promise<MediaDeviceInfo[]> {
    return [];
  }

  async start(): Promise<HTMLVideoElement> {
    throw new Error(
      "Glasses/phone camera is not part of this webcam HUD. Use the webcam source.",
    );
  }

  stop(): void {}

  getVideo(): HTMLVideoElement | null {
    return null;
  }
}
