"""Free/open local vision extension for Jarvis glasses (issue #25)."""

from cloudos.glasses.vision.pipeline import (
    VisionPipeline,
    get_runtime_pipeline,
    parse_enroll_name,
    parse_remove_name,
    set_runtime_pipeline,
)
from cloudos.glasses.vision.types import VisionResult, VisionState

__all__ = [
    "VisionPipeline",
    "VisionResult",
    "VisionState",
    "get_runtime_pipeline",
    "set_runtime_pipeline",
    "parse_enroll_name",
    "parse_remove_name",
]
