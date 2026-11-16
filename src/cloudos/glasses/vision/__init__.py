"""Offline glasses vision: authorized capture, short tracking, consent faces.

CoarseRegionDetector labels color regions, not objects. Pinch, open-palm, and
fist gestures are geometry on 21 landmarks. MediaPipe and YOLO are optional
local backends and are not downloaded. Nothing here calls a paid model or a
web search. Hardware capture stays unverified until a person uses the glasses.
"""

from cloudos.glasses.vision.capability import visor_304_camera_capability
from cloudos.glasses.vision.service import VisionService, is_vision_utterance

__all__ = [
    "VisionService",
    "is_vision_utterance",
    "visor_304_camera_capability",
]
