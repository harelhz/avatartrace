# Contributing to avatartrace

Thanks for helping. This guide covers the development setup and the two most common contributions: **adding a
platform** and **adding a search provider**.

## Ground rules

avatartrace has a narrow purpose (catfish and scam detection, finding impersonators, checking your own exposure)
and a set of limits that are part of the design. Changes that weaken them will not be merged:

- **Public, logged-out data only.** No logins, cookies, session tokens or private endpoints.
- **No evasion.** No CAPTCHA solving, proxy rotation, browser or crawler impersonation, header spoofing, or
  retrying after a refusal. If a site blocks the honest client, the correct result is `UNVERIFIED`.
- **No scraping of search-engine result pages.** Discovery goes through documented APIs.
- **No bulk mode.** One photo per run. No batch input, no watch lists, no scheduled monitoring of people.
- **Nothing leaves the machine without consent**, and nothing about third parties is kept on disk by default.
- **Never invent a score.** If a picture was not compared, the result has no confidence.

If you are unsure whether an idea fits, open an issue first.

## Setup

```bash
git clone https://github.com/harelhz/avatartrace.git
cd avatartrace
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"            # add ,face and/or ,clip to run the model tests
```

Before you open a pull request:

```bash
ruff check . && ruff format --check .
mypy
pytest
```

CI runs the same commands on Python 3.11 to 3.14, plus the face-model tests and a Docker build.

Tests never touch the network: every HTTP request is intercepted by `respx`, and an unmocked request fails the
test. The only code that uses the real internet is under `scripts/`.

## Project layout

```text
src/avatartrace/
  data/platforms.json    every platform: URL patterns, avatar sources, expansion
  extraction/            URL -> (platform, username); avatar-CDN -> account lookups
  discovery/             search providers (one module each) and the provider interface
  verification/          fetch an account's public profile picture
  compare/               exact, perceptual-hash, face and CLIP comparison, scoring
  pipeline.py            discovery -> extraction -> verification -> expansion
  report/                HTML, JSON and CSV reports
  cli.py, web/           the two interfaces
tests/                   one test module per area; fixtures in tests/fixtures/
scripts/                 calibration and the live platform check (network)
docs/                    thresholds.md, platforms.md
```

## Adding a platform

Platforms are **data, not code**. Add one object to the `platforms` array in
[`src/avatartrace/data/platforms.json`](src/avatartrace/data/platforms.json).

### 1. Describe the platform

```json
{
  "id": "examplesite",
  "name": "Example Site",
  "hosts": "(?:www\\.)?examplesite\\.com",
  "username": "[A-Za-z0-9_]{3,30}",
  "profile_url": "https://examplesite.com/u/{username}",
  "display": "@{username}",
  "reserved": ["login", "search", "settings"],
  "patterns": [
    { "path": "^/u/(?P<username>{U})/?$", "kind": "profile" },
    { "path": "^/u/(?P<username>{U})/posts/\\d+$", "kind": "content" }
  ],
  "avatar": [
    { "type": "json", "url": "https://api.examplesite.com/users/{username}", "path": "avatar_url" }
  ],
  "expand": true,
  "example": "examplesite"
}
```

| Field | Meaning |
| --- | --- |
| `id`, `name` | Stable identifier and display name. |
| `hosts` | Regex matched against the **whole** lower-cased host. List accepted subdomains explicitly. |
| `username` | Regex a valid username must fully match. `{U}` in patterns expands to it. |
| `case_sensitive` | `true` if `Abc` and `abc` are different accounts. Default `false` (usernames are lower-cased). |
| `profile_url`, `display` | Templates. `{username}` is the canonical name. |
| `account` | Canonical-name template for federated platforms, e.g. `"{username}@{instance}"`. |
| `reserved` | First-level path words that are not usernames (`login`, `explore`...). Be generous. |
| `patterns` | Tried in order; the first match wins. See below. |
| `avatar` | Avatar sources, tried in order. See below. May be `[]`. |
| `avatar_note` | One sentence shown when the picture cannot be fetched. Say why, honestly. |
| `expand` | `true` to include the platform in username expansion. Only for platforms that verify. |
| `expand_instances` | For federated platforms: the instances to try during expansion. |
| `example`, `example_instance` | A well-known public account, used by the round-trip test and the live check. |
| `rate_limit` | Minimum seconds between requests to this site, if it needs more than the global default. |

**Patterns.** Each pattern may have `host`, `path` and `query` regexes; all that are present must match.
A non-`skip` pattern must capture `(?P<username>...)`.

