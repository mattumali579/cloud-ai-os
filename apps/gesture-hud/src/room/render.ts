import { drawReadableText } from "../coords";
import { RASENGAN_MAX } from "./handPose";
import type { RoomState } from "./state";

function projectSphere(
  cx: number,
  cy: number,
  radius: number,
  theta: number,
  phi: number,
): { x: number; y: number; z: number } {
  const x = Math.sin(phi) * Math.cos(theta);
  const y = Math.cos(phi);
  const z = Math.sin(phi) * Math.sin(theta);
  return {
    x: cx + x * radius,
    y: cy + y * radius * 0.72,
    z,
  };
}

export function rasenganPixelRadius(state: RoomState, width: number, height: number): number {
  const cap = Math.min(width, height) * RASENGAN_MAX;
  const raw = state.rasenganRadius * Math.min(width, height);
  return Math.max(18, Math.min(cap, raw));
}

export function drawRasengan(
  ctx: CanvasRenderingContext2D,
  state: RoomState,
  width: number,
  height: number,
  now: number,
): void {
  if (state.rasenganAmount < 0.02 || state.power === "room") {
    return;
  }
  const cx = state.rasenganCenter.x * width;
  const cy = state.rasenganCenter.y * height;
  const r = rasenganPixelRadius(state, width, height);
  const t = now * 0.006;
  ctx.save();
  ctx.globalAlpha = state.rasenganAmount;

  ctx.shadowColor = "rgba(80, 210, 255, 0.95)";
  ctx.shadowBlur = r * 0.9;
  const body = ctx.createRadialGradient(cx - r * 0.25, cy - r * 0.3, r * 0.08, cx, cy, r);
  body.addColorStop(0, "rgba(255,255,255,1)");
  body.addColorStop(0.18, "rgba(210,250,255,0.95)");
  body.addColorStop(0.45, "rgba(60,190,255,0.85)");
  body.addColorStop(0.78, "rgba(20,90,220,0.55)");
  body.addColorStop(1, "rgba(0,20,80,0.05)");
  ctx.fillStyle = body;
  ctx.beginPath();
  ctx.ellipse(cx, cy, r, r * 0.92, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.shadowBlur = 0;

  ctx.globalCompositeOperation = "lighter";
  for (let ring = 0; ring < 5; ring += 1) {
    const speed = t * (2.4 + ring * 0.55);
    ctx.strokeStyle = `rgba(180, 245, 255, ${0.35 + ring * 0.08})`;
    ctx.lineWidth = 1.4 + (ring % 2);
    ctx.beginPath();
    ctx.ellipse(cx, cy, r * (0.35 + ring * 0.12), r * (0.18 + ring * 0.08), speed, 0, Math.PI * 2);
    ctx.stroke();
    ctx.beginPath();
    ctx.ellipse(cx, cy, r * (0.18 + ring * 0.1), r * (0.38 + ring * 0.1), -speed * 1.3, 0, Math.PI * 2);
    ctx.stroke();
  }

  ctx.strokeStyle = "rgba(255,255,255,0.7)";
  ctx.lineWidth = 1.8;
  for (let i = 0; i < 8; i += 1) {
    ctx.beginPath();
    for (let a = 0; a <= 28; a += 1) {
      const u = a / 28;
      const ang = t * 3.2 + i * 0.8 + u * Math.PI * 2;
      const rad = r * (0.15 + u * 0.78);
      const x = cx + Math.cos(ang) * rad;
      const y = cy + Math.sin(ang * 1.15 + i) * rad * 0.78;
      if (a === 0) {
        ctx.moveTo(x, y);
      } else {
        ctx.lineTo(x, y);
      }
    }
    ctx.stroke();
  }

  ctx.strokeStyle = "rgba(140, 220, 255, 0.8)";
  ctx.lineWidth = 1.1;
  for (let i = 0; i < 6; i += 1) {
    const a0 = t * 5 + i * 1.1;
    ctx.beginPath();
    ctx.moveTo(cx + Math.cos(a0) * r * 0.2, cy + Math.sin(a0) * r * 0.2);
    ctx.quadraticCurveTo(
      cx + Math.cos(a0 + 0.8) * r * 0.9,
      cy + Math.sin(a0 + 1.1) * r * 0.7,
      cx + Math.cos(a0 + 1.7) * r * 0.55,
      cy + Math.sin(a0 + 1.4) * r * 0.5,
    );
    ctx.stroke();
  }

  ctx.fillStyle = "rgba(255,255,255,0.9)";
  for (let i = 0; i < 28; i += 1) {
    const ang = t * 4.5 + i * 0.45;
    const rad = r * (0.2 + ((i * 13) % 9) / 12);
    ctx.globalAlpha = state.rasenganAmount * (0.35 + (i % 3) * 0.2);
    ctx.beginPath();
    ctx.arc(cx + Math.cos(ang) * rad, cy + Math.sin(ang * 1.25) * rad * 0.82, 1.4 + (i % 3), 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.globalAlpha = state.rasenganAmount;
  ctx.globalCompositeOperation = "source-over";
  ctx.fillStyle = "rgba(255,255,255,0.95)";
  ctx.beginPath();
  ctx.arc(cx - r * 0.18, cy - r * 0.22, r * 0.16, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}

export function drawRoom(
  ctx: CanvasRenderingContext2D,
  state: RoomState,
  width: number,
  height: number,
  now: number,
  mirrored: boolean,
): void {
  drawRasengan(ctx, state, width, height, now);
  const amount = state.openAmount;
  if (amount < 0.02) {
    return;
  }
  const cx = state.displayCenter.x * width;
  const cy = state.displayCenter.y * height;
  const fill = Math.max(
    Math.hypot(cx, cy),
    Math.hypot(width - cx, cy),
    Math.hypot(cx, height - cy),
    Math.hypot(width - cx, height - cy),
  );
  const grow = state.maxRadius > 0 ? Math.min(1, state.displayRadius / state.maxRadius) : 0;
  const radius = fill * Math.max(grow, 0.04);
  const t = now * 0.001;

  ctx.save();
  ctx.globalAlpha = amount;
  const veil = ctx.createRadialGradient(cx, cy, radius * 0.08, cx, cy, radius * 1.15);
  veil.addColorStop(0, "rgba(40, 180, 255, 0.28)");
  veil.addColorStop(0.45, "rgba(10, 70, 160, 0.16)");
  veil.addColorStop(1, "rgba(0, 10, 30, 0.02)");
  ctx.fillStyle = veil;
  ctx.beginPath();
  ctx.ellipse(cx, cy, radius, radius * 0.78, 0, 0, Math.PI * 2);
  ctx.fill();

  const core = ctx.createRadialGradient(cx, cy, 0, cx, cy, radius * 0.22);
  core.addColorStop(0, "rgba(180, 245, 255, 0.95)");
  core.addColorStop(0.35, "rgba(40, 160, 255, 0.7)");
  core.addColorStop(1, "rgba(0, 40, 120, 0)");
  ctx.fillStyle = core;
  ctx.beginPath();
  ctx.arc(cx, cy, Math.min(radius * 0.12, 48), 0, Math.PI * 2);
  ctx.fill();

  ctx.strokeStyle = "rgba(120, 220, 255, 0.55)";
  ctx.lineWidth = 1.2;
  for (let i = 1; i <= 6; i += 1) {
    const k = i / 6;
    ctx.beginPath();
    ctx.ellipse(cx, cy, radius * k, radius * 0.78 * k, 0, 0, Math.PI * 2);
    ctx.stroke();
  }

  ctx.strokeStyle = "rgba(90, 210, 255, 0.75)";
  ctx.lineWidth = 2;
  for (let m = 0; m < 3; m += 1) {
    const rot = t * 0.7 + m * 2.094;
    ctx.beginPath();
    for (let a = 0; a <= 32; a += 1) {
      const phi = (a / 32) * Math.PI;
      const p = projectSphere(cx, cy, radius, rot, phi);
      if (a === 0) {
        ctx.moveTo(p.x, p.y);
      } else {
        ctx.lineTo(p.x, p.y);
      }
    }
    ctx.stroke();
  }

  ctx.fillStyle = "rgba(160, 240, 255, 0.9)";
  for (let i = 0; i < 42; i += 1) {
    const golden = Math.PI * (3 - Math.sqrt(5));
    const phi = Math.acos(1 - (2 * (i + 0.5)) / 42);
    const theta = golden * i + t * 0.9;
    const p = projectSphere(cx, cy, radius, theta, phi);
    const size = 1.4 + (p.z + 1) * 1.6;
    ctx.globalAlpha = amount * (0.25 + (p.z + 1) * 0.35);
    ctx.beginPath();
    ctx.arc(p.x, p.y, size, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.globalAlpha = amount;

  ctx.strokeStyle = "rgba(180, 255, 255, 0.95)";
  ctx.shadowColor = "rgba(50, 200, 255, 0.9)";
  ctx.shadowBlur = 18;
  ctx.lineWidth = 3;
  ctx.beginPath();
  ctx.ellipse(cx, cy, radius, radius * 0.78, 0, 0, Math.PI * 2);
  ctx.stroke();
  ctx.shadowBlur = 0;

  if (state.shambles === "ready") {
    ctx.fillStyle = "rgba(80, 200, 255, 0.95)";
    ctx.font = "22px ui-monospace, Consolas, monospace";
    ctx.textAlign = "center";
    drawReadableText(ctx, "SHAMBLES READY", cx, cy - 28, mirrored);
  }

  if (state.swapTrail && state.swapFlash > 0) {
    ctx.globalAlpha = amount * state.swapFlash;
    ctx.strokeStyle = "rgba(255, 255, 255, 0.9)";
    ctx.lineWidth = 4;
    const pairs = [
      [state.swapTrail.from, state.swapTrail.to],
      [state.swapTrail.otherFrom, state.swapTrail.otherTo],
    ];
    for (const [from, to] of pairs) {
      ctx.beginPath();
      ctx.moveTo(from.x * width, from.y * height);
      ctx.lineTo(to.x * width, to.y * height);
      ctx.stroke();
    }
    ctx.fillStyle = "rgba(255,255,255,0.55)";
    ctx.beginPath();
    ctx.arc(cx, cy, 36 * state.swapFlash, 0, Math.PI * 2);
    ctx.fill();
  }

  if (state.open) {
    drawProps(ctx, state, width, height, mirrored, amount);
  }
  ctx.restore();
}

function drawProps(
  ctx: CanvasRenderingContext2D,
  state: RoomState,
  width: number,
  height: number,
  mirrored: boolean,
  amount: number,
): void {
  const ordered = [...state.props].sort((a, b) => a.depth - b.depth);
  for (const prop of ordered) {
    const x = prop.x * width;
    const y = prop.y * height;
    const s = 28 + prop.depth * 18;
    ctx.globalAlpha = amount;
    if (prop.id === "cube") {
      drawCube(ctx, x, y, s, state.draggingId === "cube");
      ctx.fillStyle = "#9ee7ff";
    } else {
      drawBall(ctx, x, y, s * 0.72, state.draggingId === "sphere");
      ctx.fillStyle = "#ffb4e6";
    }
    ctx.font = "13px ui-monospace, Consolas, monospace";
    ctx.textAlign = "center";
    drawReadableText(ctx, prop.label, x, y + s + 16, mirrored);
  }
}

function drawCube(ctx: CanvasRenderingContext2D, x: number, y: number, s: number, hot: boolean): void {
  const h = s * 0.38;
  ctx.fillStyle = hot ? "rgba(80, 200, 255, 0.85)" : "rgba(20, 140, 255, 0.72)";
  ctx.strokeStyle = "rgba(180, 245, 255, 0.95)";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(x, y - h);
  ctx.lineTo(x + s * 0.55, y - h * 0.35);
  ctx.lineTo(x + s * 0.55, y + h * 0.85);
  ctx.lineTo(x, y + h * 1.2);
  ctx.lineTo(x - s * 0.55, y + h * 0.85);
  ctx.lineTo(x - s * 0.55, y - h * 0.35);
  ctx.closePath();
  ctx.fill();
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(x, y - h);
  ctx.lineTo(x, y + h * 0.2);
  ctx.lineTo(x + s * 0.55, y + h * 0.85);
  ctx.moveTo(x, y + h * 0.2);
  ctx.lineTo(x - s * 0.55, y + h * 0.85);
  ctx.stroke();
}

function drawBall(ctx: CanvasRenderingContext2D, x: number, y: number, r: number, hot: boolean): void {
  const g = ctx.createRadialGradient(x - r * 0.3, y - r * 0.3, r * 0.1, x, y, r);
  g.addColorStop(0, "#ffe6f8");
  g.addColorStop(0.4, hot ? "#ff7ad9" : "#ff4fb8");
  g.addColorStop(1, "#6a1048");
  ctx.fillStyle = g;
  ctx.beginPath();
  ctx.arc(x, y, r, 0, Math.PI * 2);
  ctx.fill();
  ctx.strokeStyle = "rgba(255, 200, 240, 0.9)";
  ctx.lineWidth = 2;
  ctx.stroke();
}
