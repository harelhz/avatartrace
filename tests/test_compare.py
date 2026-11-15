"""Exact, perceptual-hash and scoring logic on the fixture images (no ML models needed)."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pytest
from PIL import Image
from pydantic import ValidationError

from avatartrace.compare import Comparator, hashing
from avatartrace.compare.engines import Vector, best_face_similarity, cosine
from avatartrace.compare.scoring import (
    clip_confidence,
    face_confidence,
    hash_confidence,
    interpolate,
)
from avatartrace.config import Thresholds
from avatartrace.imaging import pixel_sha256
from avatartrace.models import MatchLabel

T = Thresholds()
Loader = Callable[[str], Image.Image]


def compare(load: Loader, query: str, candidate: str, comparator: Comparator | None = None) -> tuple[MatchLabel, float]:
    comparator = comparator or Comparator(T)
    features = comparator.query_features(load(query))
    result = comparator.compare(features, comparator.candidate_features(load(candidate), features))
    return result.label, result.confidence


# ------------------------------------------------------------------ fixtures: hashes only


def test_identical_pixels_are_exact(fixture_image: Loader) -> None:
    assert compare(fixture_image, "face_a.jpg", "face_a_lossless.png") == (MatchLabel.EXACT, 100.0)
    assert compare(fixture_image, "logo.png", "logo.png") == (MatchLabel.EXACT, 100.0)


@pytest.mark.parametrize(
    "variant",
    [
        "face_a_resized.jpg",  # 150 px, what most platforms store
        "face_a_tiny.jpg",  # 48 px thumbnail
        "face_a_recompressed.jpg",  # JPEG quality 25
        "face_a_filtered.jpg",  # warm photo filter + blur
        "face_a_grayscale.jpg",
        "face_a_cropped_light.jpg",  # 5% trimmed off each side
        "face_a_circle.png",  # round avatar with transparent corners
        "face_a_tall.jpg",  # uncropped upload; the avatar is its centre square
    ],
)
def test_edited_copies_are_near_duplicates(fixture_image: Loader, variant: str) -> None:
    label, confidence = compare(fixture_image, "face_a.jpg", variant)
    assert label is MatchLabel.NEAR_DUPLICATE
    assert 80.0 <= confidence < 100.0


def test_comparison_is_symmetric_for_edited_copies(fixture_image: Loader) -> None:
    assert compare(fixture_image, "face_a_resized.jpg", "face_a.jpg")[0] is MatchLabel.NEAR_DUPLICATE
    assert compare(fixture_image, "face_a_circle.png", "face_a_tall.jpg")[0] is MatchLabel.NEAR_DUPLICATE


@pytest.mark.parametrize(("variant", "label"), [("logo_resized.jpg", MatchLabel.NEAR_DUPLICATE)])
def test_non_face_copies_are_near_duplicates(fixture_image: Loader, variant: str, label: MatchLabel) -> None:
    assert compare(fixture_image, "logo.png", variant)[0] is label


def test_lightly_cropped_logo_is_at_least_weak(fixture_image: Loader) -> None:
    label, confidence = compare(fixture_image, "logo.png", "logo_cropped.jpg")
    assert label in (MatchLabel.NEAR_DUPLICATE, MatchLabel.WEAK)
    assert confidence >= 50.0


@pytest.mark.parametrize(
    ("query", "candidate"),
    [
        ("face_a.jpg", "face_b.jpg"),  # different person
        ("face_a.jpg", "face_b_other.jpg"),
        ("face_a.jpg", "logo.png"),  # no face at all
        ("face_a.jpg", "landscape.jpg"),
        ("logo.png", "logo_other.png"),  # different logo
        ("logo.png", "landscape.jpg"),
        ("logo.png", "face_a.jpg"),
        ("face_b.jpg", "face_b_other.jpg"),  # same person, different photo: needs the face model
        ("face_a.jpg", "face_a_other.jpg"),
        ("face_a.jpg", "face_a_mirrored.jpg"),  # hashes are not mirror-invariant
        ("face_a.jpg", "face_a_cropped_heavy.jpg"),  # 28% trimmed: beyond hashing
    ],
)
def test_different_pictures_do_not_match_by_hash(fixture_image: Loader, query: str, candidate: str) -> None:
    label, confidence = compare(fixture_image, query, candidate)
    assert label is MatchLabel.NOT_A_MATCH
    assert confidence < 50.0


def test_flat_placeholder_images_never_match_each_other(fixture_image: Loader) -> None:
    assert hashing.compute_hashes(fixture_image("flat_gray.png")) is None
    assert hashing.compute_hashes(fixture_image("flat_white.png")) is None
    label, confidence = compare(fixture_image, "flat_gray.png", "flat_white.png")
    assert (label, confidence) == (MatchLabel.NOT_A_MATCH, 0.0)
    assert compare(fixture_image, "flat_gray.png", "flat_gray.png")[0] is MatchLabel.EXACT


def test_hash_details_are_reported(fixture_image: Loader) -> None:
    comparator = Comparator(T)
    query = comparator.query_features(fixture_image("face_a.jpg"))
    result = comparator.compare(query, comparator.candidate_features(fixture_image("face_a_tall.jpg"), query))
    assert result.method == "perceptual-hash"
    assert result.details["hash_variant"] == "square"
    assert result.details["hash_distance"] == 0.0
    for key in ("phash", "dhash", "ahash", "whash"):
        assert isinstance(result.details[key], int)


# ------------------------------------------------------------------ hashing internals


def test_hash_distance_properties(fixture_image: Loader) -> None:
    a = hashing.compute_hashes(fixture_image("face_a.jpg"))
    b = hashing.compute_hashes(fixture_image("face_b.jpg"))
    assert a is not None
    assert b is not None
    assert set(a) == set(hashing.VARIANTS)
    assert hashing.best_distance(a, a).combined == 0.0
    forward = hashing.best_distance(a, b)
    backward = hashing.best_distance(b, a)
    assert forward.combined == pytest.approx(backward.combined)
    assert 0 < forward.combined <= hashing.HASH_BITS
    assert sum(hashing.WEIGHTS.values()) == pytest.approx(1.0)


def test_hashes_are_64_bit() -> None:
    noise = Image.effect_noise((64, 64), 60).convert("RGB")
    hashes = hashing.compute_hashes(noise)
    assert hashes is not None
    for value in (hashes["full"].phash, hashes["full"].dhash, hashes["full"].ahash, hashes["full"].whash):
        assert 0 <= value < 2**64


def test_small_images_can_be_hashed() -> None:
    noise = Image.effect_noise((12, 12), 80).convert("RGB")
    assert hashing.compute_hashes(noise) is not None


# ------------------------------------------------------------------ confidence mapping


def test_interpolate_is_piecewise_linear_and_clamped() -> None:
    points = [(0.0, 0.0), (10.0, 100.0), (20.0, 0.0)]
    assert interpolate(-5, points) == 0.0
    assert interpolate(5, points) == 50.0
    assert interpolate(10, points) == 100.0
    assert interpolate(15, points) == 50.0
    assert interpolate(99, points) == 0.0


def test_confidence_anchors_line_up_with_thresholds() -> None:
    assert hash_confidence(0, T) == 99.0
    assert hash_confidence(T.hash_near_max, T) == pytest.approx(80.0)
    assert hash_confidence(T.hash_weak_max, T) == pytest.approx(50.0)
    assert hash_confidence(32, T) == 0.0
    assert hash_confidence(64, T) == 0.0
    assert face_confidence(T.face_same_min, T) == pytest.approx(80.0)
    assert face_confidence(T.face_weak_min, T) == pytest.approx(50.0)
    assert face_confidence(0.0, T) == 0.0
    assert face_confidence(-0.3, T) == 0.0
    assert face_confidence(1.0, T) == 99.0
    assert clip_confidence(T.clip_near_min, T) == pytest.approx(80.0)
    assert clip_confidence(T.clip_weak_min, T) == pytest.approx(50.0)
    assert clip_confidence(0.2, T) == 0.0


@pytest.mark.parametrize("function", [hash_confidence, face_confidence, clip_confidence])
def test_confidence_is_monotonic(function: Callable[[float, Thresholds], float]) -> None:
    xs = np.linspace(-1.0, 64.0, 500)
    values = [function(float(x), T) for x in xs]
    differences = np.diff(values)
    assert (differences <= 1e-9).all() or (differences >= -1e-9).all()
    assert all(0.0 <= v <= 99.0 for v in values)


def test_degenerate_thresholds_still_give_a_valid_scale() -> None:
    same = Thresholds(hash_near_max=10, hash_weak_max=10, face_same_min=0.5, face_weak_min=0.5)
    assert hash_confidence(9.9, same) >= 80.0
    assert hash_confidence(10.1, same) < 50.0
    assert face_confidence(0.51, same) >= 80.0


@pytest.mark.parametrize(
    "bad",
    [
        {"hash_near_max": 12, "hash_weak_max": 8},
        {"face_same_min": 0.3, "face_weak_min": 0.4},
        {"clip_near_min": 0.8, "clip_weak_min": 0.9},
        {"hash_near_max": -1},
        {"face_same_min": 2},
    ],
)
def test_inconsistent_thresholds_are_rejected(bad: dict[str, float]) -> None:
    with pytest.raises(ValidationError):
        Thresholds(**bad)


# ------------------------------------------------------------------ embeddings (fake engines)


def unit(*values: float) -> Vector:
    vector = np.asarray(values, dtype=np.float32)
    normalised: Vector = (vector / np.linalg.norm(vector)).astype(np.float32)
    return normalised


class FakeFaces:
    """Returns preset embeddings keyed by the picture's pixel hash."""

    name = "fake-face"

    def __init__(self, table: dict[str, list[Vector]]) -> None:
        self.table = table
        self.calls = 0

    def embed(self, image: Image.Image) -> list[Vector]:
        self.calls += 1
        return self.table.get(pixel_sha256(image), [])


