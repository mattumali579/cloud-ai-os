"""Tiny RGB images. PPM is built in. JPEG, PNG, and HEIC stay optional."""

from __future__ import annotations

from dataclasses import dataclass


class ImageError(ValueError):
    """The bytes are not a picture this pipeline can read."""


@dataclass(frozen=True)
class RgbImage:
    width: int
    height: int
    rgb: bytes

    def __post_init__(self) -> None:
        expected = self.width * self.height * 3
        if self.width < 1 or self.height < 1 or len(self.rgb) != expected:
            raise ImageError("image dimensions do not match the pixel buffer")

    def pixel(self, x: int, y: int) -> tuple[int, int, int]:
        i = (y * self.width + x) * 3
        return self.rgb[i], self.rgb[i + 1], self.rgb[i + 2]


def solid(width: int, height: int, color: tuple[int, int, int]) -> RgbImage:
    pixel = bytes(color)
    return RgbImage(width, height, pixel * (width * height))


def paint_rect(
    image: RgbImage,
    x: int,
    y: int,
    width: int,
    height: int,
    color: tuple[int, int, int],
) -> RgbImage:
    """Return a copy with an axis-aligned rectangle painted in ``color``."""
    buf = bytearray(image.rgb)
    x0 = max(0, x)
    y0 = max(0, y)
    x1 = min(image.width, x + width)
    y1 = min(image.height, y + height)
    pixel = bytes(color)
    for yy in range(y0, y1):
        row = yy * image.width
        for xx in range(x0, x1):
            i = (row + xx) * 3
            buf[i : i + 3] = pixel
    return RgbImage(image.width, image.height, bytes(buf))


def to_ppm(image: RgbImage) -> bytes:
    header = f"P6\n{image.width} {image.height}\n255\n".encode("ascii")
    return header + image.rgb


def parse_ppm(data: bytes) -> RgbImage:
    if not data.startswith(b"P6"):
        raise ImageError("expected a binary PPM (P6)")
    rest = data[2:]
    if rest[:1] not in (b"\n", b" ", b"\t", b"\r"):
        raise ImageError("malformed PPM header")
    header, _sep, pixels = rest.partition(b"\n255\n")
    if not pixels:
        # Single-line headers are written by to_ppm; also accept a split header.
        parts = rest.split(None, 3)
        if len(parts) < 4 or parts[2] != b"255":
            raise ImageError("malformed PPM header")
        width = int(parts[0])
        height = int(parts[1])
        pixels = parts[3]
        if pixels[:1] in (b"\n", b"\r"):
            pixels = pixels[1:]
    else:
        tokens = header.split()
        if len(tokens) < 2:
            raise ImageError("malformed PPM header")
        width = int(tokens[0])
        height = int(tokens[1])
    expected = width * height * 3
    if len(pixels) < expected:
        raise ImageError("PPM pixel buffer is short")
    return RgbImage(width, height, pixels[:expected])


def decode_image(data: bytes, mime: str | None = None) -> RgbImage:
    """Decode an authorized still. HEIC is refused until a local decoder exists."""
    kind = (mime or "").lower()
    if data.startswith(b"P6") or "portable-pixmap" in kind or kind.endswith("/ppm"):
        return parse_ppm(data)
    if "heic" in kind or "heif" in kind or data[4:12] == b"ftypheic" or data[4:12] == b"ftypmif1":
        raise ImageError(
            "HEIC stills from camera.live.photo need a local HEIC decoder; none is bundled"
        )
    if data.startswith(b"\xff\xd8") or "jpeg" in kind or "jpg" in kind:
        return _pillow(data)
    if data.startswith(b"\x89PNG") or "png" in kind:
        return _pillow(data)
    raise ImageError("unsupported image; PPM works offline, JPEG and PNG need Pillow")


def _pillow(data: bytes) -> RgbImage:
    try:
        from PIL import Image
    except ImportError as exc:
        raise ImageError("JPEG and PNG need Pillow, which is not installed") from exc
    image = Image.open(__import__("io").BytesIO(data)).convert("RGB")
    return RgbImage(image.width, image.height, image.tobytes())
