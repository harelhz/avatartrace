"""Reproduce the threshold calibration documented in docs/thresholds.md.

Both commands need a local copy of the LFW dataset ("Labeled Faces in the Wild",
funneled version): a folder containing ``lfw_funneled/<person>/<person>_0001.jpg``
and ``pairs.txt``. LFW is a research benchmark; it is not shipped with this
project and nothing from it is committed.

    python scripts/calibrate.py hashes /data/lfw --images 2500
    python scripts/calibrate.py faces  /data/lfw --pairs 1500

``hashes`` measures the combined perceptual-hash distance for edited copies of a
picture (should be small) and for unrelated pictures (should be large).
``faces`` measures InsightFace cosine similarity for same-person and
different-person photo pairs.
"""

from __future__ import annotations

import argparse
import io
import random
import statistics
from collections.abc import Callable
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps

from avatartrace.compare import hashing
from avatartrace.compare.engines import InsightFaceEngine, Vector, cosine
from avatartrace.imaging import decode_image

Transform = Callable[[Image.Image], Image.Image]


def _jpeg(image: Image.Image, quality: int) -> Image.Image:
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=quality)
    return decode_image(buffer.getvalue())


def _circle(image: Image.Image, background: tuple[int, int, int]) -> Image.Image:
    width, height = image.size
    side = min(width, height)
    mask = Image.new("L", image.size, 0)
    box = ((width - side) // 2, (height - side) // 2, (width + side) // 2 - 1, (height + side) // 2 - 1)
    ImageDraw.Draw(mask).ellipse(box, fill=255)
    out = Image.new("RGB", image.size, background)
    out.paste(image, (0, 0), mask)
    return out


def _crop(image: Image.Image, fraction: float) -> Image.Image:
    width, height = image.size
    dx, dy = int(width * (1 - fraction) / 2), int(height * (1 - fraction) / 2)
    return image.crop((dx, dy, width - dx, height - dy))


TRANSFORMS: dict[str, Transform] = {
    "resize to 150 px": lambda im: im.resize((150, 150), Image.Resampling.LANCZOS),
    "resize to 48 px": lambda im: im.resize((48, 48), Image.Resampling.LANCZOS),
    "JPEG quality 30": lambda im: _jpeg(im, 30),
    "JPEG quality 10": lambda im: _jpeg(im, 10),
    "resize 150 px + JPEG 50": lambda im: _jpeg(im.resize((150, 150), Image.Resampling.BILINEAR), 50),
    "grayscale": lambda im: ImageOps.grayscale(im).convert("RGB"),
    "sepia": lambda im: ImageOps.colorize(ImageOps.grayscale(im), "#2b1a0e", "#f5e3c3"),
    "brightness +20%": lambda im: ImageEnhance.Brightness(im).enhance(1.2),
    "contrast +30%": lambda im: ImageEnhance.Contrast(im).enhance(1.3),
    "blur radius 2": lambda im: im.filter(ImageFilter.GaussianBlur(2)),
    "round mask, white corners": lambda im: _circle(im, (255, 255, 255)),
    "round mask, black corners": lambda im: _circle(im, (0, 0, 0)),
    "crop to 95%": lambda im: _crop(im, 0.95),
    "crop to 90%": lambda im: _crop(im, 0.90),
    "crop to 80%": lambda im: _crop(im, 0.80),
    "rotate 5 degrees": lambda im: im.rotate(5, resample=Image.Resampling.BICUBIC),
    "mirror": ImageOps.mirror,
}


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))]


