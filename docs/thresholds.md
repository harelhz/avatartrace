# Match thresholds

How avatartrace decides between `EXACT`, `NEAR-DUPLICATE`, `LIKELY SAME FACE`, `WEAK` and `NOT A MATCH`, what
the confidence number means, and the measurements behind the default thresholds.

## Decision order

For each candidate, the account's current profile picture is compared with your photo. The first rule that
applies wins:

| # | Rule | Label | Confidence |
| --- | --- | --- | --- |
| 1 | The decoded pixels are identical | `EXACT` | 100 |
| 2 | Perceptual-hash distance ≤ `hash_near_max` | `NEAR-DUPLICATE` | 80 to 99 |
| 3 | Face similarity ≥ `face_same_min` | `LIKELY SAME FACE` | 80 to 99 |
| 4 | CLIP similarity ≥ `clip_near_min` (only when your photo has no face, and only with `--clip`) | `NEAR-DUPLICATE` | 80 to 99 |
| 5 | Any signal inside its weak band | `WEAK` | 50 to 80 |
| 6 | Otherwise | `NOT A MATCH` | below 50 |

A candidate whose picture could not be fetched is `UNVERIFIED` and has no confidence at all.

## Defaults

| Setting | Default | Unit | Environment variable |
| --- | --- | --- | --- |
| `hash_near_max` | 8.0 | bits (0 = identical, about 27 = unrelated) | `AVATARTRACE_THRESHOLDS__HASH_NEAR_MAX` |
| `hash_weak_max` | 12.0 | bits | `AVATARTRACE_THRESHOLDS__HASH_WEAK_MAX` |
| `face_same_min` | 0.45 | cosine similarity | `AVATARTRACE_THRESHOLDS__FACE_SAME_MIN` |
| `face_weak_min` | 0.32 | cosine similarity | `AVATARTRACE_THRESHOLDS__FACE_WEAK_MIN` |
| `clip_near_min` | 0.95 | cosine similarity | `AVATARTRACE_THRESHOLDS__CLIP_NEAR_MIN` |
| `clip_weak_min` | 0.90 | cosine similarity | `AVATARTRACE_THRESHOLDS__CLIP_WEAK_MIN` |

The defaults favour precision: a false match points at an innocent person, a missed match only costs a lead.

## Confidence

Confidence is a piecewise-linear map of the deciding metric, anchored so that **80 is the match threshold and 50
the weak threshold for every method**. That is why `--min-confidence 80` (the default) shows exactly the three
match labels.

| Method | Anchors (metric → confidence) |
| --- | --- |
| Perceptual hash | 0 bits → 99, `hash_near_max` → 80, `hash_weak_max` → 50, 32 bits → 0 |
| Face | 0 → 0, `face_weak_min` → 50, `face_same_min` → 80, 0.80 → 97, 1.0 → 99 |
| CLIP | 0.5 → 0, `clip_weak_min` → 50, `clip_near_min` → 80, 1.0 → 99 |

Confidence is an ordering aid, not a probability. 91% does not mean a 9% chance of being wrong.

## 1. Exact

Both pictures are decoded, EXIF-rotated, flattened onto white if transparent, and hashed with SHA-256 over
their RGB pixels and dimensions. Container format and metadata do not matter: a JPEG and a lossless PNG of the
same pixels are `EXACT`.

Platforms re-encode almost every upload, so `EXACT` mostly appears when your copy is the platform's own file.
A re-encoded copy lands in `NEAR-DUPLICATE` with a confidence in the high nineties.

## 2. Perceptual hashes

Four 64-bit hashes are computed (pHash, dHash, aHash, wHash) and combined into one distance:

```text
distance = 0.40·pHash + 0.30·dHash + 0.15·aHash + 0.15·wHash      (Hamming distances, in bits)
```

Each picture is hashed three ways, and the smallest like-for-like distance is used:

| Crop | Purpose |
| --- | --- |
| `full` | the whole picture |
| `square` | the centred square, which is what platforms crop an upload to |
| `inner` | the centred 70% of that square. It lies inside the circle avatars are displayed in, so a screenshot of a round avatar (corners masked) still matches the square original. |

Nearly flat pictures (blank or single-colour placeholders) are not hashed at all, because any two of them would
"match".

### Measurements

Reproduce with `python scripts/calibrate.py hashes <lfw-dir> --images 2500`. The pictures are 2,500 LFW
photographs of 2,500 different people: all centred faces with similar framing, which is the hardest realistic
case for hashing avatars.

Edited copies of the same picture (600 pictures per edit):

