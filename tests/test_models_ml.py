"""Tests that run the real InsightFace and CLIP models on the fixture images.

They are skipped automatically when the optional extra is not installed, so
the default ``pip install -e '.[dev]'`` test run stays light. CI has a separate
job that installs the ``face`` extra and runs the ``face`` tests.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from PIL import Image

from avatartrace.compare import (
    Comparator,
    EngineUnavailable,
    FastEmbedClipEngine,
    InsightFaceEngine,
    clip_available,
    face_available,
)
from avatartrace.config import Thresholds
from avatartrace.models import MatchLabel

Loader = Callable[[str], Image.Image]
T = Thresholds()


@pytest.fixture(scope="module")
def face_engine() -> InsightFaceEngine:
    if not face_available():
        pytest.skip("the 'face' extra is not installed")
    engine = InsightFaceEngine("buffalo_l")
    try:
        engine.warm_up()
    except EngineUnavailable as exc:
        pytest.skip(f"face model unavailable: {exc}")
    return engine


@pytest.fixture(scope="module")
def clip_engine() -> FastEmbedClipEngine:
    if not clip_available():
        pytest.skip("the 'clip' extra is not installed")
    engine = FastEmbedClipEngine()
    try:
        engine.warm_up()
    except EngineUnavailable as exc:
        pytest.skip(f"CLIP model unavailable: {exc}")
    return engine


def run(comparator: Comparator, load: Loader, query: str, candidate: str) -> tuple[MatchLabel, float, str]:
    features = comparator.query_features(load(query))
    result = comparator.compare(features, comparator.candidate_features(load(candidate), features))
    return result.label, result.confidence, result.method


@pytest.mark.face
@pytest.mark.parametrize(
    "name",
    ["face_a.jpg", "face_a_resized.jpg", "face_a_tiny.jpg", "face_a_circle.png", "face_a_tall.jpg", "face_b.jpg"],
)
def test_exactly_one_face_is_found(face_engine: InsightFaceEngine, fixture_image: Loader, name: str) -> None:
    assert len(face_engine.embed(fixture_image(name))) == 1


@pytest.mark.face
@pytest.mark.parametrize("name", ["logo.png", "logo_other.png", "landscape.jpg", "flat_gray.png"])
def test_no_face_in_faceless_pictures(face_engine: InsightFaceEngine, fixture_image: Loader, name: str) -> None:
    assert face_engine.embed(fixture_image(name)) == []


@pytest.mark.face
@pytest.mark.parametrize(
    ("query", "candidate"),
    [
        ("face_a.jpg", "face_a_other.jpg"),  # a different photo of the same person
        ("face_b.jpg", "face_b_other.jpg"),
        ("face_a.jpg", "face_a_mirrored.jpg"),  # edits that defeat perceptual hashing
        ("face_a.jpg", "face_a_cropped_heavy.jpg"),
        ("face_a_other.jpg", "face_a_filtered.jpg"),
    ],
)
def test_same_person_is_likely_same_face(
    face_engine: InsightFaceEngine, fixture_image: Loader, query: str, candidate: str
) -> None:
    label, confidence, method = run(Comparator(T, face_engine=face_engine), fixture_image, query, candidate)
    assert label is MatchLabel.LIKELY_SAME_FACE
    assert method == "face"
    assert 80.0 <= confidence < 100.0


@pytest.mark.face
@pytest.mark.parametrize(
    ("query", "candidate"),
    [
        ("face_a.jpg", "face_b.jpg"),
        ("face_a.jpg", "face_b_other.jpg"),
        ("face_a_other.jpg", "face_b.jpg"),
        ("face_b.jpg", "face_a_filtered.jpg"),
        ("face_a.jpg", "logo.png"),
        ("face_a.jpg", "landscape.jpg"),
    ],
)
def test_different_people_are_not_a_match(
    face_engine: InsightFaceEngine, fixture_image: Loader, query: str, candidate: str
) -> None:
    label, confidence, _ = run(Comparator(T, face_engine=face_engine), fixture_image, query, candidate)
    assert label is MatchLabel.NOT_A_MATCH
    assert confidence < 50.0


@pytest.mark.face
def test_edited_copy_stays_a_near_duplicate_with_the_face_model(
    face_engine: InsightFaceEngine, fixture_image: Loader
) -> None:
    label, _, method = run(Comparator(T, face_engine=face_engine), fixture_image, "face_a.jpg", "face_a_resized.jpg")
    assert (label, method) == (MatchLabel.NEAR_DUPLICATE, "perceptual-hash")


@pytest.mark.face
def test_tightly_cropped_face_is_still_detected(face_engine: InsightFaceEngine, fixture_image: Loader) -> None:
    face = fixture_image("face_a.jpg")
    width, height = face.size
    tight = face.crop((int(width * 0.30), int(height * 0.22), int(width * 0.72), int(height * 0.72)))
    assert len(face_engine.embed(tight)) == 1


@pytest.mark.face
def test_embeddings_are_unit_length(face_engine: InsightFaceEngine, fixture_image: Loader) -> None:
    (embedding,) = face_engine.embed(fixture_image("face_a.jpg"))
    assert embedding.shape == (512,)
    assert float((embedding**2).sum()) == pytest.approx(1.0, abs=1e-4)


@pytest.mark.clip
def test_clip_ranks_copies_above_other_pictures(clip_engine: FastEmbedClipEngine, fixture_image: Loader) -> None:
    from avatartrace.compare.engines import cosine

    logo = clip_engine.embed(fixture_image("logo.png"))
    copy = cosine(logo, clip_engine.embed(fixture_image("logo_resized.jpg")))
    other = cosine(logo, clip_engine.embed(fixture_image("logo_other.png")))
    scenery = cosine(logo, clip_engine.embed(fixture_image("landscape.jpg")))
    assert copy > T.clip_weak_min
    assert other < T.clip_weak_min
    assert scenery < T.clip_weak_min
    assert copy > other + 0.08


@pytest.mark.clip
def test_clip_alone_never_confirms_a_different_logo(clip_engine: FastEmbedClipEngine, fixture_image: Loader) -> None:
    comparator = Comparator(T, clip_engine=clip_engine)
    assert run(comparator, fixture_image, "logo.png", "logo_other.png")[0] is MatchLabel.NOT_A_MATCH
    assert run(comparator, fixture_image, "logo.png", "landscape.jpg")[0] is MatchLabel.NOT_A_MATCH
    assert run(comparator, fixture_image, "logo.png", "logo_resized.jpg")[0] is MatchLabel.NEAR_DUPLICATE