class FakeClip:
    name = "fake-clip"

    def __init__(self, table: dict[str, Vector]) -> None:
        self.table = table
        self.calls = 0

    def embed(self, image: Image.Image) -> Vector:
        self.calls += 1
        return self.table[pixel_sha256(image)]


def test_cosine_and_best_pair() -> None:
    a, b = unit(1, 0, 0), unit(0, 1, 0)
    assert cosine(a, a) == pytest.approx(1.0)
    assert cosine(a, b) == pytest.approx(0.0)
    assert best_face_similarity([a], []) is None
    assert best_face_similarity([], [a]) is None
    assert best_face_similarity([a, b], [unit(0.1, 1, 0)]) == pytest.approx(cosine(b, unit(0.1, 1, 0)))


@pytest.mark.parametrize(
    ("angle_cosine", "label"),
    [
        (0.80, MatchLabel.LIKELY_SAME_FACE),
        (0.46, MatchLabel.LIKELY_SAME_FACE),
        (0.40, MatchLabel.WEAK),
        (0.33, MatchLabel.WEAK),
        (0.25, MatchLabel.NOT_A_MATCH),
        (0.05, MatchLabel.NOT_A_MATCH),
    ],
)
def test_face_similarity_bands(fixture_image: Loader, angle_cosine: float, label: MatchLabel) -> None:
    query, candidate = fixture_image("face_a.jpg"), fixture_image("face_b.jpg")
    other = unit(angle_cosine, float(np.sqrt(1 - angle_cosine**2)), 0)
    faces = FakeFaces({pixel_sha256(query): [unit(1, 0, 0)], pixel_sha256(candidate): [other]})
    comparator = Comparator(T, face_engine=faces)
    features = comparator.query_features(query)
    result = comparator.compare(features, comparator.candidate_features(candidate, features))
    assert result.label is label
    assert result.details["face_similarity"] == pytest.approx(angle_cosine, abs=1e-3)
    if label is MatchLabel.LIKELY_SAME_FACE:
        assert result.method == "face"
        assert result.confidence >= 80.0


