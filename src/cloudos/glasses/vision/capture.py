"""Authorized capture adapters. Visor hardware success is never faked."""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from PIL import Image, ImageDraw

from cloudos.glasses.vision.types import CaptureFrame


class CaptureAuthorizationError(PermissionError):
    """User/device did not authorize capture, or required Visor camera scope is missing."""


class CaptureAdapter(Protocol):
    def capture_photo(self) -> CaptureFrame: ...


def _png_bytes(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


@dataclass
class InMemoryCaptureAdapter:
    """Holds an already-authorized frame (e.g. from async Visor capture)."""

    frame: CaptureFrame
    authorized: bool = True

    def capture_photo(self) -> CaptureFrame:
        if not self.authorized:
            raise CaptureAuthorizationError("Capture not authorized by the user.")
        return self.frame


@dataclass
class FixtureCaptureAdapter:
    """Prerecorded / synthetic frames for tests. Never counts as hardware verification."""

    path: Path
    authorized: bool = True
    retain_dir: Path | None = None

    def capture_photo(self) -> CaptureFrame:
        if not self.authorized:
            raise CaptureAuthorizationError("Capture not authorized by the user.")
        image = Image.open(self.path).convert("RGB")
        data = _png_bytes(image)
        if self.retain_dir is not None:
            self.retain_dir.mkdir(parents=True, exist_ok=True)
            (self.retain_dir / "retained_frame.png").write_bytes(data)
        return CaptureFrame(
            bytes_png=data,
            source="fixture",
            hardware_verified=False,
            width=image.width,
            height=image.height,
            meta={"path": str(self.path)},
        )


@dataclass
class WebcamSimulationAdapter:
    """PC webcam / generated simulation only — never hardware-verified glasses capture."""

    authorized: bool = True
    color: tuple[int, int, int] = (180, 180, 40)

    def capture_photo(self) -> CaptureFrame:
        if not self.authorized:
            raise CaptureAuthorizationError("Capture not authorized by the user.")
        image = Image.new("RGB", (160, 120), (30, 30, 30))
        draw = ImageDraw.Draw(image)
        draw.ellipse((50, 30, 110, 90), fill=self.color)
        data = _png_bytes(image)
        return CaptureFrame(
            bytes_png=data,
            source="webcam_simulation",
            hardware_verified=False,
            width=160,
            height=120,
            meta={"simulation": True},
        )


class VisorCameraCaptureAdapter:
    """Smallest Visor VDK boundary for camera.photo / optional bounded camera.live.

    Official Visor 3.0.4+ JSON-RPC (scope ``camera``):
      - method camera.photo
      - method camera.live.photo
      - feed camera.live (HEVC/H.264 samples; decode on-device / phone side)

    This adapter never invents a successful glasses frame. Missing scope or failed
    call raises CaptureAuthorizationError / RuntimeError with the exact blocker.
    """

    def __init__(self, visor: Any, *, authorized: bool = False, import_to_gallery: bool = True):
        self._visor = visor
        self.authorized = authorized
        self.import_to_gallery = import_to_gallery

    @staticmethod
    def session_policy() -> dict[str, Any]:
        return {
            "default_mode": "single_shot",
            "max_fps": 2,
            "allowed_fps": (2, 7),
            "max_live_seconds": 20,
            "hidden_streaming": False,
            "retain_frames_default": False,
            "stop_on_thermal": True,
            "stop_on_low_battery": True,
        }

    def camera_photo_supported(self) -> bool:
        return bool(getattr(self._visor, "supports", lambda _n: False)("camera.photo"))

    def camera_live_supported(self) -> bool:
        return bool(getattr(self._visor, "supports", lambda _n: False)("camera.live"))

    def capture_photo(self) -> CaptureFrame:
        if not self.authorized:
            raise CaptureAuthorizationError("Glasses camera capture requires explicit user authorization.")
        if not self.camera_photo_supported():
            raise CaptureAuthorizationError(
                "BLOCKED: Visor session lacks camera.photo (scope camera). "
                "Grant the program the camera scope in Visor → Apps → Let programs connect. "
                "Live View alone does not deliver external frames to this bridge."
            )
        # Synchronous wrapper expects caller to use capture_photo_async in async bridge.
        raise CaptureAuthorizationError(
            "Visor camera.photo must be invoked from the async bridge via capture_photo_async; "
            "refusing sync fake success."
        )

    async def capture_photo_async(self) -> CaptureFrame:
        if not self.authorized:
            raise CaptureAuthorizationError("Glasses camera capture requires explicit user authorization.")
        if not self.camera_photo_supported():
            raise CaptureAuthorizationError(
                "BLOCKED: Visor session lacks camera.photo (scope camera). "
                "Grant the program the camera scope in Visor → Apps → Let programs connect."
            )
        result = await self._visor.call("camera.photo", {"import": self.import_to_gallery})
        # Network door returns Capture; live.photo may include base64 data.
        data_b64 = result.get("data") if isinstance(result, dict) else None
        uri = result.get("uri") if isinstance(result, dict) else None
        if data_b64:
            raw = base64.b64decode(data_b64)
            image = Image.open(io.BytesIO(raw)).convert("RGB")
            png = _png_bytes(image)
            return CaptureFrame(
                bytes_png=png,
                source="visor_camera.photo",
                hardware_verified=True,
                width=image.width,
                height=image.height,
                meta={"capture_id": result.get("id"), "uri": uri, "type": result.get("type")},
            )
        raise CaptureAuthorizationError(
            "BLOCKED: camera.photo returned no image bytes on this transport "
            f"(uri={uri!r}). Phone-local content:// URIs are not readable from the Windows bridge; "
            "use camera.live.photo (base64) or an on-phone import path."
        )

    async def capture_live_photo_async(self) -> CaptureFrame:
        """Single frame while Live is already running; still requires authorization."""
        if not self.authorized:
            raise CaptureAuthorizationError("Glasses camera capture requires explicit user authorization.")
        if not getattr(self._visor, "supports", lambda _n: False)("camera.live.photo"):
            raise CaptureAuthorizationError("BLOCKED: Visor session lacks camera.live.photo.")
        result = await self._visor.call("camera.live.photo", {})
        data_b64 = result.get("data") if isinstance(result, dict) else None
        if not data_b64:
            raise CaptureAuthorizationError("BLOCKED: camera.live.photo returned no base64 data.")
        raw = base64.b64decode(data_b64)
        image = Image.open(io.BytesIO(raw)).convert("RGB")
        return CaptureFrame(
            bytes_png=_png_bytes(image),
            source="visor_camera.live.photo",
            hardware_verified=True,
            width=image.width,
            height=image.height,
            meta={"mime": result.get("mime")},
        )
