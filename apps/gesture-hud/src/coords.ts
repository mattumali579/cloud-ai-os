export type Pixel = { x: number; y: number };

export function mirrorPixel(point: Pixel, width: number, mirrored: boolean): Pixel {
  if (!mirrored) {
    return { x: point.x, y: point.y };
  }
  return { x: width - point.x, y: point.y };
}

export function drawReadableText(
  ctx: CanvasRenderingContext2D,
  text: string,
  x: number,
  y: number,
  mirrored: boolean,
): void {
  ctx.save();
  ctx.translate(x, y);
  if (mirrored) {
    ctx.scale(-1, 1);
  }
  ctx.fillText(text, 0, 0);
  ctx.restore();
}
