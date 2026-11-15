"""Turn raw image evidence into a label and a 0-100 confidence.

The decision order is fixed and documented in docs/thresholds.md:

1. identical decoded pixels                      -> EXACT (100)
2. perceptual-hash distance within the near cut  -> NEAR-DUPLICATE
3. face similarity above the same-face cut       -> LIKELY SAME FACE
4. CLIP similarity above the near cut (only for
   photos without a detectable face)             -> NEAR-DUPLICATE
5. any signal inside its "weak" band             -> WEAK
6. otherwise                                     -> NOT A MATCH

Confidence is a piecewise-linear map of the deciding metric, anchored so that
80 is exactly the match threshold and 50 the weak threshold for every method.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from itertools import pairwise

from PIL import Image

from avatartrace.compare import hashing
from avatartrace.compare.engines import (
    ClipEngine,
    FaceEngine,
    Vector,
    best_face_similarity,
    cosine,
)
from avatartrace.config import Thresholds
from avatartrace.imaging import pixel_sha256
from avatartrace.models import Comparison, MatchLabel

MATCH_CONFIDENCE = 80.0
WEAK_CONFIDENCE = 50.0


def interpolate(x: float, points: Sequence[tuple[float, float]]) -> float:
    """Piecewise-linear interpolation through ``points`` (sorted by x), clamped at the ends."""
    if x <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in pairwise(points):
        if x <= x1:
            if x1 == x0:
                return y1
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return points[-1][1]


def hash_confidence(distance: float, t: Thresholds) -> float:
    """0 bits -> 99, near cut -> 80, weak cut -> 50, 32 bits (unrelated) -> 0."""
    points = _dedupe(
        [(0.0, 99.0), (t.hash_near_max, MATCH_CONFIDENCE), (t.hash_weak_max, WEAK_CONFIDENCE), (32.0, 0.0)]
    )
    return interpolate(distance, points)


def face_confidence(similarity: float, t: Thresholds) -> float:
    """0 -> 0, weak cut -> 50, same-face cut -> 80, 0.80 -> 97, 1.0 -> 99."""
    points = _dedupe(
        [
            (min(0.0, t.face_weak_min), 0.0),
            (t.face_weak_min, WEAK_CONFIDENCE),
            (t.face_same_min, MATCH_CONFIDENCE),
            (max(0.80, t.face_same_min), 97.0),
            (1.0, 99.0),
        ]
    )
    return interpolate(similarity, points)


def clip_confidence(similarity: float, t: Thresholds) -> float:
    """0.5 -> 0, weak cut -> 50, near cut -> 80, 1.0 -> 99."""
    points = _dedupe(
        [
            (min(0.5, t.clip_weak_min), 0.0),
            (t.clip_weak_min, WEAK_CONFIDENCE),
            (t.clip_near_min, MATCH_CONFIDENCE),
            (1.0, 99.0),
        ]
    )
    return interpolate(similarity, points)


def _dedupe(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Make the anchor x-values strictly increasing.

    When two thresholds coincide the scale has a step there. The anchor with the
    higher confidence keeps the exact x (thresholds are inclusive) and the other
    one is moved an epsilon away, so the step falls on the correct side.
    """
    epsilon = 1e-9
    result: list[tuple[float, float]] = []
    for x, y in points:
        if not result or x > result[-1][0]:
            result.append((x, y))
        elif x == result[-1][0]:
            previous_x, previous_y = result[-1]
            if y > previous_y:
                result[-1] = (previous_x - epsilon, previous_y)
                result.append((x, y))
            else:
                result.append((x + epsilon, y))
        # an anchor left of the previous one is redundant and dropped
    return result


@dataclass(slots=True)
class ImageFeatures:
    """Everything needed to compare one image against another."""

    sha256: str
    width: int
    height: int
    hashes: dict[str, hashing.HashSet] | None
    faces: list[Vector] | None = None
    clip: Vector | None = None
    notes: list[str] = field(default_factory=list)

    def clear_embeddings(self) -> None:
        """Forget biometric data as soon as it is no longer needed."""
        self.faces = None
        self.clip = None


