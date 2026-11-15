"""Regenerate the derived test fixtures.

The four ``face_*.jpg`` base images are avatar-style crops of public-domain NASA
portraits (see README.md in this folder). Everything else is produced here:
edited copies of ``face_a.jpg`` and fully synthetic non-face images.

    python tests/fixtures/make_fixtures.py

The outputs are committed, so the test-suite never depends on this script or on
the exact JPEG encoder of the Pillow version in use.
"""

from __future__ import annotations

import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps

HERE = Path(__file__).parent


def _save_jpeg(image: Image.Image, name: str, quality: int = 90) -> None:
    image.convert("RGB").save(HERE / name, "JPEG", quality=quality, optimize=True)


def _crop_fraction(image: Image.Image, fraction: float) -> Image.Image:
    width, height = image.size
    dx = round(width * (1 - fraction) / 2)
    dy = round(height * (1 - fraction) / 2)
    return image.crop((dx, dy, width - dx, height - dy))


def _circle_png(image: Image.Image) -> Image.Image:
    """What a screenshot of a round avatar looks like: transparent corners."""
    rgba = image.convert("RGBA")
    mask = Image.new("L", rgba.size, 0)
    ImageDraw.Draw(mask).ellipse((0, 0, rgba.size[0] - 1, rgba.size[1] - 1), fill=255)
    rgba.putalpha(mask)
    return rgba


def _warm_filter(image: Image.Image) -> Image.Image:
    """A typical photo-app filter: warmer, brighter, more contrast, slight blur."""
    red, green, blue = image.split()
    red = red.point(lambda v: min(255, round(v * 1.10)))
    blue = blue.point(lambda v: round(v * 0.88))
    warm = Image.merge("RGB", (red, green, blue))
    warm = ImageEnhance.Brightness(warm).enhance(1.08)
    warm = ImageEnhance.Contrast(warm).enhance(1.18)
    return warm.filter(ImageFilter.GaussianBlur(0.6))


def _tall_photo(image: Image.Image) -> Image.Image:
    """The uncropped upload: the avatar is the centre square of this taller picture."""
    width, height = image.size
    band = height // 4
    top = image.crop((0, 0, width, band)).filter(ImageFilter.GaussianBlur(12))
    bottom = image.crop((0, height - band, width, height)).filter(ImageFilter.GaussianBlur(12))
    canvas = Image.new("RGB", (width, height + 2 * band))
    canvas.paste(ImageOps.flip(top), (0, 0))
    canvas.paste(image, (0, band))
    canvas.paste(ImageOps.flip(bottom), (0, band + height))
    return canvas


def _logo(seed: int, background: tuple[int, int, int], accent: tuple[int, int, int]) -> Image.Image:
    """A synthetic, face-free 'brand' avatar."""
    rng = random.Random(seed)
    image = Image.new("RGB", (400, 400), background)
    draw = ImageDraw.Draw(image)
    for _ in range(7):
        x, y = rng.randint(20, 380), rng.randint(20, 380)
        radius = rng.randint(25, 110)
        colour = tuple(max(0, min(255, c + rng.randint(-70, 70))) for c in accent)
        if rng.random() < 0.5:
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=colour)
        else:
            draw.rectangle((x - radius, y - radius // 2, x + radius, y + radius // 2), fill=colour)
    points = [(rng.randint(40, 360), rng.randint(40, 360)) for _ in range(5)]
    draw.polygon(points, fill=(245, 245, 245))
    draw.line([*points, points[0]], fill=(20, 20, 20), width=8)
    return image


def _landscape(seed: int) -> Image.Image:
    """Synthetic scenery: sky gradient, a sun, layered hills."""
    rng = random.Random(seed)
    image = Image.new("RGB", (400, 400))
    draw = ImageDraw.Draw(image)
    for y in range(400):
        draw.line([(0, y), (400, y)], fill=(40 + y // 4, 110 + y // 5, 215 - y // 6))
    draw.ellipse((270, 50, 340, 120), fill=(255, 236, 170))
    for layer in range(4):
        base = 210 + layer * 45
        points = [(0, 400), (0, base)]
        for x in range(0, 401, 40):
            points.append((x, base + rng.randint(-35, 25)))
        points += [(400, base), (400, 400)]
        shade = 150 - layer * 32
        draw.polygon(points, fill=(shade // 3, shade, shade // 2))
    return image


def main() -> None:
    face = Image.open(HERE / "face_a.jpg").convert("RGB")

    _save_jpeg(face.resize((150, 150), Image.Resampling.LANCZOS), "face_a_resized.jpg", quality=88)
    _save_jpeg(face.resize((48, 48), Image.Resampling.LANCZOS), "face_a_tiny.jpg", quality=85)
    _save_jpeg(face, "face_a_recompressed.jpg", quality=25)
    _save_jpeg(_crop_fraction(face, 0.95), "face_a_cropped_light.jpg")
    _save_jpeg(_crop_fraction(face, 0.72), "face_a_cropped_heavy.jpg")
    _save_jpeg(_warm_filter(face), "face_a_filtered.jpg")
    _save_jpeg(ImageOps.grayscale(face), "face_a_grayscale.jpg")
    _save_jpeg(ImageOps.mirror(face), "face_a_mirrored.jpg")
    _save_jpeg(_tall_photo(face), "face_a_tall.jpg")
    _circle_png(face.resize((320, 320), Image.Resampling.LANCZOS)).save(HERE / "face_a_circle.png", optimize=True)
    face.save(HERE / "face_a_lossless.png", optimize=True)

    logo = _logo(7, (24, 60, 140), (240, 150, 40))
    logo.save(HERE / "logo.png", optimize=True)
    _save_jpeg(logo.resize((128, 128), Image.Resampling.LANCZOS), "logo_resized.jpg", quality=80)
    _save_jpeg(_crop_fraction(logo, 0.95), "logo_cropped.jpg")
    _logo(99, (150, 30, 60), (60, 200, 170)).save(HERE / "logo_other.png", optimize=True)
    _save_jpeg(_landscape(3), "landscape.jpg")

    Image.new("RGB", (200, 200), (128, 128, 128)).save(HERE / "flat_gray.png")
    Image.new("RGB", (200, 200), (250, 250, 250)).save(HERE / "flat_white.png")
    print("fixtures written to", HERE)


if __name__ == "__main__":
    main()
