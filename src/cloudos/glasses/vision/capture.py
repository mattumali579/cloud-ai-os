"""Authorized stills and the Visor 3.0.4 capture adapters.

Adapters refuse to invent pixels. ``hardware_verified`` stays false: only a
person wearing the glasses can set that, and this process never does.
"""

from __future__ import annotations

import base64
import hmac
from dataclasses import dataclass
from pathlib import Path

from cloudos.glasses.vision.images import RgbImage, decode_image

MAX_PHOTO_BYTES = 15_000_000


class CaptureError(Exception):
    """The capture call was refused or the bytes could not be used."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class CapturedFrame:
    image: RgbImage
    mime: str
    source: str
    authorized: bool
    simulated: bool
    hardware_verified: bool = False
    capture_light_expected: bool = True

    def __post_init__(self) -> None:
        if self.hardware_verified:
            raise CaptureError("hardware_unverified", "this pipeline cannot mark a frame hardware-verified")
        if not self.authorized:
            raise CaptureError("unauthorized", "a frame must be explicitly authorized")


def _tokens_match(presented: str | None, expected: str | None) -> bool:
    if not presented or not expected:
        return False
    left = presented.encode("utf-8")
    right = expected.encode("utf-8")
    if len(left) != len(right):
        return False
    return hmac.compare_digest(left, right)


class PhotoFileAdapter:
    """Read a picture the user already authorized, usually a gallery export."""

    def load(self, path: Path, *, authorized: bool) -> CapturedFrame:
        if not authorized:
            raise CaptureError("unauthorized", "refusing a photo that was not user-authorized")
        file = Path(path)
        if not file.is_file():
            raise CaptureError("not_found", "authorized photo file is missing")
        data = file.read_bytes()
        if len(data) > MAX_PHOTO_BYTES:
            raise CaptureError("too_large", "photo is over 15 MB")
        try:
            image = decode_image(data)
        except ValueError as exc:
            raise CaptureError("decode", str(exc)) from exc
        return CapturedFrame(
            image=image,
            mime="image/x-portable-pixmap" if data.startswith(b"P6") else "application/octet-stream",
            source="photo-file",
            authorized=True,
            simulated=True,
            capture_light_expected=False,
        )


class FrameSequenceAdapter:
    """Prerecorded frames for tracking tests when no live decoder is available."""

    def __init__(self, frames: list[RgbImage]) -> None:
        self._frames = list(frames)
        self._index = 0

    def next_frame(self) -> CapturedFrame | None:
        if self._index >= len(self._frames):
            return None
        image = self._frames[self._index]
        self._index += 1
        return CapturedFrame(
            image=image,
            mime="image/x-portable-pixmap",
            source="frame-sequence",
            authorized=True,
            simulated=True,
            capture_light_expected=False,
        )


class ScriptedVisor:
    """In-memory VDK stand-in. Tests use it; it is not a glasses connection."""

    def __init__(self, results: dict[str, dict], *, supported: tuple[str, ...] = ()) -> None:
        self._results = results
        self._supported = set(supported)
        self.calls: list[tuple[str, dict]] = []
        self.simulated = True

    def supports(self, name: str) -> bool:
        return name in self._supported

    def call(self, method: str, params: dict | None = None) -> dict:
        self.calls.append((method, dict(params or {})))
        if method not in self._results:
            raise CaptureError("unsupported", f"scripted Visor has no {method}")
        return self._results[method]


class VisorStillAdapter:
    """Take one still through a VDK-shaped client (``camera.live.photo`` or ``camera.photo``)."""

    def __init__(self, client: ScriptedVisor, *, device_token: str) -> None:
        if not device_token:
            raise CaptureError("auth", "a Visor device token is required before any camera call")
        self._client = client
        self._token = device_token

    def capture(self, *, presented_token: str) -> CapturedFrame:
        if not _tokens_match(presented_token, self._token):
            raise CaptureError("auth", "Visor device token was rejected")
        if self._client.supports("camera.live.photo"):
            method = "camera.live.photo"
            result = self._client.call(method, {})
        elif self._client.supports("camera.photo"):
            method = "camera.photo"
            result = self._client.call(method, {"import": True})
        else:
            raise CaptureError(
                "blocked",
                "this Visor connection does not offer camera.live.photo or camera.photo",
            )
        return self._frame_from(method, result)

    def _frame_from(self, method: str, result: dict) -> CapturedFrame:
        encoded = result.get("data")
        if encoded:
            try:
                raw = base64.b64decode(encoded, validate=True)
            except Exception as exc:  # noqa: BLE001 — bad payload is a capture failure
                raise CaptureError("decode", "Visor photo data was not valid base64") from exc
            mime = str(result.get("mime") or "")
            try:
                image = decode_image(raw, mime)
            except ValueError as exc:
                raise CaptureError("decode", str(exc)) from exc
            return CapturedFrame(
                image=image,
                mime=mime or "application/octet-stream",
                source=f"visor-vdk:{method}",
                authorized=True,
                simulated=bool(getattr(self._client, "simulated", True)),
                capture_light_expected=True,
            )
        uri = str(result.get("uri") or "")
        if uri.startswith("content://"):
            raise CaptureError(
                "blocked",
                "camera.photo returned a phone gallery URI. This PC cannot read content://. "
                "Use camera.live.photo over the network door, which returns Base64, "
                "or copy the gallery file onto the PC on purpose.",
            )
        raise CaptureError("blocked", f"{method} returned no readable pixels")


class LiveFeedAdapter:
    """``camera.live`` exists, but the samples are encoded video, not bitmaps."""

    def open_session(self, *, frame_rate: int = 2) -> None:
        if frame_rate not in (2, 7, 15, 24, 30):
            raise CaptureError("invalid_params", "camera.live frameRate must be 2, 7, 15, 24, or 30")
        raise CaptureError(
            "blocked",
            "camera.live on Visor 3.0.4 is an encoded HEVC or AVC feed, and this "
            "pipeline has no decoder. Short tracking can use camera.live.photo "
            "stills at 2 fps, Android LIVE_FRAMES JPEGs, or prerecorded frames. "
            "A phone VPN fails this feed with reason vpn.",
        )