def test_best_of_several_faces_is_used_and_noted(fixture_image: Loader) -> None:
    query, candidate = fixture_image("face_a.jpg"), fixture_image("face_b.jpg")
    faces = FakeFaces(
        {
            pixel_sha256(query): [unit(1, 0, 0), unit(0, 1, 0)],
            pixel_sha256(candidate): [unit(0, 0, 1), unit(0.05, 1, 0)],
        }
    )
    comparator = Comparator(T, face_engine=faces)
    features = comparator.query_features(query)
    assert any("2 faces" in note for note in features.notes)
    result = comparator.compare(features, comparator.candidate_features(candidate, features))
    assert result.label is MatchLabel.LIKELY_SAME_FACE
    assert result.details["query_faces"] == 2
    assert result.details["candidate_faces"] == 2


def test_candidate_without_a_face_gets_no_face_score(fixture_image: Loader) -> None:
    query, candidate = fixture_image("face_a.jpg"), fixture_image("logo.png")
    faces = FakeFaces({pixel_sha256(query): [unit(1, 0, 0)]})
    comparator = Comparator(T, face_engine=faces)
    features = comparator.query_features(query)
    result = comparator.compare(features, comparator.candidate_features(candidate, features))
    assert result.label is MatchLabel.NOT_A_MATCH
    assert "face_similarity" not in result.details
    assert result.details["candidate_faces"] == 0


