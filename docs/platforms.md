# Platforms

What avatartrace can do with each platform it recognises. The table below is the output of

```bash
python scripts/check_platforms.py --markdown
```

run on **14 November 2026** from a residential connection, logged out, with the default honest `User-Agent`
(`avatartrace/<version> (+repo URL)`). It fetched the profile picture of one well-known public account per
platform, exactly the way a scan does.

**53 of 68 platforms returned a picture.**

Read the result column like this:

- **verified**: the current profile picture was fetched, so a candidate on this platform gets a real comparison
  and a real score.
- **UNVERIFIED**: the platform does not show the picture to a logged-out client that identifies itself honestly.
  Candidates found there by a search provider are still listed, marked `UNVERIFIED (found via <provider>)`, with
  no score. avatartrace does not log in, impersonate a browser, solve challenges or rotate addresses to change
  this.

Results vary by network. Instagram and Facebook answered here, but both commonly redirect data-centre addresses
(CI runners, cloud servers, some VPNs) to their login page, and always do so for private or restricted profiles.
Re-run the script to see what your own connection gets.

`--expand` shows whether the platform is part of username expansion. Platforms that cannot be verified are left
out of expansion on purpose: probing a site that refuses us would only produce noise.

| Platform | Picture source | `--expand` | Logged-out result | Detail |
| --- | --- | --- | --- | --- |
| AllMyLinks | profile page (og_image) | yes | verified | 300x300 px |
| Audiomack | profile page (og_image) | yes | verified | 464x464 px |
| Bitbucket | public JSON API | yes | verified | 128x128 px |
| Bluesky | public JSON API | yes | verified | 1000x1000 px |
| Chess.com | public JSON API | yes | verified | 112x112 px |
| Codeberg | public JSON API | yes | verified | 290x290 px |
| crates.io | public JSON API | yes | verified | 460x460 px |
| Dailymotion | public JSON API | yes | verified | 360x360 px |
| DEV Community | public JSON API | yes | verified | 320x320 px |
| DeviantArt | profile page (og_image) | yes | verified | 100x100 px |
| Docker Hub | public JSON API | yes | verified | 80x80 px |
| Facebook | profile page (og_image) | yes | verified | 720x720 px |
| Farcaster | public JSON API | yes | verified | 256x256 px |
| freeCodeCamp | public JSON API | yes | verified | 460x460 px |
| Genius | profile page (og_image) | yes | verified | 1000x1000 px |
| Gitea | public JSON API | yes | verified | 266x239 px |
| GitHub | public JSON API | yes | verified | 460x460 px |
| GitLab | public JSON API | yes | verified | 200x200 px |
| Gravatar | public JSON API | yes | verified | 80x80 px |
| HackerRank | public JSON API | yes | verified | 150x150 px |
| Hugging Face | public JSON API | yes | verified | 200x200 px |
| Instagram | profile page (og_image) | yes | verified | 150x150 px |
| Instructables | profile page (og_image) | yes | verified | 200x200 px |
| Keybase | public JSON API | yes | verified | 360x360 px |
| Kick | public JSON API | yes | verified | 350x350 px |
| Last.fm | profile page (og_image) | yes | verified | 420x420 px |
| Lemmy | public JSON API | yes | verified | 283x378 px |
| Liberapay | profile page (og_image) | yes | verified | 160x160 px |
| Linktree | profile page (regex) | yes | verified | 800x800 px |
| Lobsters | public JSON API | yes | verified | 100x100 px |
| Mastodon / Fediverse | public JSON API | yes | verified | 400x400 px |
| Minds | public JSON API | yes | verified | 400x400 px |
| Mixcloud | public JSON API | yes | verified | 640x640 px |
| Myspace | profile page (og_image) | yes | verified | 600x600 px |
| note | public JSON API | yes | verified | 330x330 px |
| osu! | profile page (og_image) | yes | verified | 256x256 px |
| Pinterest | profile page (ld_json) | yes | verified | 280x280 px |
| Plurk | profile page (og_image) | yes | verified | 200x200 px |
| Qiita | public JSON API | yes | verified | 160x160 px |
| Scratch | public JSON API | yes | verified | 90x90 px |
| SoundCloud | profile page (og_image) | yes | verified | 500x500 px |
| Spotify | profile page (og_image) | no | verified | 300x300 px |
| Steam | profile page (regex) | yes | verified | 184x184 px |
| Substack | profile page (ld_json) | no | verified | 1179x1179 px |
| Telegram | profile page (og_image) | yes | verified | 320x320 px |
| Threads | profile page (og_image) | yes | verified | 640x640 px |
| Tumblr | public image endpoint | yes | verified | 512x512 px |
| Twitch | twitch API (needs key); profile page (og_image) | yes | verified | 300x300 px |
| Vimeo | profile page (og_image) | yes | verified | 360x360 px |
| WordPress.org | profile page (og_image) | yes | verified | 1024x1024 px |
| X (Twitter) | x API (needs key); profile page (regex) | yes | verified | 400x400 px |
| YouTube | youtube API (needs key); profile page (og_image) | yes | verified | 900x900 px |
| Zenn | public JSON API | yes | verified | 260x260 px |
| Behance | profile page (og_image) | no | UNVERIFIED | This site sits behind bot protection or does not expose the profile picture in page metadata, so results are usually UNVERIFIED. |
| Buy Me a Coffee | profile page (og_image) | no | UNVERIFIED | This site sits behind bot protection or does not expose the profile picture in page metadata, so results are usually UNVERIFIED. |
| CodePen | profile page (og_image) | no | UNVERIFIED | This site sits behind bot protection or does not expose the profile picture in page metadata, so results are usually UNVERIFIED. |
| Dribbble | none | no | UNVERIFIED | Dribbble's og:image is a shot, not the avatar, and the page offers no machine-readable avatar, so results are UNVERIFIED. |
| Flickr | profile page (og_image, ld_json) | no | UNVERIFIED | This site sits behind bot protection or does not expose the profile picture in page metadata, so results are usually UNVERIFIED. |
| Kaggle | profile page (og_image) | no | UNVERIFIED | This site sits behind bot protection or does not expose the profile picture in page metadata, so results are usually UNVERIFIED. |
| Ko-fi | profile page (og_image) | no | UNVERIFIED | This site sits behind bot protection or does not expose the profile picture in page metadata, so results are usually UNVERIFIED. |
| Letterboxd | profile page (og_image) | no | UNVERIFIED | This site sits behind bot protection or does not expose the profile picture in page metadata, so results are usually UNVERIFIED. |
| LinkedIn | profile page (og_image, ld_json) | no | UNVERIFIED | LinkedIn answers logged-out, non-browser clients with HTTP 999 or an auth wall, so profile pictures are not retrievable. |
| Medium | profile page (og_image, ld_json) | no | UNVERIFIED | Medium sits behind Cloudflare bot protection and answers non-browser clients with HTTP 403. |
| Patreon | profile page (og_image) | no | UNVERIFIED | This site sits behind bot protection or does not expose the profile picture in page metadata, so results are usually UNVERIFIED. |
| Reddit | reddit API (needs key); profile page (og_image, ld_json) | no | UNVERIFIED | Reddit blocks logged-out, non-browser clients; set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET (a free 'script' app) to use the official API. |
| Snapchat | profile page (og_image, ld_json) | no | UNVERIFIED | Snapchat returns HTTP 404 for public profiles when the client is not a browser, so the picture is not retrievable. |
| TikTok | profile page (og_image, ld_json) | no | UNVERIFIED | TikTok serves a JavaScript bot challenge to non-browser clients, so the avatar is normally not retrievable. |
| VK | profile page (og_image) | no | UNVERIFIED | VK redirects logged-out, non-browser clients to a generic page without profile data. |

