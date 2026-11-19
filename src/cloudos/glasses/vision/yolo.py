"""Optional local YOLO detection. Categories only, never durable identities.

The decoder in this file is original. It reads the common YOLOv8 head layout
``(4 + num_classes, num_boxes)`` or already-transposed rows. Nothing here
copies Ultralytics source.

Licenses, checked before wiring a backend:

- ``onnxruntime`` is MIT. A local ``.onnx`` file is the preferred backend.
- The Ultralytics package and its pretrained YOLOv8 weights are AGPL-3.0.
  The ``.pt`` backend imports ``ultralytics`` only when a local weights file
  already exists. This project does not vendor those weights and does not
  download them.
- Class names are COCO categories. A category is not a tracking id and not
  a durable object identity.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cloudos.glasses.vision.detector import Detection
from cloudos.glasses.vision.images import RgbImage

MODEL_ID = "yolo-local"
_INPUT_SIZE = 640

# COCO 80, index order used by YOLOv8. "cup" is 41. These names are categories.
COCO_NAMES = (
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
    "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat",
    "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack",
    "umbrella", "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball",
    "kite", "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket",
    "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple",
    "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza", "donut", "cake",
    "chair", "couch", "potted plant", "bed", "dining table", "toilet", "tv", "laptop",
    "mouse", "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink",
    "refrigerator", "book", "clock", "vase", "scissors", "teddy bear", "hair drier",
    "toothbrush",
)


class YoloUnavailable(RuntimeError):
    """No local weights or runtime. Detection was not invented and nothing was downloaded."""


def yolo_model_status(weights: Path | None = None) -> dict:
    path = Path(weights) if weights else None
    return {
        "model_id": MODEL_ID,
        "onnxruntime_license": "MIT",
        "ultralytics_package_and_pretrained_weights": "AGPL-3.0",
        "decoder": "original YOLOv8 head decode, no Ultralytics source copied",
        "weights_vendored": False,
        "auto_download": False,
        "weights_present": bool(path and path.is_file()),
        "category_is_not_identity": True,
        "hardware_verified": False,
    }


def letterbox(image: RgbImage, size: int = _INPUT_SIZE) -> tuple[RgbImage, float, float, float]:
    """Fit ``image`` inside a square. Returns the square, scale, and padding.

    Padding is the gray border added on the left and top, in input pixels.
    Scale is the factor from original pixels to input pixels.
    """
    if size < 2:
        raise ValueError("YOLO input size is too small")
    scale = min(size / image.width, size / image.height)
    new_w = max(1, int(round(image.width * scale)))
    new_h = max(1, int(round(image.height * scale)))
    new_w = min(size, new_w)
    new_h = min(size, new_h)
    pad_x = (size - new_w) / 2.0
    pad_y = (size - new_h) / 2.0
    canvas = bytearray((114, 114, 114) * (size * size))
    left = int(round(pad_x))
    top = int(round(pad_y))
    for y in range(new_h):
        src_y = min(image.height - 1, int(y / scale))
        for x in range(new_w):
            src_x = min(image.width - 1, int(x / scale))
            src_i = (src_y * image.width + src_x) * 3
            dst_i = ((top + y) * size + (left + x)) * 3
            canvas[dst_i : dst_i + 3] = image.rgb[src_i : src_i + 3]
    return RgbImage(size, size, bytes(canvas)), scale, pad_x, pad_y


def decode_yolov8(
    output,
    *,
    image_width: int,
    image_height: int,
    class_names: tuple[str, ...] = COCO_NAMES,
    conf_threshold: float = 0.25,
    iou_threshold: float = 0.45,
    scale: float = 1.0,
    pad_x: float = 0.0,
    pad_y: float = 0.0,
) -> list[Detection]:
    """Turn one YOLO head into category boxes.

    ``output`` is either rows of ``[cx, cy, w, h, class scores...]`` or the
    channel-first layout ``(4 + classes, boxes)``. Coordinates are in the
    model input's pixel space. ``scale`` and ``pad_*`` undo a letterbox.
    """
    if scale <= 0:
        raise ValueError("letterbox scale must be positive")
    rows = _rows(output, len(class_names))
    kept: list[tuple[float, int, tuple[int, int, int, int]]] = []
    for row in rows:
        scores = row[4:]
        class_id = max(range(len(scores)), key=scores.__getitem__)
        score = float(scores[class_id])
        if score < conf_threshold or score > 1.0:
            continue
        cx, cy, width, height = (float(value) for value in row[:4])
        box = _map_box(
            cx, cy, width, height,
            image_width=image_width,
            image_height=image_height,
            scale=scale,
            pad_x=pad_x,
            pad_y=pad_y,
        )
        if box is None:
            continue
        kept.append((score, class_id, box))
    kept.sort(key=lambda item: item[0], reverse=True)
    chosen: list[tuple[float, int, tuple[int, int, int, int]]] = []
    for candidate in kept:
        if all(_iou(candidate[2], prior[2]) < iou_threshold for prior in chosen):
            chosen.append(candidate)
    found: list[Detection] = []
    for score, class_id, box in chosen:
        found.append(
            Detection(
                label=class_names[class_id],
                confidence=round(score, 4),
                bbox=box,
                model_id=MODEL_ID,
                product_model=False,
            )
        )
    return found


def _rows(output, n_classes: int) -> list[list[float]]:
    data = output.tolist() if hasattr(output, "tolist") else output
    if data is None:
        return []
    # Drop a leading batch dimension of 1.
    if (
        isinstance(data, list)
        and len(data) == 1
        and data
        and isinstance(data[0], list)
        and data[0]
        and isinstance(data[0][0], list)
    ):
        data = data[0]
    if not data:
        return []
    width = 4 + n_classes
    first = data[0]
    if not isinstance(first, list):
        raise ValueError("YOLO output layout was not recognized")
    if len(data) == width and len(first) != width:
        count = len(first)
        return [[float(data[channel][index]) for channel in range(width)] for index in range(count)]
    if len(first) == width:
        return [[float(value) for value in row] for row in data]
    raise ValueError("YOLO output layout was not recognized")


def _map_box(
    cx: float,
    cy: float,
    width: float,
    height: float,
    *,
    image_width: int,
    image_height: int,
    scale: float,
    pad_x: float,
    pad_y: float,
) -> tuple[int, int, int, int] | None:
    x = (cx - width / 2.0 - pad_x) / scale
    y = (cy - height / 2.0 - pad_y) / scale
    w = width / scale
    h = height / scale
    x0 = int(round(x))
    y0 = int(round(y))
    x1 = int(round(x + w))
    y1 = int(round(y + h))
    x0 = max(0, x0)
    y0 = max(0, y0)
    x1 = min(image_width, max(x0, x1))
    y1 = min(image_height, max(y0, y1))
    if x1 <= x0 or y1 <= y0:
        return None
    return (x0, y0, x1 - x0, y1 - y0)


def _iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix0, iy0 = max(ax, bx), max(ay, by)
    ix1, iy1 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    inter = max(0, ix1 - ix0) * max(0, iy1 - iy0)
    union = aw * ah + bw * bh - inter
    if union <= 0:
        return 0.0
    return inter / union


@dataclass
class ScriptedObjectDetector:
    """Replay category boxes. ``simulated`` stays true: this is not YOLO inference."""

    batches: list[list[Detection]]
    simulated: bool = True
    calls: int = 0

    def detect(self, image: RgbImage) -> list[Detection]:
        del image
        if not self.batches:
            self.calls += 1
            return []
        batch = self.batches[min(self.calls, len(self.batches) - 1)]
        self.calls += 1
        return list(batch)


class YoloObjectDetector:
    """Run a local YOLO file when one is present. Never downloads weights."""

    def __init__(self, weights: Path) -> None:
        self.weights = Path(weights)
        self._session = None
        self._input_name = "images"
        self._input_size = _INPUT_SIZE

    @property
    def available(self) -> bool:
        if not self.weights.is_file():
            return False
        if self.weights.suffix == ".onnx":
            return _module_importable("onnxruntime")
        if self.weights.suffix == ".pt":
            return _module_importable("ultralytics")
        return False

    def detect(self, image: RgbImage) -> list[Detection]:
        if not self.weights.is_file():
            raise YoloUnavailable(
                "YOLO weights are not on disk. This pipeline does not download them. "
                "Ultralytics pretrained weights are AGPL-3.0 and are not vendored."
            )
        if self.weights.suffix == ".onnx":
            return self._onnx(image)
        if self.weights.suffix == ".pt":
            return self._ultralytics(image)
        raise YoloUnavailable("YOLO weights must be a local .onnx or .pt file")

    def detections_from_output(
        self,
        output,
        image: RgbImage,
        *,
        input_size: int = _INPUT_SIZE,
    ) -> list[Detection]:
        """Decode a head for ``image`` using this file's letterbox math.

        Tests pass a synthetic head here. No weights file is opened.
        """
        _boxed, scale, pad_x, pad_y = letterbox(image, input_size)
        return decode_yolov8(
            output,
            image_width=image.width,
            image_height=image.height,
            scale=scale,
            pad_x=pad_x,
            pad_y=pad_y,
        )

    def _onnx(self, image: RgbImage) -> list[Detection]:
        try:
            import numpy as np
            import onnxruntime as ort
        except ImportError as exc:
            raise YoloUnavailable(
                "the ONNX backend needs onnxruntime (MIT) and numpy, which are not installed"
            ) from exc
        session = self._load_onnx(ort)
        boxed, scale, pad_x, pad_y = letterbox(image, self._input_size)
        array = np.frombuffer(boxed.rgb, dtype=np.uint8).reshape(self._input_size, self._input_size, 3)
        feed = np.transpose(array.astype(np.float32) / 255.0, (2, 0, 1))[None, ...]
        raw = session.run(None, {self._input_name: feed})[0]
        return decode_yolov8(
            raw,
            image_width=image.width,
            image_height=image.height,
            scale=scale,
            pad_x=pad_x,
            pad_y=pad_y,
        )

    def _load_onnx(self, ort):
        if self._session is not None:
            return self._session
        session = ort.InferenceSession(str(self.weights), providers=["CPUExecutionProvider"])
        model_input = session.get_inputs()[0]
        self._input_name = model_input.name
        shape = list(model_input.shape)
        if len(shape) == 4 and isinstance(shape[2], int) and isinstance(shape[3], int):
            self._input_size = int(shape[2])
        self._session = session
        return session

    def _ultralytics(self, image: RgbImage) -> list[Detection]:
        try:
            import numpy as np
            from ultralytics import YOLO
        except ImportError as exc:
            raise YoloUnavailable(
                "the .pt backend needs ultralytics (AGPL-3.0), which is not installed. "
                "Prefer a local .onnx file with onnxruntime (MIT). Nothing was downloaded."
            ) from exc
        model = YOLO(str(self.weights))
        array = np.frombuffer(image.rgb, dtype=np.uint8).reshape(image.height, image.width, 3)
        results = model.predict(np.ascontiguousarray(array), verbose=False, device="cpu")
        found: list[Detection] = []
        for result in results:
            names = result.names
            for box in result.boxes:
                class_id = int(box.cls[0])
                score = float(box.conf[0])
                if not 0.0 < score <= 1.0:
                    continue
                x0, y0, x1, y1 = (float(value) for value in box.xyxy[0].tolist())
                mapped = _map_box(
                    (x0 + x1) / 2.0,
                    (y0 + y1) / 2.0,
                    max(0.0, x1 - x0),
                    max(0.0, y1 - y0),
                    image_width=image.width,
                    image_height=image.height,
                    scale=1.0,
                    pad_x=0.0,
                    pad_y=0.0,
                )
                if mapped is None:
                    continue
                label = str(names.get(class_id, class_id))
                found.append(
                    Detection(
                        label=label,
                        confidence=round(min(1.0, score), 4),
                        bbox=mapped,
                        model_id=MODEL_ID,
                        product_model=False,
                    )
                )
        return found


def _module_importable(name: str) -> bool:
    try:
        __import__(name)
    except ImportError:
        return False
    return True
