"""Perceptual hashing: pHash, dHash, aHash and wHash over three crops.

Each image is hashed three times so differently framed copies of the same
picture still line up:

``full``    the whole image;
``square``  the centred square (what platforms crop an upload to);
``inner``   the centred 70% of that square, which lies entirely inside the
            circle avatars are displayed in, so a screenshot of a round avatar
            (corners masked out) can still match the square original.
"""

from __future__ import annotations

from dataclasses import dataclass

import imagehash
import numpy as np
from PIL import Image

from avatartrace.imaging import center_square, inner_square

HASH_BITS = 64
VARIANTS: tuple[str, ...] = ("full", "square", "inner")

#: Weights of the four hashes in the combined distance. pHash and dHash are the
#: most discriminative; aHash and wHash mostly add robustness to filtering.
WEIGHTS: dict[str, float] = {"phash": 0.40, "dhash": 0.30, "ahash": 0.15, "whash": 0.15}

_MIN_HASH_SIDE = 32
_LOW_INFO_STDDEV = 6.0


@dataclass(frozen=True, slots=True)
class HashSet:
    """The four 64-bit hashes of one crop, stored as Python integers."""

    phash: int
    dhash: int
    ahash: int
    whash: int


@dataclass(frozen=True, slots=True)
class HashDistance:
    """Hamming distances (in bits) between two :class:`HashSet` objects."""

    variant: str
    phash: int
    dhash: int
    ahash: int
    whash: int

    @property
    def combined(self) -> float:
        return (
            WEIGHTS["phash"] * self.phash
            + WEIGHTS["dhash"] * self.dhash
            + WEIGHTS["ahash"] * self.ahash
            + WEIGHTS["whash"] * self.whash
        )


def _to_int(value: imagehash.ImageHash) -> int:
    bits = np.asarray(value.hash, dtype=bool).flatten()
    number = 0
    for bit in bits:
        number = (number << 1) | int(bit)
    return number


def is_low_information(image: Image.Image) -> bool:
    """True for (nearly) flat images, whose perceptual hashes are meaningless.

    Two different solid-colour or blank placeholder avatars hash to almost the
    same value, so hashing them would produce false near-duplicates.
    """
    small = np.asarray(image.convert("L").resize((32, 32), Image.Resampling.BILINEAR), dtype=np.float32)
    return float(small.std()) < _LOW_INFO_STDDEV


def _hash_one(image: Image.Image) -> HashSet:
    if min(image.size) < _MIN_HASH_SIDE:
        scale = _MIN_HASH_SIDE / min(image.size)
        image = image.resize(
            (max(_MIN_HASH_SIDE, round(image.size[0] * scale)), max(_MIN_HASH_SIDE, round(image.size[1] * scale))),
            Image.Resampling.LANCZOS,
        )
    return HashSet(
        phash=_to_int(imagehash.phash(image)),
        dhash=_to_int(imagehash.dhash(image)),
        ahash=_to_int(imagehash.average_hash(image)),
        whash=_to_int(imagehash.whash(image)),
    )


def compute_hashes(image: Image.Image) -> dict[str, HashSet] | None:
    """Hash all three crops, or return ``None`` for a low-information image."""
    if is_low_information(image):
        return None
    return {
        "full": _hash_one(image),
        "square": _hash_one(center_square(image)),
        "inner": _hash_one(inner_square(image)),
    }


def _hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def distance(a: HashSet, b: HashSet, variant: str) -> HashDistance:
    return HashDistance(
        variant=variant,
        phash=_hamming(a.phash, b.phash),
        dhash=_hamming(a.dhash, b.dhash),
        ahash=_hamming(a.ahash, b.ahash),
        whash=_hamming(a.whash, b.whash),
    )


def best_distance(query: dict[str, HashSet], candidate: dict[str, HashSet]) -> HashDistance:
    """Smallest combined distance over the like-for-like crops."""
    best: HashDistance | None = None
    for variant in VARIANTS:
        current = distance(query[variant], candidate[variant], variant)
        if best is None or current.combined < best.combined:
            best = current
    assert best is not None
    return best