def test_face_engine_is_not_run_on_candidates_when_the_query_has_no_face(fixture_image: Loader) -> None:
    faces = FakeFaces({})
    comparator = Comparator(T, face_engine=faces)
    features = comparator.query_features(fixture_image("logo.png"))
    assert faces.calls == 1
    comparator.candidate_features(fixture_image("face_a.jpg"), features)
    assert faces.calls == 1


def test_no_models_run_for_identical_pictures(fixture_image: Loader) -> None:
    query = fixture_image("face_a.jpg")
    faces = FakeFaces({pixel_sha256(query): [unit(1, 0, 0)]})
    comparator = Comparator(T, face_engine=faces)
    features = comparator.query_features(query)
    comparator.candidate_features(fixture_image("face_a_lossless.png"), features)
    assert faces.calls == 1


def test_near_duplicate_outranks_face_match(fixture_image: Loader) -> None:
    query, candidate = fixture_image("face_a.jpg"), fixture_image("face_a_resized.jpg")
    faces = FakeFaces({pixel_sha256(query): [unit(1, 0, 0)], pixel_sha256(candidate): [unit(1, 0, 0)]})
    comparator = Comparator(T, face_engine=faces)
    features = comparator.query_features(query)
    result = comparator.compare(features, comparator.candidate_features(candidate, features))
    assert result.label is MatchLabel.NEAR_DUPLICATE
    assert result.method == "perceptual-hash"


@pytest.mark.parametrize(
    ("similarity", "label", "method"),
    [
        (0.97, MatchLabel.NEAR_DUPLICATE, "clip"),
        (0.92, MatchLabel.WEAK, "clip"),
        (0.80, MatchLabel.NOT_A_MATCH, None),
    ],
)
def test_clip_bands_for_faceless_pictures(
    fixture_image: Loader, similarity: float, label: MatchLabel, method: str | None
) -> None:
    query, candidate = fixture_image("logo.png"), fixture_image("logo_other.png")
    other = unit(similarity, float(np.sqrt(1 - similarity**2)))
    clip = FakeClip({pixel_sha256(query): unit(1, 0), pixel_sha256(candidate): other})
    comparator = Comparator(T, face_engine=FakeFaces({}), clip_engine=clip)
    features = comparator.query_features(query)
    result = comparator.compare(features, comparator.candidate_features(candidate, features))
    assert result.label is label
    assert result.details["clip_similarity"] == pytest.approx(similarity, abs=1e-3)
    if method:
        assert result.method == method


def test_clip_is_not_used_when_the_query_has_a_face(fixture_image: Loader) -> None:
    query = fixture_image("face_a.jpg")
    clip = FakeClip({})
    comparator = Comparator(T, face_engine=FakeFaces({pixel_sha256(query): [unit(1, 0, 0)]}), clip_engine=clip)
    features = comparator.query_features(query)
    assert features.clip is None
    comparator.candidate_features(fixture_image("face_b.jpg"), features)
    assert clip.calls == 0


def test_clip_is_not_used_for_flat_placeholders(fixture_image: Loader) -> None:
    clip = FakeClip({})
    comparator = Comparator(T, clip_engine=clip)
    features = comparator.query_features(fixture_image("flat_gray.png"))
    assert features.clip is None
    assert clip.calls == 0


def test_embeddings_can_be_cleared(fixture_image: Loader) -> None:
    query = fixture_image("face_a.jpg")
    comparator = Comparator(T, face_engine=FakeFaces({pixel_sha256(query): [unit(1, 0, 0)]}))
    features = comparator.query_features(query)
    assert features.faces
    features.clear_embeddings()
    remaining: list[object] = [features.faces, features.clip]
    assert remaining == [None, None]
