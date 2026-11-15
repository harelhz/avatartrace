"""Image comparison: exact pixels, perceptual hashes, faces and CLIP."""

from avatartrace.compare.engines import (
    ClipEngine,
    EngineUnavailable,
    FaceEngine,
    FastEmbedClipEngine,
    InsightFaceEngine,
    clip_available,
    face_available,
)
from avatartrace.compare.scoring import Comparator, ImageFeatures

__all__ = [
    "ClipEngine",
    "Comparator",
    "EngineUnavailable",
    "FaceEngine",
    "FastEmbedClipEngine",
    "ImageFeatures",
    "InsightFaceEngine",
    "clip_available",
    "face_available",
]
