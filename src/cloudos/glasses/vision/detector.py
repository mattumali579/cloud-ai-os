"""Local free/open object detection. Class labels ≠ exact product identity."""

from __future__ import annotations

import io
from collections import Counter

from PIL import Image

from cloudos.glasses.vision.types import CaptureFrame, Detection

# Synthetic RGB centroids used by BlobClassDetector for fixture/demo frames.
_CLASS_COLORS: dict[str, tuple[int, int, int]] = {
    "cup": (220, 40, 40),
    "bottle": (40, 80, 220),
    "book": (40, 180, 80),
    "person_region": (180, 180, 40),
}


def _nearest_class(rgb: tuple[int, int, int]) -> tuple[str, float]:
    best_label = "object"
    best_dist = 10**9
    for label, center in _CLASS_COLORS.items():
        dist = sum((a - b) ** 2 for a, b in zip(rgb, center, strict=True))
        if dist < best_dist:
            best_dist = dist
            best_label = label
    # Map squared RGB distance into a soft confidence; far blobs stay low.
    confidence = max(0.35, min(0.95, 1.0 - (best_dist ** 0.5) / 400.0))
    if best_dist > 180**2:
        return "object", max(0.35, confidence * 0.7)
    return best_label, confidence


class BlobClassDetector:
    """Lightweight local detector (no paid API).

    Finds non-dark regions and maps mean color to a coarse class label.
    Always reports identity_kind=class_label — never invents SKU/product model IDs.
    Optional OpenCV/YOLO backends can replace this when installed; same Detection contract.
    """

    def detect(self, frame: CaptureFrame) -> list[Detection]:
        image = Image.open(io.BytesIO(frame.bytes_png)).convert("RGB")
        w, h = image.size
        # Pillow 12 still supports getdata(); avoid flat-buffer ambiguity.
        pixels = [image.getpixel((x, y)) for y in range(h) for x in range(w)]
        # Collect pixels brighter than near-black background.
        bright = [(i, px) for i, px in enumerate(pixels) if sum(px) > 90]
        if not bright:
            return []
        xs = [i % w for i, _ in bright]
        ys = [i // w for i, _ in bright]
        bbox = (min(xs), min(ys), max(xs), max(ys))
        # Dominant color among bright pixels (quantized).
        quantized = [((r // 32) * 32, (g // 32) * 32, (b // 32) * 32) for _, (r, g, b) in bright]
        (r, g, b), _count = Counter(quantized).most_common(1)[0]
        label, confidence = _nearest_class((r, g, b))
        return [
            Detection(
                label=label,
                confidence=round(confidence, 3),
                bbox=bbox,
                identity_kind="class_label",
                product_identity=None,
            )
        ]

    def speak(self, detections: list[Detection]) -> str:
        if not detections:
            return "I do not see a clear object class in that frame."
        parts = []
        for det in detections[:3]:
            pct = int(round(det.confidence * 100))
            parts.append(f"{det.label} at about {pct} percent confidence")
        joined = "; ".join(parts)
        return (
            f"I see {joined}. "
            "These are class labels, not exact product model identification."
        )
