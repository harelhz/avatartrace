"""Optional embedding engines: InsightFace (faces) and CLIP (everything else).

Both are imported lazily so the core tool works without the heavy extras, and
both run locally on the CPU: no image or embedding leaves the machine here.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import warnings
from typing import Any, Protocol

import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageOps

Vector = NDArray[np.float32]


class EngineUnavailable(Exception):
    """The optional dependency or its model could not be loaded."""


class FaceEngine(Protocol):
    name: str

    def embed(self, image: Image.Image) -> list[Vector]:
        """One L2-normalised embedding per detected face, largest face first."""
        ...


class ClipEngine(Protocol):
    name: str

    def embed(self, image: Image.Image) -> Vector:
        """One L2-normalised embedding for the whole image."""
        ...


def _normalise(vector: NDArray[Any]) -> Vector:
    array = np.asarray(vector, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(array))
    if norm == 0.0:
        return array
    normalised: Vector = (array / np.float32(norm)).astype(np.float32)
    return normalised


def cosine(a: Vector, b: Vector) -> float:
    """Cosine similarity of two L2-normalised vectors, clipped to [-1, 1]."""
    return float(np.clip(np.dot(a, b), -1.0, 1.0))


def best_face_similarity(query: list[Vector], candidate: list[Vector]) -> float | None:
    """Highest similarity over every (query face, candidate face) pair."""
    if not query or not candidate:
        return None
    return max(cosine(q, c) for q in query for c in candidate)


def face_available() -> bool:
    return importlib.util.find_spec("insightface") is not None and importlib.util.find_spec("onnxruntime") is not None


def clip_available() -> bool:
    return importlib.util.find_spec("fastembed") is not None


class InsightFaceEngine:
    """Face detection (SCRFD) and recognition (ArcFace) through InsightFace."""

    def __init__(self, model: str = "buffalo_l", det_size: int = 640, max_faces: int = 5) -> None:
        self.name = f"insightface:{model}"
        self._model = model
        self._det_size = det_size
        self._max_faces = max_faces
        self._app: Any = None

    def _load(self) -> Any:
        if self._app is not None:
            return self._app
        if not face_available():
            raise EngineUnavailable("face matching needs the optional extra: pip install 'avatartrace[face]'")
        try:
            # InsightFace prints model details straight to stdout while loading.
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                from insightface.app import FaceAnalysis

                app = FaceAnalysis(
                    name=self._model,
                    allowed_modules=["detection", "recognition"],
                    providers=["CPUExecutionProvider"],
                )
                app.prepare(ctx_id=-1, det_size=(self._det_size, self._det_size))
        except Exception as exc:  # the library raises many unrelated types here
            raise EngineUnavailable(f"could not load InsightFace model '{self._model}': {exc}") from exc
        self._app = app
        return app

    def warm_up(self) -> None:
        """Load the model now (downloads it on first use)."""
        self._load()

    def _detect(self, app: Any, image: Image.Image) -> list[Any]:
        bgr = np.ascontiguousarray(np.asarray(image.convert("RGB"), dtype=np.uint8)[:, :, ::-1])
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # third-party deprecation chatter
            faces = app.get(bgr)
        return list(faces) if faces is not None else []

    def embed(self, image: Image.Image) -> list[Vector]:
        app = self._load()
        faces = self._detect(app, image)
        if not faces:
            # Avatars are often cropped so tightly that the face fills the frame
            # and the detector's anchors miss it. A border makes it detectable.
            border = max(image.size) // 2
            padded = ImageOps.expand(image, border=border, fill=(127, 127, 127))
            faces = self._detect(app, padded)
        scored: list[tuple[float, Vector]] = []
        for face in faces:
            embedding = getattr(face, "normed_embedding", None)
            bbox = getattr(face, "bbox", None)
            if embedding is None or bbox is None:
                continue
            area = float(max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1]))
            scored.append((area, _normalise(embedding)))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [vector for _, vector in scored[: self._max_faces]]


class FastEmbedClipEngine:
    """CLIP image embeddings through fastembed (ONNX Runtime, no PyTorch)."""

    def __init__(self, model: str = "Qdrant/clip-ViT-B-32-vision") -> None:
        self.name = f"clip:{model}"
        self._model_name = model
        self._model: Any = None

    def _load(self) -> Any:
        if self._model is not None:
            return self._model
        if not clip_available():
            raise EngineUnavailable("CLIP similarity needs the optional extra: pip install 'avatartrace[clip]'")
        try:
            from fastembed import ImageEmbedding

            self._model = ImageEmbedding(model_name=self._model_name)
        except Exception as exc:  # download, ONNX and model-name errors
            raise EngineUnavailable(f"could not load CLIP model '{self._model_name}': {exc}") from exc
        return self._model

    def warm_up(self) -> None:
        self._load()

    def embed(self, image: Image.Image) -> Vector:
        model = self._load()
        vectors = list(model.embed([image.convert("RGB")]))
        if not vectors:
            raise EngineUnavailable("CLIP model returned no embedding")
        return _normalise(vectors[0])
