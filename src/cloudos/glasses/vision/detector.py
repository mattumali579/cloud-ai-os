"""Coarse object labels from pixel regions. Not a product-model identifier."""

from __future__ import annotations

from dataclasses import dataclass

from cloudos.glasses.vision.images import RgbImage

MODEL_ID = "coarse-region-v1"


@dataclass(frozen=True)
class Detection:
    label: str
    confidence: float
    bbox: tuple[int, int, int, int]
    model_id: str = MODEL_ID
    product_model: bool = False

    def __post_init__(self) -> None:
        if self.product_model:
            raise ValueError("this detector must not claim a product model")
        if not 0.0 <= self.confidence <= 0.93:
            raise ValueError("coarse confidence stays at or below 0.93")


def _hue_name(r: int, g: int, b: int) -> str | None:
    mx = max(r, g, b)
    mn = min(r, g, b)
    if mx - mn < 40 or mx < 80:
        return None
    delta = mx - mn
    if mx == r:
        hue = ((g - b) / delta) % 6
    elif mx == g:
        hue = ((b - r) / delta) + 2
    else:
        hue = ((r - g) / delta) + 4
    degrees = hue * 60
    if degrees < 20 or degrees >= 340:
        return "red"
    if degrees < 45:
        return "orange"
    if degrees < 70:
        return "yellow"
    if degrees < 160:
        return "green"
    if degrees < 200:
        return "cyan"
    if degrees < 260:
        return "blue"
    if degrees < 320:
        return "purple"
    return "red"


class CoarseRegionDetector:
    """Group saturated pixels by hue and return one box per color.

    Two objects of the same color become one region. The label is a color
    plus the word "object", which is a look of the scene, not a SKU.
    """

    def detect(self, image: RgbImage) -> list[Detection]:
        buckets: dict[str, list[int]] = {}
        for y in range(image.height):
            for x in range(image.width):
                name = _hue_name(*image.pixel(x, y))
                if name is None:
                    continue
                buckets.setdefault(name, []).append(y * image.width + x)
        minimum = max(16, (image.width * image.height) // 100)
        found: list[Detection] = []
        for name, cells in buckets.items():
            if len(cells) < minimum:
                continue
            xs = [cell % image.width for cell in cells]
            ys = [cell // image.width for cell in cells]
            x0, x1 = min(xs), max(xs) + 1
            y0, y1 = min(ys), max(ys) + 1
            fraction = len(cells) / (image.width * image.height)
            confidence = round(min(0.93, 0.55 + fraction), 2)
            found.append(
                Detection(
                    label=f"{name} object",
                    confidence=confidence,
                    bbox=(x0, y0, x1 - x0, y1 - y0),
                )
            )
        found.sort(key=lambda item: item.bbox[2] * item.bbox[3], reverse=True)
        return found
