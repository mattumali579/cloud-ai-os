"""What Visor 3.0.4 actually exposes for camera frames.

Checked against the installed-version pins, not against later docs alone:

- Visor tag ``v3.0.4`` (2026-11-11) depends on ``com.macedonga.visor:kit`` 0.1.1
  (``gradle/libs.versions.toml``).
- ``visor-kit`` tag ``v0.1.1`` (2026-11-09) ``protocol/api/openrpc.json`` lists
  ``camera.photo``, ``camera.live.photo``, and the ``camera.live`` feed.
- The same Visor tag's ``DeveloperIntents`` / ``FrameService`` expose
  ``TAKE_PHOTO``, ``NEW_MEDIA``, and binder message ``LIVE_FRAMES`` (JPEG).
"""

from __future__ import annotations

VISOR_VERSION = "3.0.4"
VDK_VERSION = "0.1.1"

# Names present in visor-kit v0.1.1 openrpc.json.
VDK_CAMERA_METHODS = (
    "camera.photo",
    "camera.video.start",
    "camera.video.stop",
    "camera.live.photo",
)
VDK_CAMERA_FEEDS = ("camera.live",)
VDK_CAPTURE_METHODS = ("captures.list", "captures.import")
VDK_VOICE_METHODS = ("voice.reply", "voice.speak")


def visor_304_camera_capability() -> dict:
    """Static audit of the external camera surface. No device was contacted."""
    return {
        "visor_version": VISOR_VERSION,
        "vdk_version": VDK_VERSION,
        "hardware_verified": False,
        "frames_exposed": True,
        "summary": (
            "Visor 3.0.4 does expose authorized camera frames outside the Visor UI. "
            "Stills and an encoded live feed are in VDK 0.1.1. Decoded pixels are "
            "not delivered to this PC pipeline yet: gallery URIs stay on the phone, "
            "live photos are typically HEIC, and camera.live is HEVC or AVC."
        ),
        "paths": [
            {
                "api": "camera.photo",
                "where": "VDK JSON-RPC, scope camera",
                "gives": "Capture id, type, time, size, and a content:// gallery URI after import",
                "pixels_on_pc": False,
                "blocker": "The URI is on the phone. This PC cannot read content://.",
                "capture_light": "on for every photo",
            },
            {
                "api": "camera.live.photo",
                "where": "VDK JSON-RPC while Live is already running",
                "gives": "LivePhoto mime, and Base64 data over adb or the network door",
                "pixels_on_pc": "only when data is present and the mime is one this process can decode",
                "blocker": (
                    "Fails with busy when Live is not running. The glasses photo is "
                    "often HEIC, and no HEIC decoder is bundled."
                ),
                "capture_light": "Live keeps the capture light on",
            },
            {
                "api": "camera.live",
                "where": "VDK feed, scope camera",
                "gives": "Encoded video/hevc or video/avc Annex-B, plus optional AAC",
                "pixels_on_pc": False,
                "blocker": (
                    "No HEVC decoder is bundled, so this feed cannot drive tracking yet. "
                    "Allowed rates are 2, 7, 15, 24, or 30. A reader that stalls for "
                    "5 seconds is dropped. reason vpn means a phone VPN captured the "
                    "Wi-Fi Direct link to the glasses."
                ),
                "preferred_rate": 2,
            },
            {
                "api": "com.macedonga.visor.action.LIVE_FRAMES",
                "where": "Android binder inside Developer mode, Visor 3.0.4 FrameService",
                "gives": "JPEG bytes (message 3) at interval_ms >= 200 while a Live is already running",
                "pixels_on_pc": False,
                "blocker": (
                    "Only an Android app bound to com.macedonga.visor can subscribe. "
                    "Wrong token or Developer mode off returns message 4."
                ),
            },
            {
                "api": "TAKE_PHOTO / NEW_MEDIA",
                "where": "Developer mode broadcasts, token when required",
                "gives": "NEW_MEDIA carries uri and path once the capture is in the phone gallery",
                "pixels_on_pc": False,
                "blocker": "The file is on the phone until something copies it on purpose.",
            },
        ],
        "tailscale": {
            "live_failure_reason": "vpn",
            "evidence": (
                "VDK camera.live lists reason vpn: a VPN on the phone keeps Visor "
                "off the glasses' Wi-Fi Direct network."
            ),
            "owner_split_tunnel": (
                "Visor and Meta AI are excluded from the Android Tailscale tunnel "
                "so Live View works. This change does not revert that exclusion."
            ),
            "jarvis_reachability": "NOT VERIFIED",
            "smallest_check": (
                "With the split tunnel left as it is, confirm the phone can open "
                "the private Jarvis bridge and that Live View still shows a picture."
            ),
        },
        "not_used": [
            "Meta Live AI",
            "paid vision APIs",
            "a second assistant or dashboard",
        ],
    }