class Comparator:
    """Extracts features and compares them with the configured thresholds."""

    def __init__(
        self,
        thresholds: Thresholds,
        face_engine: FaceEngine | None = None,
        clip_engine: ClipEngine | None = None,
    ) -> None:
        self.thresholds = thresholds
        self.face_engine = face_engine
        self.clip_engine = clip_engine

    def query_features(self, image: Image.Image) -> ImageFeatures:
        """Features of the uploaded photo. CLIP is only used when no face is found."""
        features = self._base(image)
        if self.face_engine is not None:
            features.faces = self.face_engine.embed(image)
            if len(features.faces) > 1:
                features.notes.append(
                    f"{len(features.faces)} faces in the uploaded photo; the best-matching one is used"
                )
        # CLIP is for face-less pictures only, and says nothing useful about flat placeholder images.
        if self.clip_engine is not None and not features.faces and features.hashes is not None:
            features.clip = self.clip_engine.embed(image)
        return features

    def candidate_features(self, image: Image.Image, query: ImageFeatures) -> ImageFeatures:
        """Features of a fetched avatar, computing only what the query can be compared on."""
        features = self._base(image)
        if features.sha256 == query.sha256:
            return features
        if self.face_engine is not None and query.faces:
            features.faces = self.face_engine.embed(image)
        if self.clip_engine is not None and query.clip is not None:
            features.clip = self.clip_engine.embed(image)
        return features

    @staticmethod
    def _base(image: Image.Image) -> ImageFeatures:
        return ImageFeatures(
            sha256=pixel_sha256(image),
            width=image.size[0],
            height=image.size[1],
            hashes=hashing.compute_hashes(image),
        )

    def compare(self, query: ImageFeatures, candidate: ImageFeatures) -> Comparison:
        t = self.thresholds
        details: dict[str, float | int | str | None] = {}

        if query.sha256 == candidate.sha256:
            return Comparison(MatchLabel.EXACT, 100.0, "sha256", {"sha256": query.sha256})

        hash_conf: float | None = None
        if query.hashes is not None and candidate.hashes is not None:
            best = hashing.best_distance(query.hashes, candidate.hashes)
            hash_conf = hash_confidence(best.combined, t)
            details.update(
                hash_distance=round(best.combined, 2),
                hash_variant=best.variant,
                phash=best.phash,
                dhash=best.dhash,
                ahash=best.ahash,
                whash=best.whash,
            )
        else:
            details["hash_note"] = "image too uniform for perceptual hashing"

        face_conf: float | None = None
        if query.faces is not None:
            details["query_faces"] = len(query.faces)
        if query.faces and candidate.faces is not None:
            details["candidate_faces"] = len(candidate.faces)
            similarity = best_face_similarity(query.faces, candidate.faces)
            if similarity is not None:
                face_conf = face_confidence(similarity, t)
                details["face_similarity"] = round(similarity, 4)

        clip_conf: float | None = None
        if query.clip is not None and candidate.clip is not None:
            similarity = cosine(query.clip, candidate.clip)
            clip_conf = clip_confidence(similarity, t)
            details["clip_similarity"] = round(similarity, 4)

        if hash_conf is not None and hash_conf >= MATCH_CONFIDENCE:
            return Comparison(MatchLabel.NEAR_DUPLICATE, round(hash_conf, 1), "perceptual-hash", details)
        if face_conf is not None and face_conf >= MATCH_CONFIDENCE:
            return Comparison(MatchLabel.LIKELY_SAME_FACE, round(face_conf, 1), "face", details)
        if clip_conf is not None and clip_conf >= MATCH_CONFIDENCE:
            return Comparison(MatchLabel.NEAR_DUPLICATE, round(clip_conf, 1), "clip", details)

        scored = [
            (conf, method)
            for conf, method in ((hash_conf, "perceptual-hash"), (face_conf, "face"), (clip_conf, "clip"))
            if conf is not None
        ]
        if not scored:
            return Comparison(MatchLabel.NOT_A_MATCH, 0.0, "none", details)
        best_conf, method = max(scored, key=lambda item: item[0])
        label = MatchLabel.WEAK if best_conf >= WEAK_CONFIDENCE else MatchLabel.NOT_A_MATCH
        return Comparison(label, round(best_conf, 1), method, details)