| Edit | Median distance | 99th percentile | Maximum | ≤ 8 (match) | ≤ 12 (at least weak) |
| --- | --- | --- | --- | --- | --- |
| Resize to 150 px | 0.00 | 0.80 | 1.10 | 100% | 100% |
| Resize to 48 px | 0.15 | 1.25 | 1.70 | 100% | 100% |
| JPEG quality 30 | 0.00 | 0.80 | 1.10 | 100% | 100% |
| JPEG quality 10 | 0.30 | 1.85 | 2.20 | 100% | 100% |
| Resize to 150 px + JPEG 50 | 0.00 | 1.10 | 1.40 | 100% | 100% |
| Grayscale | 0.00 | 0.00 | 0.00 | 100% | 100% |
| Sepia | 0.00 | 0.80 | 1.20 | 100% | 100% |
| Brightness +20% | 0.30 | 3.05 | 4.40 | 100% | 100% |
| Contrast +30% | 0.75 | 2.30 | 2.60 | 100% | 100% |
| Blur, radius 2 | 0.00 | 0.90 | 1.40 | 100% | 100% |
| Round mask, white or black corners | 0.00 | 0.00 | 0.00 | 100% | 100% |
| Crop to 95% | 4.10 | 7.10 | 8.15 | 99.5% | 100% |
| Crop to 90% | 8.65 | 13.35 | 14.35 | 36.3% | 95.5% |
| Rotate 5° | 6.20 | 10.55 | 11.35 | 85.7% | 100% |
| Crop to 80% | 17.25 | 23.30 | 25.15 | 0% | 2.2% |
| Mirror | 24.00 | 34.10 | 39.80 | 0% | 0% |

Unrelated pictures (400,000 random pairs of different people, median distance 26.75):

| Distance | False matches | Rate |
| --- | --- | --- |
| ≤ 8 (`hash_near_max`) | 1 | 0.00025% |
| ≤ 10 | 16 | 0.004% |
| ≤ 12 (`hash_weak_max`) | 118 | 0.03% |
| ≤ 14 | 753 | 0.19% |
| ≤ 16 | 3,171 | 0.79% |

So at the default of 8 bits every faithful copy is caught (resizes, recompression, filters, round masks, light
crops) with about one false match in 400,000 comparisons of similar-looking portraits. Heavier crops and mirrored
copies are beyond hashing. For faces, the face model catches them; otherwise they surface as `WEAK` or not at all.

## 3. Faces

InsightFace `buffalo_l` (SCRFD detector, ArcFace ResNet-50 recogniser) runs locally on the CPU. Each face yields
a 512-dimensional unit vector; similarity is the cosine between two vectors.

- **No face in your photo**: face matching is skipped and the scan says so.
- **No face in the candidate's picture**: that candidate gets no face score.
- **Several faces**: every face in your photo is compared with every face in the candidate's picture (five largest
  each) and the best pair is used. The report notes that your photo contained several faces.
- **Tightly cropped avatars**: if no face is found, detection is retried with a border added, which recovers
  faces that fill the frame.

### Measurements

Reproduce with `python scripts/calibrate.py faces <lfw-dir> --pairs 1500`: 1,500 same-person and 1,500
different-person pairs from the LFW benchmark's `pairs.txt`.

|  | Mean similarity | Extreme |
| --- | --- | --- |
| Same person | 0.654 | lowest −0.077 |
| Different people | 0.004 | highest 0.243 |

| Threshold | Same-person pairs accepted | Different-person pairs accepted |
| --- | --- | --- |
| 0.20 | 96.5% | 5 of 1,500 (0.33%) |
| 0.25 | 96.5% | 0 of 1,500 |
| 0.32 (`face_weak_min`) | 96.3% | 0 of 1,500 |
| 0.40 | 95.8% | 0 of 1,500 |
| **0.45 (`face_same_min`)** | **94.5%** | **0 of 1,500** |
| 0.50 | 92.0% | 0 of 1,500 |
| 0.60 | 76.7% | 0 of 1,500 |

The default of 0.45 sits 0.2 above the highest different-person score observed and still accepts 94.5% of
same-person pairs. About 3.5% of LFW's same-person pairs score below 0.20; these include the benchmark's known
labelling errors and photos in which another, larger face is in frame.

On the repository's own fixtures (public-domain NASA portraits): two different photos of the same person score
0.78 and 0.79, different people 0.04 to 0.13, a mirrored copy 0.95, a copy cropped to 72% 0.97.

### Caveats that matter

- "0 of 1,500" bounds the false-match rate at roughly 0.2% with 95% confidence; it does not make it zero. Across
  many candidates, and with `--expand` trying 60 sites per username, an occasional look-alike will pass.
- LFW is mostly Western public figures photographed by the press. Face recognition is measurably less accurate
  for groups under-represented in training data, for children, and for low-resolution, heavily filtered or
  partly covered faces. Expect worse numbers there.
- Twins and close relatives can exceed the threshold.
- Raise `face_same_min` to 0.50 or 0.55 if a false match would be costly in your use.

## 4. CLIP (optional)

`--clip` adds a CLIP ViT-B/32 image embedding for photos **without a detectable face** (logos, drawings, anime,
pets). It is a deliberately weak signal: CLIP measures what a picture is *of*, and two different pictures of the
same kind of thing score high.

Measured on the repository's fixtures only, so treat these as indicative:

| Pair | Similarity |
| --- | --- |
| A logo and its resized or lightly cropped copy | 0.91 to 0.92 |
| A portrait and its edited copies | 0.90 to 0.99 |
| Two different logos | 0.78 |
| Two different people's portraits | 0.82 |
| A logo and a landscape | 0.77 |

With the defaults (0.95 and 0.90), CLIP alone confirms only near-identical pictures and otherwise contributes
`WEAK` results, which are hidden unless you lower `--min-confidence`. CLIP is never used for flat placeholder
images.

## Changing thresholds

Set any of the variables above in the environment or in `.env`. Invalid combinations (a weak threshold stricter
than its match threshold) are rejected at start-up. `avatartrace compare a.jpg b.jpg` shows the raw metrics for
two local pictures, which is the quickest way to see where a pair lands.
