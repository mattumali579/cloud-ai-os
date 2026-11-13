"""Offline glasses vision: authorized capture, labels, short tracking, consent faces.

Nothing in this package calls a paid model or a web search. Hardware capture
stays unverified until a person uses the glasses.
"""

from cloudos.glasses.vision.capability import visor_304_camera_capability
from cloudos.glasses.vision.service import VisionService, is_vision_utterance

__all__ = [
    "VisionService",
    "is_vision_utterance",
    "visor_304_camera_capability",
]
