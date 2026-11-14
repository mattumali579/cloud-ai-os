import { HAND_CONNECTIONS, type TrackedHand } from "./landmarks";
import type { GestureState, PanelPose } from "./gesture";

export function sizeCanvas(canvas: HTMLCanvasElement, width: number, height: number): void {
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
}

function toPx(point: { x: number; y: number }, width: number, height: number): { x: number; y: number } {
  return { x: point.x * width, y: point.y * height };
}

export function drawHands(
  ctx: CanvasRenderingContext2D,
  hands: TrackedHand[],
  width: number,
  height: number,
  fade: number,
): void {
  ctx.save();
  ctx.globalAlpha = fade;
  for (const hand of hands) {
    const color = hand.handedness === "Left" ? "#3dff7a" : "#ff3b3b";
    ctx.strokeStyle = color;
    ctx.lineWidth = Math.max(2, width * 0.0024);
    ctx.lineCap = "round";
    for (const [a, b] of HAND_CONNECTIONS) {
      const from = hand.landmarks[a];
      const to = hand.landmarks[b];
      if (!from || !to) {
        continue;
      }
      const p = toPx(from, width, height);
      const q = toPx(to, width, height);
      ctx.beginPath();
      ctx.moveTo(p.x, p.y);
      ctx.lineTo(q.x, q.y);
      ctx.stroke();
    }
    for (const landmark of hand.landmarks) {
      const p = toPx(landmark, width, height);
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.arc(p.x, p.y, Math.max(3.5, width * 0.006), 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = "rgba(255,255,255,0.85)";
      ctx.lineWidth = 1;
      ctx.stroke();
    }
  }
  ctx.restore();
}

export function panelCorners(pose: PanelPose): { x: number; y: number }[] {
  const hw = pose.width / 2;
  const hh = pose.height / 2;
  const cos = Math.cos(pose.rotation);
  const sin = Math.sin(pose.rotation);
  const raw = [
    { x: -hw, y: -hh },
    { x: hw, y: -hh },
    { x: hw, y: hh },
    { x: -hw, y: hh },
  ];
  return raw.map((point) => ({
    x: pose.cx + point.x * cos - point.y * sin,
    y: pose.cy + point.x * sin + point.y * cos,
  }));
}

export function drawPanelOutline(
  ctx: CanvasRenderingContext2D,
  pose: PanelPose,
  width: number,
  height: number,
  gripping: boolean,
): void {
  const corners = panelCorners(pose).map((point) => toPx(point, width, height));
  ctx.save();
  ctx.beginPath();
  ctx.moveTo(corners[0].x, corners[0].y);
  for (let i = 1; i < corners.length; i += 1) {
    ctx.lineTo(corners[i].x, corners[i].y);
  }
  ctx.closePath();
  ctx.fillStyle = gripping ? "rgba(4, 10, 18, 0.18)" : "rgba(4, 10, 18, 0.08)";
  ctx.fill();
  ctx.strokeStyle = gripping ? "rgba(190, 255, 255, 0.95)" : "rgba(210, 230, 240, 0.8)";
  ctx.lineWidth = gripping ? 2.4 : 1.6;
  ctx.shadowColor = "rgba(80, 220, 255, 0.55)";
  ctx.shadowBlur = gripping ? 18 : 8;
  ctx.stroke();
  ctx.restore();
}

export function applyPanelStyle(
  el: HTMLElement,
  pose: PanelPose,
  width: number,
  height: number,
  gripping: boolean,
): void {
  const px = pose.cx * width;
  const py = pose.cy * height;
  const w = pose.width * width;
  const h = pose.height * height;
  el.style.width = `${w}px`;
  el.style.height = `${h}px`;
  el.style.left = `${px}px`;
  el.style.top = `${py}px`;
  el.style.opacity = "1";
  el.style.borderColor = gripping ? "rgba(170,255,255,0.95)" : "rgba(220,235,245,0.7)";
  el.style.boxShadow = gripping
    ? "0 0 24px rgba(70,220,255,0.45), inset 0 0 40px rgba(0,40,70,0.35)"
    : "0 0 16px rgba(40,120,160,0.28), inset 0 0 30px rgba(0,20,40,0.28)";
  el.style.transform = `translate(-50%, -50%) perspective(900px) rotateX(${pose.tilt * 42}deg) rotateZ(${pose.rotation}rad)`;
}

export function drawStatus(
  ctx: CanvasRenderingContext2D,
  state: GestureState,
  fps: number,
  detectMs: number,
  delegate: string,
): void {
  const lines = [
    `${state.status}${state.gripping ? " · grip" : ""}`,
    `${fps} fps · ${detectMs.toFixed(0)} ms · ${delegate}`,
  ];
  ctx.save();
  ctx.font = "12px ui-monospace, Consolas, monospace";
  ctx.fillStyle = "rgba(0,0,0,0.45)";
  ctx.fillRect(12, 12, 230, 40);
  ctx.fillStyle = "#d9f6ff";
  ctx.fillText(lines[0], 20, 28);
  ctx.fillStyle = "#9ad7e6";
  ctx.fillText(lines[1], 20, 44);
  ctx.restore();
}
