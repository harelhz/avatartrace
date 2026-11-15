# avatartrace

[![CI](https://github.com/harelhz/avatartrace/actions/workflows/ci.yml/badge.svg)](https://github.com/harelhz/avatartrace/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)

Give it **one profile picture**. It returns the accounts that use that same picture, or a very similar one, as
their profile picture.

```console
$ avatartrace scan photo.jpg --expand

 Platform   Username    Profile URL                         Match type        Confidence  Found via
 Instagram  @john.doe   https://www.instagram.com/john.doe/ EXACT                   100%  google_vision
 TikTok     @johnd      https://www.tiktok.com/@johnd       NEAR-DUPLICATE           91%  serpapi_google_lens
 GitHub     jdoe        https://github.com/jdoe             LIKELY SAME FACE         84%  username expansion (from tiktok:@johnd)
```

The table above is illustrative. This is a real run with no API keys at all, where the photo is given as an
avatar-CDN URL (the account behind it is found from the URL, verified, and its username tried on 60 more sites):

```console
$ avatartrace scan "https://avatars.githubusercontent.com/u/583231?v=4" --expand

 Platform  Username  Profile URL                 Match type  Confidence  Found via
 GitHub    octocat   https://github.com/octocat  EXACT             100%  input URL (avatar CDN)

1 candidate(s) verified, 60 expansion check(s), 75 HTTP request(s). Matches are leads, not proof.
```

avatartrace is built for three jobs: checking whether someone you met online is using stolen photos, finding
accounts that impersonate you, and reviewing your own exposure. **A match is a lead, not proof**, and the tool
must not be used to stalk, harass or identify strangers. Read [Ethics & safety](#ethics--safety) before you use it.

## How it works

```text
photo ──► 1. discovery ──► 2. extraction ──► 3. verification ──► 4. expansion (optional)
          search providers   URLs to            fetch each          try verified usernames
          return URLs        (platform,         account's public    on 60 more sites and
                             username)          picture, compare    verify those too
```

1. **Discovery.** There is no public index of avatars, so the photo is looked up through reverse-image and
   face-search APIs that have already crawled the web. Every provider is optional and switches on when its API key
   is set. Search-engine result pages are never scraped.
2. **Extraction.** Each returned URL is parsed into a `(platform, username)` pair by a data-driven parser
   ([`platforms.json`](src/avatartrace/data/platforms.json), 68 platforms). Posts, hashtags, login pages and other
   non-profile pages are dropped. Avatar-CDN URLs are mapped back to accounts where the platform allows it
   (`avatars.githubusercontent.com/u/583231` becomes `octocat`).
3. **Verification.** For every candidate, the **current** profile picture is fetched from a public, logged-out
   source (an official API where one exists, otherwise the profile page's metadata) and compared with your photo
   locally. A candidate whose picture cannot be fetched is kept as `UNVERIFIED (found via <provider>)`. A score is
   never invented.
4. **Expansion** (`--expand`). Each verified username is tried on 60 more sites; pictures found there go through
   the same comparison. A username on its own is never reported as a match.

## Install

Python 3.11 or newer.

```bash
git clone https://github.com/harelhz/avatartrace.git
cd avatartrace
python -m venv .venv && source .venv/bin/activate
pip install ".[face,web]"
```

| Extra  | Adds                                           | Size                                           |
| ------ | ---------------------------------------------- | ---------------------------------------------- |
| (none) | exact and near-duplicate matching, all reports | small                                          |
| `face` | face matching (InsightFace + ONNX Runtime)     | model pack of about 280 MB, fetched on first use |
| `clip` | CLIP similarity for pictures without a face    | model of about 350 MB, fetched on first use    |
| `web`  | the local web UI                               | small                                          |
| `all`  | everything above                               |                                                |

Docker (includes `face` and `web`):

```bash
docker build -t avatartrace .
docker run --rm -v "$PWD:/work" avatartrace scan /work/photo.jpg --report /work/report.html
```

## Quick start

**Without any API key** the tool still works. It prints ready-to-click reverse-search links (Google Lens, Yandex,
Bing, TinEye); open them, then hand the profile URLs you find back for verification:

```bash
avatartrace scan photo.jpg
avatartrace scan photo.jpg --candidate https://www.instagram.com/john.doe/ --candidate https://x.com/johnd --expand
```

If the photo is already online, pass its URL. An avatar-CDN URL identifies its account directly:

```bash
avatartrace scan "https://avatars.githubusercontent.com/u/583231?v=4" --expand
```

**With API keys** (see [Providers](#providers)), copy [`.env.example`](.env.example) to `.env`, fill in the keys you
have, and run:

```bash
avatartrace scan photo.jpg --expand --report out.html --report out.json
```

The first time a scan would send your photo anywhere, avatartrace lists exactly which services will receive it and
asks you to confirm. In scripts, confirm with `--i-understand-upload`. Use `--providers none` to keep the photo on
your machine.

### Commands

| Command | Purpose |
| --- | --- |
| `avatartrace scan PHOTO` | The main command. `PHOTO` is a file path or a public URL. |
| `avatartrace web` | Local web UI on `http://127.0.0.1:8765`: drag and drop a photo, get the results table. |
| `avatartrace providers` | Which providers exist and which are configured. |
| `avatartrace platforms` | Supported platforms and how each picture is obtained. |
| `avatartrace parse URL...` | Show which account a URL points at (offline). |
| `avatartrace compare A B` | Compare two local pictures with the scan logic (offline). |
| `avatartrace clear-cache` | Delete everything kept by earlier `--keep-cache` runs. |

### `scan` options

| Option | Default | Meaning |
| --- | --- | --- |
| `--expand` | off | Try each verified username on 60 more sites. |
| `--providers` | `all` | `all` (every configured provider), `none`, or a comma list such as `tineye,google_vision`. |
| `--min-confidence` | `80` | Hide verified results below this confidence. Unverified leads are always listed. |
| `--report PATH` | | Write a report; the format follows the extension (`.html`, `.json`, `.csv`). Repeatable. |
| `--candidate URL`, `-c` | | Verify this profile URL as well. Repeatable. |
| `--image-url URL` | | Public URL of the same photo, for providers that only accept URLs (SerpApi). |
| `--i-understand-upload` | | Confirm that the photo may be sent to the enabled providers. |
| `--no-face` | | Turn face matching off. |
| `--clip` | off | Use CLIP similarity for photos in which no face is found. |
| `--keep-cache` | off | Keep fetched pictures and embeddings on disk after the run. |
| `--show-rejected` | off | Also list candidates whose picture did not match. |

### Reports

- **HTML**: your photo next to each matched profile picture, with the score, the reason and a link. The file is
  self-contained and loads nothing from the network when opened.
- **JSON**: everything, including unverified leads, rejected candidates, per-provider status and raw metrics.
- **CSV**: the results table (`Platform, Username, Profile URL, Match type, Confidence, Found via, ...`).

### Web UI

```bash
avatartrace web --open
```

The server binds to `127.0.0.1` only and cannot be told to do otherwise. It also checks the `Host` header, refuses
cross-origin requests and requires a per-session token, so another website open in your browser cannot drive it.
In Docker, use host networking on Linux: `docker run --rm --network host avatartrace web`.

## Providers

| Provider (`--providers` name) | Key in `.env` | Receives | Cost | Finds |
| --- | --- | --- | --- | --- |
| Google Cloud Vision, Web Detection (`google_vision`) | `GOOGLE_VISION_API_KEY` | the photo | Paid. First 1,000 requests a month are free. | Pages with the same or a partially matching image |
| Google Lens via SerpApi (`serpapi_google_lens`) | `SERPAPI_API_KEY` | the photo's URL | SerpApi free plan, then paid. Two searches per scan. | Exact and visually similar matches |
| Google Reverse Image via SerpApi (`serpapi_google_reverse_image`) | `SERPAPI_API_KEY` | the photo's URL | One SerpApi search | Pages containing the image |
| Yandex Images via SerpApi (`serpapi_yandex`) | `SERPAPI_API_KEY` | the photo's URL | One SerpApi search | Pages containing the image; strong on faces and on VK |
| Bing Reverse Image via SerpApi (`serpapi_bing`) | `SERPAPI_API_KEY` | the photo's URL | One SerpApi search | Pages with this image |
| TinEye (`tineye`) | `TINEYE_API_KEY` | the photo | Paid, prepaid bundles | Exact copies and edits, with the pages they appear on |
| FaceCheck.ID (`facecheck`) | `FACECHECK_API_TOKEN` | the photo | Paid: 3 credits per search, credits bought with cryptocurrency. Demo mode is free. | The same **face**, including in different photos |
| SauceNAO (`saucenao`) | `SAUCENAO_API_KEY` | the photo | Free account key with daily limits | Artwork, anime and illustration sources |

Notes:

- The four SerpApi engines accept a **public image URL only**. For a local file they are skipped unless you add
  `--image-url`. avatartrace never uploads your photo to a file host by itself to obtain a URL.
- Each API was checked against its current documentation and a live endpoint in November 2026. The retired Bing
  Search APIs are not used. No provider could be exercised with a paid key during development; they are tested
  against responses that follow each provider's documented schema.
- `AVATARTRACE_FACECHECK_DEMO=true` searches only FaceCheck's first 100,000 faces and costs nothing. It is for
  testing the integration, not for real results.
- Limit SerpApi to some engines with `AVATARTRACE_SERPAPI_ENGINES='["google_lens","yandex_images"]'`.

Optional keys for official **profile** APIs, used in step 3 only:

| Key | Effect |
| --- | --- |
| `GITHUB_TOKEN` | Raises the GitHub API limit from 60 to 5,000 requests an hour. |
| `YOUTUBE_API_KEY` | Uses the YouTube Data API instead of the channel page. |
| `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET` | Uses the Twitch Helix API instead of the channel page. |
| `X_BEARER_TOKEN` | Uses the X API v2 instead of the public profile HTML. |
| `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET` | Makes Reddit verifiable at all (it blocks logged-out clients). |

These are application keys for official APIs, never a user login.

## Match types and confidence

| Label | Meaning | Decided by |
| --- | --- | --- |
| `EXACT` | Pixel-for-pixel the same picture (100%). | SHA-256 of the decoded, normalised pixels |
| `NEAR-DUPLICATE` | The same picture after resizing, recompression, a filter or light cropping. | pHash, dHash, aHash and wHash over three crops, including a centre crop for round avatars |
| `LIKELY SAME FACE` | A different picture that very probably shows the same person. | InsightFace (ArcFace) embeddings, cosine similarity |
| `WEAK` | Some similarity, below the match threshold. Hidden by default. | any of the above |
| `NOT A MATCH` | The account's picture is something else. Hidden by default. | |
| `UNVERIFIED` | Returned by a provider, but the picture could not be fetched logged-out. No score. | |

Confidence is a 0 to 100 scale on which **80 is the match threshold and 50 the weak threshold** for every method.
The thresholds live in configuration, were calibrated on the test fixtures and on 3,000 LFW face pairs and 400,000
unrelated picture pairs, and are documented with the measured error rates in
[docs/thresholds.md](docs/thresholds.md).

Platforms almost always re-encode uploads, so a picture you hold will normally come back as `NEAR-DUPLICATE` at
95% or more rather than `EXACT`. `EXACT` appears when your copy is the platform's own file.

## Platforms

68 platforms are recognised in URLs: Instagram, TikTok, Facebook (vanity URLs and `profile.php?id=`), Threads
(`threads.net` and `threads.com`), X/Twitter, GitHub, Reddit, YouTube (`@handle`, `/c/`, `/channel/`, `/user/`),
LinkedIn, Pinterest, Snapchat, Telegram, VK, Twitch, Bluesky, Mastodon and other fediverse instances, Steam,
SoundCloud, Spotify, Medium, Linktree, and 47 more. Redirect wrappers (`l.facebook.com`, `vk.com/away.php`,
archive.org copies) and common third-party mirrors (Picuki, Nitter, Social Blade and others) are unwrapped to the
account they refer to.

Whether a candidate can be **verified** depends on what the platform shows a logged-out client that identifies
itself honestly. [docs/platforms.md](docs/platforms.md) has the full, dated table. In short, on a residential
connection in November 2026:

- **Verifiable** (53 platforms): GitHub, GitLab, Bluesky, Mastodon, YouTube, Twitch, Steam, Telegram, Pinterest,
  SoundCloud, Spotify, Linktree, Threads, X, Instagram, Facebook and 37 others.
- **Usually `UNVERIFIED`**: TikTok (JavaScript bot challenge), LinkedIn (HTTP 999 / auth wall), Reddit (blocks
  logged-out clients unless you add API credentials), Snapchat (404 for non-browser clients), VK (generic page
  without profile data), Medium (Cloudflare 403), and Patreon, Behance, Letterboxd, CodePen, Kaggle, Ko-fi,
  Buy Me a Coffee, Flickr and Dribbble.
- **Network-dependent**: Instagram and Facebook answer residential addresses but often redirect data-centre
  addresses (CI, VPS, some VPNs) to the login page. Private and restricted profiles are always `UNVERIFIED`.

avatartrace does not work around any of these. See [What it will not do](#what-it-will-not-do).

## Configuration

Settings come from environment variables or a `.env` file (the working directory, or
`~/.config/avatartrace/.env`). Provider keys work with or without the `AVATARTRACE_` prefix.

| Variable | Default | Meaning |
| --- | --- | --- |
| `AVATARTRACE_PER_HOST_INTERVAL` | `1.0` | Minimum seconds between two requests to the same site. |
| `AVATARTRACE_MAX_CONCURRENCY` | `8` | Requests in flight at once, across all sites. |
| `AVATARTRACE_REQUEST_TIMEOUT` | `20` | Seconds before a request is abandoned. |
| `AVATARTRACE_MAX_CANDIDATES` | `150` | Most candidates verified in one scan. |
| `AVATARTRACE_MAX_EXPANSION_SEEDS` | `5` | Most distinct usernames expanded in one scan. |
| `AVATARTRACE_INCLUDE_CONTENT_URLS` | `true` | Attribute posts that name their author (`x.com/<user>/status/...`) to that author. |
| `AVATARTRACE_FACE_MODEL` | `buffalo_l` | InsightFace model pack. |
| `AVATARTRACE_THRESHOLDS__HASH_NEAR_MAX` | `8` | See [docs/thresholds.md](docs/thresholds.md) for all six thresholds. |
| `AVATARTRACE_USER_AGENT` | `avatartrace/<version> (+repo URL)` | Sent with every request. Keep it honest. |
| `AVATARTRACE_CACHE_DIR` | `~/.cache/avatartrace` | Where `--keep-cache` writes. |

## Ethics & safety

### Use it for

- **Catfish and romance-scam checks.** Someone you met online sends photos; see whether the same pictures belong
  to other names and accounts.
- **Finding impersonators of yourself**, or of a person or organisation that asked you to look.
- **Checking your own exposure**: where your profile picture, and so your identity, is linked across platforms.
- Trust-and-safety, fraud and journalistic work that has a lawful basis and proportionate purpose.

### Do not use it for

- **Stalking, harassment, doxxing or intimidation.**
- **Identifying strangers**: people photographed in public, people on dating apps who have not agreed to be
  looked up, anyone whose photo you simply happen to have.
- Unmasking anonymous or pseudonymous accounts, including those of activists, journalists, abuse survivors and
  sex workers.
- Building datasets or watchlists of people, or any bulk processing. The tool takes one photo per run by design.
- Decisions about a person (hiring, renting, lending, discipline) based on a match.

### Matches are leads, not proof

A matching picture does not show who controls an account. Scammers copy pictures precisely because it works;
stock photos and memes are shared by thousands of accounts; people look alike; face models make mistakes, and
make more of them for groups under-represented in their training data. Never confront, report or publish on the
strength of a match alone. Corroborate it first.

### What it will not do

These are design decisions, not missing features:

- **Public data only.** No logins, no stored credentials, no session cookies, no private-profile access.
- **No evasion.** No CAPTCHA solving, no proxy rotation, no browser impersonation, no retries after a refusal.
  Requests carry an honest `User-Agent`. When a site says no, the result is `UNVERIFIED`.
- **Rate limits.** At most one request a second to any one site, and a cap on concurrent requests.
- **Consent before upload.** Nothing is sent to a search provider until you have seen which services will receive
  the photo and confirmed. The uploaded copy is re-encoded from pixels, so EXIF data (GPS position, device,
  timestamps) never leaves your machine.
- **Nothing kept.** Fetched pictures and face embeddings exist in memory only and are discarded when the scan
  ends. `--keep-cache` is the explicit opt-in to write them to disk (owner-only permissions);
  `avatartrace clear-cache` deletes them.
- **Local comparison.** Hashing, face matching and CLIP run on your machine. No telemetry.

### Privacy law

This is orientation, not legal advice. You are responsible for your own use.

- **EU / UK (GDPR).** A face embedding computed to recognise a person is biometric data, a special category
  under Article 9, and profile pictures and usernames are personal data. Purely personal or household use falls
  outside the Regulation; any professional, organisational or published use needs a lawful basis, and special
  category data needs an Article 9 condition on top. Sending someone's photo to a search provider is itself a
  disclosure to a third party.
- **Israel.** The Privacy Protection Law, 5741-1981, as amended by Amendment 13 (in force since August 2025),
  treats biometric identifiers used to identify a person as information of special sensitivity, with stricter
  duties and enforcement by the Privacy Protection Authority. Processing someone else's face for identification
  without a lawful basis can expose you to administrative and civil liability.
- **Elsewhere.** Several jurisdictions regulate face recognition specifically (Illinois BIPA and other US state
  laws, among others), and anti-stalking and harassment laws apply everywhere regardless of the tool used.
- **Provider and platform terms** also apply: each search provider restricts what its results may be used for.

If you believe avatartrace is being used to harm someone, see [SECURITY.md](SECURITY.md).

## Limitations

- Discovery is only as good as the providers. Reverse-image engines index a small share of profile pictures,
  and private accounts not at all. An empty result does not mean the picture is not in use.
- Verification compares against the account's **current** picture. If it changed since the search engine saw it,
  the result is `NOT A MATCH`, which is correct for today.
- Heavy crops, mirrored copies and added text defeat perceptual hashing; for faces the face model usually still
  matches them, for other pictures use `--clip` and treat the result as weak evidence.
- Default and placeholder avatars are filtered where known, but a platform's generic silhouette can still appear
  as a low-scoring candidate.
- Pages change. The X and Linktree pictures are read from page markup that those sites can alter at any time.
  Run `python scripts/check_platforms.py` to see the current state.
- Face matching was calibrated on LFW, which under-represents many groups. Error rates on other populations,
  on children, and on low-resolution or heavily filtered pictures will be higher.

## Development

```bash
pip install -e ".[dev]"            # add ,face to run the face-model tests too
ruff check . && ruff format --check .
mypy
pytest                             # 618 tests, no network access
python scripts/check_platforms.py  # live health check of every platform (uses the network)
```

[CONTRIBUTING.md](CONTRIBUTING.md) explains how to add a platform or a provider.

## License and third-party terms

avatartrace is released under the [MIT License](LICENSE).

- **InsightFace** code is MIT-licensed, but its pretrained model packs (`buffalo_l` and others) are published for
  **non-commercial research purposes only**. The `face` extra downloads them on first use; nothing is bundled.
- The CLIP model (`Qdrant/clip-ViT-B-32-vision`, an ONNX export of OpenAI CLIP) is MIT-licensed.
- Test fixtures containing faces are crops of public-domain NASA portraits; see
  [tests/fixtures/README.md](tests/fixtures/README.md). LFW is used for calibration only and is not distributed.
