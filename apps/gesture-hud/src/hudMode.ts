export const HUD_MODES = ["original", "distance", "room"] as const;
export type HudMode = (typeof HUD_MODES)[number];

export function modeLabel(mode: HudMode): string {
  if (mode === "original") {
    return "ORIGINAL FILTER";
  }
  if (mode === "distance") {
    return "DISTANCE";
  }
  return "POWERS";
}
