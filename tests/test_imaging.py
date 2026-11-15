"""Decoding and normalising images."""

from __future__ import annotations

import io
from collections.abc import Callable

import pytest
from PIL import Image

from avatartrace import imaging
from avatartrace.imaging import ImageError, decode_image, pixel_sha256


def _png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def test_same_pixels_in_different_containers_hash_identically(fixture_image: Callable[[str], Image.Image]) -> None:
    jpeg = fixture_image("face_a.jpg")
    png = fixture_image("face_a_lossless.png")
    assert pixel_sha256(jpeg) == pixel_sha256(png)


def test_recompression_changes_the_pixel_hash(fixture_image: Callable[[str], Image.Image]) -> None:
    assert pixel_sha256(fixture_image("face_a.jpg")) != pixel_sha256(fixture_image("face_a_recompressed.jpg"))


def test_pixel_hash_depends_on_dimensions() -> None:
    wide = Image.new("RGB", (16, 8), (10, 20, 30))
    tall = Image.new("RGB", (8, 16), (10, 20, 30))
    assert pixel_sha256(wide) != pixel_sha256(tall)


def test_transparency_is_flattened_onto_white(fixture_image: Callable[[str], Image.Image]) -> None:
    image = fixture_image("face_a_circle.png")
    assert image.mode == "RGB"
    assert image.getpixel((0, 0)) == (255, 255, 255)


def test_palette_transparency_is_flattened() -> None:
    palette = Image.new("P", (16, 16), 0)
    palette.putpalette([200, 0, 0] + [0, 0, 0] * 255)
    palette.info["transparency"] = 0
    buffer = io.BytesIO()
    palette.save(buffer, "PNG", transparency=0)
    assert decode_image(buffer.getvalue()).getpixel((3, 3)) == (255, 255, 255)


def test_exif_orientation_is_applied() -> None:
    image = Image.new("RGB", (40, 20), (255, 0, 0))
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 degrees clockwise when displayed
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", exif=exif)
    assert decode_image(buffer.getvalue()).size == (20, 40)


def test_first_frame_of_an_animation_is_used() -> None:
    frames = [Image.new("RGB", (32, 32), colour) for colour in ((255, 0, 0), (0, 0, 255))]
    buffer = io.BytesIO()
    frames[0].save(buffer, "GIF", save_all=True, append_images=frames[1:], duration=100)
    red, green, blue = decode_image(buffer.getvalue()).getpixel((5, 5))  # type: ignore[misc]
    assert red > 200
    assert blue < 60
    assert green < 60


@pytest.mark.parametrize(
    "data",
    [b"", b"not an image", b"\x89PNG\r\n\x1a\n" + b"\x00" * 40, b"<svg xmlns='http://www.w3.org/2000/svg'/>"],
)
def test_unreadable_data_raises_image_error(data: bytes) -> None:
    with pytest.raises(ImageError):
        decode_image(data)


def test_truncated_file_raises_image_error(fixture_bytes: Callable[[str], bytes]) -> None:
    with pytest.raises(ImageError):
        decode_image(fixture_bytes("face_a.jpg")[:900])


def test_tiny_image_is_rejected() -> None:
    with pytest.raises(ImageError, match="too small"):
        decode_image(_png(Image.new("RGB", (4, 4))))


def test_oversized_image_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(imaging, "MAX_PIXELS", 1000)
    with pytest.raises(ImageError, match="too large"):
        decode_image(_png(Image.new("RGB", (64, 64))))


def test_upload_copy_strips_exif_and_caps_size() -> None:
    image = Image.new("RGB", (4000, 2000), (20, 120, 220))
    exif = Image.Exif()
    exif[0x010F] = "SecretCameraMaker"
    exif[0x8825] = {1: "N", 2: (32.0, 5.0, 0.0)}  # GPS IFD
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", exif=exif)
    original = buffer.getvalue()
    assert b"SecretCameraMaker" in original

    upload = imaging.to_upload_jpeg(decode_image(original))
    assert b"SecretCameraMaker" not in upload
    with Image.open(io.BytesIO(upload)) as sent:
        assert max(sent.size) == 1600
        assert not sent.getexif()


def test_thumbnail_is_a_small_data_uri(fixture_image: Callable[[str], Image.Image]) -> None:
    uri = imaging.thumbnail_data_uri(fixture_image("face_a.jpg"), size=96)
    assert uri.startswith("data:image/jpeg;base64,")
    assert len(uri) < 20_000


def test_center_and_inner_squares() -> None:
    image = Image.new("RGB", (300, 200))
    assert imaging.center_square(image).size == (200, 200)
    assert imaging.inner_square(image).size == (140, 140)
    assert imaging.inner_square(Image.new("RGB", (9, 9))).size == (8, 8)
