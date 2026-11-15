"""Image decoding and normalisation.

Every image, whether uploaded by the user or fetched from a platform, goes
through :func:`decode_image` so comparisons always see the same thing: an
upright RGB bitmap with transparency flattened onto white.
"""

from __future__ import annotations

import base64
import hashlib
import io
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

#: Refuse to decode anything larger than this many pixels (decompression bombs).
MAX_PIXELS = 64_000_000
_MIN_SIDE = 8


class ImageError(Exception):
    """The bytes could not be turned into a usable image."""


def decode_image(data: bytes) -> Image.Image:
    """Decode bytes into a normalised RGB image.

    * the first frame of animated images is used;
    * EXIF orientation is applied;
    * transparency is flattened onto a white background.
    """
    if not data:
        raise ImageError("empty image data")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as raw:
                width, height = raw.size
                if width * height > MAX_PIXELS:
                    raise ImageError(f"image too large ({width}x{height})")
                raw.seek(0)
                raw.load()
                image = ImageOps.exif_transpose(raw) or raw
                rgb = _flatten(image)
    except ImageError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ImageError("image too large to decode safely") from exc
    except UnidentifiedImageError as exc:
        raise ImageError("unsupported or corrupt image format") from exc
    except (OSError, ValueError, SyntaxError, EOFError, IndexError, MemoryError) as exc:
        raise ImageError(f"could not decode image ({type(exc).__name__})") from exc
    if min(rgb.size) < _MIN_SIDE:
        raise ImageError(f"image too small ({rgb.size[0]}x{rgb.size[1]})")
    return rgb


def _flatten(image: Image.Image) -> Image.Image:
    has_alpha = image.mode in ("RGBA", "LA", "PA") or (image.mode == "P" and "transparency" in image.info)
    if has_alpha:
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        return Image.alpha_composite(background, rgba).convert("RGB")
    return image.convert("RGB")


def pixel_sha256(image: Image.Image) -> str:
    """SHA-256 over the decoded pixels (and dimensions), ignoring file format and metadata."""
    digest = hashlib.sha256()
    digest.update(f"{image.mode}:{image.size[0]}x{image.size[1]}:".encode())
    digest.update(image.tobytes())
    return digest.hexdigest()


def to_upload_jpeg(image: Image.Image, *, max_side: int = 1600, quality: int = 90) -> bytes:
    """Re-encode for sending to a search provider.

    The copy is built from decoded pixels, so EXIF data (GPS position, device,
    timestamps) from the original file is never uploaded.
    """
    copy = image.copy()
    copy.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    copy.save(buffer, format="JPEG", quality=quality, optimize=True)
    return buffer.getvalue()


def thumbnail_data_uri(image: Image.Image, *, size: int = 160) -> str:
    """Small JPEG as a ``data:`` URI, so reports are self-contained."""
    copy = image.copy()
    copy.thumbnail((size, size), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    copy.save(buffer, format="JPEG", quality=82)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def center_square(image: Image.Image) -> Image.Image:
    """Largest centred square (what most platforms crop an avatar to)."""
    width, height = image.size
    side = min(width, height)
    left = (width - side) // 2
    top = (height - side) // 2
    return image.crop((left, top, left + side, top + side))


def inner_square(image: Image.Image, fraction: float = 0.70) -> Image.Image:
    """Centred square that fits inside the circle platforms display avatars in.

    The inscribed square of a circle has ``1/sqrt(2) ~= 0.707`` of its diameter,
    so with ``fraction=0.70`` the crop never includes masked-out corners.
    """
    square = center_square(image)
    side = square.size[0]
    inner = max(_MIN_SIDE, round(side * fraction))
    offset = (side - inner) // 2
    return square.crop((offset, offset, offset + inner, offset + inner))