## Platforms with optional official-API keys

| Platform | Variables | Without the key |
| --- | --- | --- |
| GitHub | `GITHUB_TOKEN` | Works, limited to 60 API requests an hour |
| YouTube | `YOUTUBE_API_KEY` | Works through the channel page; a consent interstitial can block it in the EU |
| Twitch | `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET` | Works through the channel page |
| X (Twitter) | `X_BEARER_TOKEN` | Works through an image preload hint in the profile HTML, which X can change at any time |
| Reddit | `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET` | `UNVERIFIED` |

The four keyed integrations (YouTube, Twitch, X, Reddit) follow each API's documentation and are covered by
mocked tests, but were not exercised with real credentials during development.

## Fediverse

Any host whose URL looks like Mastodon (`https://<host>/@<user>`) is tried through the standard public endpoint
`/api/v1/accounts/lookup`. Mastodon, Pixelfed, Pleroma and Akkoma answer it. A host that does not is reported
as an account that was not found, and the candidate is listed as `UNVERIFIED`.

For `--expand`, usernames are tried on these instances: mastodon.social, mastodon.online, mstdn.social, mas.to,
fosstodon.org, hachyderm.io, infosec.exchange, techhub.social, mastodon.world, c.im, and on lemmy.world and
lemmy.ml for Lemmy.

## Special URL forms

- Numeric and opaque IDs (`facebook.com/profile.php?id=`, `youtube.com/channel/UC...`, `vk.com/id123`,
  `steamcommunity.com/profiles/7656...`, Spotify artist IDs) are verified like any other account but are never
  used as a seed for username expansion.
- Posts that name their author (`x.com/<user>/status/<id>`, `tiktok.com/@<user>/video/<id>`,
  `github.com/<user>/<repo>`) are attributed to that author and marked `(post URL)` in "Found via". Set
  `AVATARTRACE_INCLUDE_CONTENT_URLS=false` to accept profile URLs only.
- Pages that do not identify an account (`instagram.com/p/<id>`, hashtags, search, login, `/explore`) are
  dropped.
