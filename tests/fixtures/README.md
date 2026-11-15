# Test fixtures

Small images used by the comparison tests. Nothing here comes from a social network or a research dataset.

## Base images with a face (public domain)

The four base images are avatar-style square crops (400 x 400 px) of official NASA astronaut portraits. NASA
imagery is in the public domain; the files were taken from Wikimedia Commons.

| File | Source | Person |
| --- | --- | --- |
| `face_a.jpg` | [Commander Eileen Collins - GPN-2000-001177.jpg](https://commons.wikimedia.org/wiki/File:Commander_Eileen_Collins_-_GPN-2000-001177.jpg) | A |
| `face_a_other.jpg` | [Eileen Collins, early NASA portrait.jpg](https://commons.wikimedia.org/wiki/File:Eileen_Collins,_early_NASA_portrait.jpg) | A, a different photo |
| `face_b.jpg` | [Mae Carol Jemison.jpg](https://commons.wikimedia.org/wiki/File:Mae_Carol_Jemison.jpg) | B |
| `face_b_other.jpg` | [Mae Jemison - Official portrait of 1987 astronaut candidate.jpg](https://commons.wikimedia.org/wiki/File:Mae_Jemison_-_Official_portrait_of_1987_astronaut_candidate.jpg) | B, a different photo |

Each crop is a square 2.3 times the height of the detected face, centred on the face, taken from the 800-px-wide
Commons rendering and resized to 400 px.

They give the tests the four situations that matter: the same picture, a different picture of the same person,
a different person, and no person at all.

## Derived images

Produced by [`make_fixtures.py`](make_fixtures.py) and committed so the tests do not depend on the JPEG encoder
of whichever Pillow version is installed.

| File | What it is | Expected against `face_a.jpg` |
| --- | --- | --- |
| `face_a_lossless.png` | the same pixels in another container | `EXACT` |
| `face_a_resized.jpg` | 150 px | `NEAR-DUPLICATE` |
| `face_a_tiny.jpg` | 48 px | `NEAR-DUPLICATE` |
| `face_a_recompressed.jpg` | JPEG quality 25 | `NEAR-DUPLICATE` |
| `face_a_filtered.jpg` | warm filter, more contrast, slight blur | `NEAR-DUPLICATE` |
| `face_a_grayscale.jpg` | grayscale | `NEAR-DUPLICATE` |
| `face_a_cropped_light.jpg` | 5% trimmed | `NEAR-DUPLICATE` |
| `face_a_circle.png` | round avatar with transparent corners | `NEAR-DUPLICATE` |
| `face_a_tall.jpg` | taller picture whose centre square is the avatar | `NEAR-DUPLICATE` |
| `face_a_cropped_heavy.jpg` | 28% trimmed | `LIKELY SAME FACE` (hashing alone: no match) |
| `face_a_mirrored.jpg` | mirrored | `LIKELY SAME FACE` (hashing alone: no match) |

## Synthetic images without a face

Drawn by `make_fixtures.py`: `logo.png` and its copies `logo_resized.jpg` and `logo_cropped.jpg`, a different
logo `logo_other.png`, `landscape.jpg`, and the flat placeholders `flat_gray.png` and `flat_white.png`.

## Regenerating

```bash
python tests/fixtures/make_fixtures.py
```