def calibrate_hashes(root: Path, count: int, negative_pairs: int, seed: int) -> None:
    rng = random.Random(seed)
    files = sorted((root / "lfw_funneled").glob("*/*.jpg"))
    rng.shuffle(files)
    picked: list[Path] = []
    people: set[str] = set()
    for file in files:  # one picture per person, so every negative pair is two different people
        if file.parent.name not in people:
            people.add(file.parent.name)
            picked.append(file)
        if len(picked) == count:
            break
    images = [decode_image(f.read_bytes()) for f in picked]
    hashes = [hashing.compute_hashes(image) for image in images]
    usable = [i for i, h in enumerate(hashes) if h is not None]
    print(f"{len(usable)} pictures of {len(usable)} different people\n")
    cuts = (8.0, 12.0)
    print(f"{'edit':28} {'median':>7} {'p99':>7} {'max':>7}   <=8    <=12")
    for name, transform in TRANSFORMS.items():
        distances: list[float] = []
        for index in usable[:600]:
            original = hashes[index]
            edited = hashing.compute_hashes(transform(images[index]))
            if original is None or edited is None:
                continue
            distances.append(hashing.best_distance(original, edited).combined)
        shares = "  ".join(f"{sum(d <= cut for d in distances) / len(distances):6.1%}" for cut in cuts)
        print(
            f"{name:28} {statistics.median(distances):7.2f} {_percentile(distances, 0.99):7.2f} "
            f"{max(distances):7.2f}  {shares}"
        )
    negatives: list[float] = []
    for _ in range(negative_pairs):
        a, b = rng.sample(usable, 2)
        first, second = hashes[a], hashes[b]
        assert first is not None
        assert second is not None
        negatives.append(hashing.best_distance(first, second).combined)
    print(f"\n{negative_pairs} random pairs of different people: median distance {statistics.median(negatives):.2f}")
    for cut in (8.0, 10.0, 12.0, 14.0, 16.0):
        wrong = sum(d <= cut for d in negatives)
        print(f"  distance <= {cut:4.1f}: {wrong:6d} false matches ({wrong / negative_pairs:.5%})")


def calibrate_faces(root: Path, pairs_each: int, seed: int, model: str) -> None:
    rng = random.Random(seed)
    same: list[tuple[str, int, str, int]] = []
    different: list[tuple[str, int, str, int]] = []
    for line in (root / "pairs.txt").read_text(encoding="utf-8").splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) == 3:
            same.append((parts[0], int(parts[1]), parts[0], int(parts[2])))
        elif len(parts) == 4:
            different.append((parts[0], int(parts[1]), parts[2], int(parts[3])))
    rng.shuffle(same)
    rng.shuffle(different)
    engine = InsightFaceEngine(model)
    cache: dict[tuple[str, int], Vector | None] = {}

    def embedding(name: str, index: int) -> Vector | None:
        key = (name, index)
        if key not in cache:
            path = root / "lfw_funneled" / name / f"{name}_{index:04d}.jpg"
            found = engine.embed(decode_image(path.read_bytes()))
            cache[key] = found[0] if found else None
        return cache[key]

    genuine: list[float] = []
    impostor: list[float] = []
    skipped = 0
    for target, pairs in ((genuine, same[:pairs_each]), (impostor, different[:pairs_each])):
        for name_a, index_a, name_b, index_b in pairs:
            a, b = embedding(name_a, index_a), embedding(name_b, index_b)
            if a is None or b is None:
                skipped += 1
                continue
            target.append(cosine(a, b))
    print(f"model {model}: {len(genuine)} same-person pairs, {len(impostor)} different-person pairs")
    print(f"pairs skipped because no face was detected: {skipped}")
    print(f"same person:      mean {statistics.mean(genuine):.3f}, lowest {min(genuine):.3f}")
    print(f"different people: mean {statistics.mean(impostor):.3f}, highest {max(impostor):.3f}\n")
    print("threshold   same-person accepted   different-person accepted")
    for cut in (0.20, 0.25, 0.28, 0.32, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60):
        accepted = sum(s >= cut for s in genuine) / len(genuine)
        wrong = sum(s >= cut for s in impostor)
        share = wrong / len(impostor)
        print(f"   {cut:.2f}          {accepted:7.2%}              {wrong:4d} of {len(impostor)} ({share:.3%})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    hashes = commands.add_parser("hashes", help="perceptual-hash distances")
    hashes.add_argument("lfw", type=Path)
    hashes.add_argument("--images", type=int, default=2500)
    hashes.add_argument("--negative-pairs", type=int, default=400_000)
    hashes.add_argument("--seed", type=int, default=11)
    faces = commands.add_parser("faces", help="face similarity")
    faces.add_argument("lfw", type=Path)
    faces.add_argument("--pairs", type=int, default=1500, help="pairs of each kind")
    faces.add_argument("--seed", type=int, default=7)
    faces.add_argument("--model", default="buffalo_l")
    args = parser.parse_args()
    if args.command == "hashes":
        calibrate_hashes(args.lfw, args.images, args.negative_pairs, args.seed)
    else:
        calibrate_faces(args.lfw, args.pairs, args.seed, args.model)


if __name__ == "__main__":
    main()
