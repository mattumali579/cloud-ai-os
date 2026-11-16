"""Reachability probe for Visor 3.0.4 on the Windows host.

This module does not invent pixels, does not call a glasses assistant, and
does not use a paid model. The default path is a short TCP check of the
configured ``visor_url``. An optional handshake uses visor_kit only when
that package and a token are already present.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
from importlib.util import find_spec
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

DEFAULT_CONFIG = Path.home() / "AppData" / "Local" / "CloudAIOS" / "jarvis-glasses.json"
DRIVE_FS = Path(r"C:\Program Files\Google\Drive File Stream")
DEFAULT_TIMEOUT = 2.0


class ProbeError(RuntimeError):
    """The probe could not even parse its target."""


def parse_visor_url(url: str) -> tuple[str, int, str]:
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in {"ws", "wss"}:
        raise ProbeError("visor_url must be a private ws:// or wss:// URL")
    host = parsed.hostname
    if not host:
        raise ProbeError("visor_url is missing a host")
    port = parsed.port or (443 if parsed.scheme == "wss" else 80)
    return host, port, parsed.path or "/"


def load_visor_url(config_path: Path | None = None) -> str:
    path = config_path or DEFAULT_CONFIG
    if not path.is_file():
        raise ProbeError(f"Visor config is missing at {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    url = str(raw.get("visor_url") or "")
    parse_visor_url(url)
    return url


def tcp_probe(
    host: str,
    port: int,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    opener: Callable[..., socket.socket] | None = None,
) -> dict:
    connect = opener or socket.create_connection
    try:
        sock = connect((host, port), timeout)
    except OSError as exc:
        return {
            "open": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    try:
        sock.close()
    except OSError:
        pass
    return {"open": True, "error": ""}


def host_inventory() -> dict:
    """What this process can see. Not a glasses session."""
    return {
        "rclone_on_path": shutil.which("rclone") is not None,
        "google_drive_file_stream": DRIVE_FS.exists(),
        "visor_kit_importable": find_spec("visor_kit") is not None,
        "pillow_heif_importable": find_spec("pillow_heif") is not None,
        "mediapipe_importable": find_spec("mediapipe") is not None,
        "onnxruntime_importable": find_spec("onnxruntime") is not None,
        "openai_api_key_set": bool(os.environ.get("OPENAI_API_KEY")),
        "archive_remote_set": bool(os.environ.get("GLASSES_ARCHIVE_RCLONE_REMOTE")),
        "hardware_verified": False,
        "assistant_dispatched": False,
        "paid_inference": False,
    }


def probe_visor(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    capture: bool = False,
    opener: Callable[..., socket.socket] | None = None,
) -> dict:
    """TCP probe of Visor. Capture is refused until TCP is open."""
    host, port, path = parse_visor_url(url)
    tcp = tcp_probe(host, port, timeout=timeout, opener=opener)
    result = {
        "url_host": host,
        "url_port": port,
        "url_path": path,
        "tcp_open": tcp["open"],
        "tcp_error": tcp["error"],
        "handshake": "not_attempted",
        "capture": "refused" if capture else "not_requested",
        "capture_reason": "",
        "methods": {},
        "hardware_verified": False,
        "assistant_dispatched": False,
        "paid_inference": False,
        "inventory": host_inventory(),
    }
    if capture and not tcp["open"]:
        result["capture"] = "refused"
        result["capture_reason"] = (
            "Visor TCP is closed. camera.live.photo was not called. "
            "Open Visor on the phone, wear the glasses, and confirm visor_url."
        )
    if not tcp["open"]:
        result["next_action"] = (
            "Bring the Samsung online on the same LAN (or Tailscale), open Visor, "
            "and keep Live View's split tunnel. Do not send a ChatGPT assistant prompt."
        )
        return result
    result["handshake"] = "tcp_open_only"
    result["next_action"] = (
        "TCP is open. A visor_kit handshake can call glasses.status, then "
        "camera.live.photo, with no assistant prompt."
    )
    if capture:
        result["capture"] = "not_implemented_without_handshake"
        result["capture_reason"] = (
            "This probe does not open visor_kit unless a later handshake path "
            "is explicitly added and a token is present. Pixels were not invented."
        )
    return result