| Pattern field | Meaning |
| --- | --- |
| `kind` | `profile` (default), `content` (a post that names its author) or `skip` (reject this URL). |
| `namespace` | Separates identifier spaces that must not be merged, e.g. YouTube `channel` IDs and `@handles`. |
| `id` | `true` for numeric or opaque IDs. They are never used as an expansion seed. |
| `validate` | `false` to skip the platform-wide `username` regex (the pattern's own group is the check). |
| `profile_url`, `display`, `case_sensitive` | Per-pattern overrides. |

**Avatar sources.**

| `type` | Fields | Use when |
| --- | --- | --- |
| `json` | `url`, `path` (`a.b[0].c`, alternatives separated by `\|`), `missing_status` | There is a public, key-less JSON API. Preferred. |
| `html` | `extract`: any of `og_image`, `ld_json`, `regex`; optional `url`, `regex` | The profile page exposes the picture in metadata. |
| `image` | `url` | A URL returns the picture itself. |
| `api` | `name` | An official API that needs a user-supplied key. Requires a handler in `verification/fetchers.py`. |

Any source may add `reject` (substrings that mark a default or site-wide placeholder image) and `replace`
(`[["_mini.", "_400x400."]]` to request a larger size).

> **Check that `og:image` really is the avatar.** On many sites it is a banner, a recent post or a generated
> share card. Comparing against the wrong picture silently turns real matches into `NOT A MATCH`. If the page
> offers no reliable avatar, use `"avatar": []` with an `avatar_note`; `UNVERIFIED` is the honest answer.

### 2. Add tests

In [`tests/test_extraction.py`](tests/test_extraction.py), add rows to `MATCHES` (URL, platform, username, kind)
and to `NOT_PROFILES` (URLs that must be rejected: posts, search, login, reserved words). Include the tricky ones:
mobile subdomains, trailing slashes, upper case, query strings. The round-trip and reserved-word tests cover your
platform automatically once it has an `example`.

If you added an `api` handler or an unusual source, add a mocked test to
[`tests/test_fetchers.py`](tests/test_fetchers.py).

### 3. Check it against the real site

```bash
avatartrace parse "https://examplesite.com/u/someone"
python scripts/check_platforms.py examplesite
```

Then refresh the table in [docs/platforms.md](docs/platforms.md) with
`python scripts/check_platforms.py --markdown`.

### Mirrors, redirect wrappers and avatar CDNs

The same file has three smaller lists:

- `mirrors`: third-party sites whose URLs name an account on a real platform (`picuki.com/profile/<user>` →
  Instagram). The mirror is never fetched; only the URL is read.
- `redirectors`: link wrappers to unwrap (`l.facebook.com/l.php?u=...`).
- `cdn`: avatar-CDN URL shapes. Either the username is in the URL (`platform`), or an ID needs a public lookup
  (`resolver`, implemented in `extraction/cdn.py`).

## Adding a search provider

A provider turns the photo into candidate URLs. Before writing any code:

1. **Read the provider's current documentation** and confirm that the API exists, how it authenticates, and what
   it returns. Do not work from memory or old blog posts; several reverse-image APIs have been retired.
2. Confirm that it is a documented API offered for this kind of use. Do not add scrapers of search result pages
   or services that work by evading someone else's protections.

Then:

1. Create `src/avatartrace/discovery/<name>.py` with a subclass of `Provider`:

   ```python
   class ExampleProvider(Provider):
       name = "example"  # the --providers name
       label = "Example Reverse Search"
       env_keys = ("EXAMPLE_API_KEY",)
       sends = "image"  # "image": uploads the photo; "url": needs a public URL
       destination = "Example Inc. (api.example.com)"
       pricing = "paid; ..."  # state only what you verified
       kind = "image"  # "face" if it matches faces rather than pictures

       @classmethod
       def from_settings(cls, settings: Settings) -> Self | None:
           key = settings.secret("example_api_key")
           return cls(key) if key else None

       async def search(self, query: QueryImage, http: HttpClient) -> list[Hit]:
           status, data = await request_json(http, ENDPOINT, method="POST", headers=..., files=...)
           ...
           return [Hit(url=..., provider=self.name, kind="page", match="full")]
   ```

   - Upload `query.upload_jpeg` (re-encoded, EXIF-free), never the original file.
   - Raise `ProviderError` with the API's own message on failure. Never put the key in a message.
   - Put the key in a header when the API allows it, so it stays out of URLs.
   - Parse defensively with `as_dict`, `as_list` and `as_url`: a malformed response must not crash a scan.
   - `sends` and `destination` feed the consent notice, so they must be accurate.

2. Add the key to `Settings` in `config.py` with `_key("EXAMPLE_API_KEY")`, and to `.env.example`.
3. Register the class in `PROVIDERS` in `discovery/__init__.py`.
4. Add tests to `tests/test_providers.py` using responses that follow the documented schema: the request that is
   sent (endpoint, auth, body), the parsed hits, empty results, and each documented error.
5. Document the provider in the README table: what it receives, what it costs, what it finds.

## Changing thresholds or comparison logic

Thresholds are evidence-based. If you change a default or the scoring, re-run
`python scripts/calibrate.py hashes|faces <lfw-dir>` and update [docs/thresholds.md](docs/thresholds.md) with
the new numbers. Fixture expectations live in `tests/test_compare.py` and `tests/test_models_ml.py`.

## Test fixtures

Fixture images containing faces must be public domain or synthetic, with their source recorded in
[tests/fixtures/README.md](tests/fixtures/README.md). Do not commit pictures of private individuals, scraped
profile pictures, or anything from a research dataset.

## Reporting misuse or security problems

See [SECURITY.md](SECURITY.md).
