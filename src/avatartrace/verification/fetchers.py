"""Fetch an account's current profile picture from public, logged-out sources.

Each platform lists its avatar sources in ``platforms.json``; they are tried in
order until one yields a picture:

``api``    an official API that needs a key the user configured (YouTube, Twitch,
           X, Reddit); silently skipped when the key is absent;
``json``   a public, key-less JSON API (GitHub, Bluesky, Mastodon, GitLab, ...);
``html``   the public profile page: og:image, schema.org JSON-LD or a regex;
``image``  a URL that is the picture itself.

Nothing here logs in, solves challenges or retries after being refused. When a
site says no, the outcome is "unavailable" with the reason, and the candidate
is reported as UNVERIFIED.
"""

from __future__ import annotations

import base64
import html as htmllib
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import quote, urljoin

from avatartrace.config import Settings
from avatartrace.extraction.registry import AvatarSource, Platform, PlatformRegistry, render
from avatartrace.models import Account
from avatartrace.net import FetchError, HttpClient
from avatartrace.verification import html as htmlx

_PATH_TOKEN = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


@dataclass(frozen=True, slots=True)
class AvatarResult:
    status: Literal["ok", "not_found", "unavailable"]
    image_url: str | None = None
    data: bytes | None = None
    reason: str | None = None


class _NotFound(Exception):
    """The account does not exist (any more)."""


class _Skip(Exception):
    """This source cannot provide a picture; try the next one."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _describe(exc: FetchError) -> str:
    if exc.kind == "login_wall":
        return "login wall (the profile is only shown to signed-in users)"
    if exc.kind == "blocked":
        return f"blocked for logged-out clients ({exc.reason})"
    if exc.kind == "rate_limited":
        return "rate limited by the site"
    if exc.kind == "timeout":
        return "the site did not respond in time"
    return exc.reason


def json_path(data: Any, path: str) -> Any:
    """Resolve ``a.b[0].c`` inside parsed JSON; ``None`` if any step is missing."""
    current = data
    for key, index in _PATH_TOKEN.findall(path):
        if key:
            if not isinstance(current, dict):
                return None
            current = current.get(key)
        else:
            position = int(index)
            if not isinstance(current, list) or position >= len(current):
                return None
            current = current[position]
        if current is None:
            return None
    return current


class AvatarFetcher:
    def __init__(self, registry: PlatformRegistry, http: HttpClient, settings: Settings) -> None:
        self._registry = registry
        self._http = http
        self._settings = settings
        self._tokens: dict[str, str] = {}
        self._apis: dict[str, Callable[[Account], Awaitable[str | None]]] = {
            "x": self._api_x,
            "youtube": self._api_youtube,
            "twitch": self._api_twitch,
            "reddit": self._api_reddit,
        }

    async def fetch(self, account: Account) -> AvatarResult:
        platform = self._registry.platforms.get(account.platform)
        if platform is None:
            return AvatarResult("unavailable", reason="unknown platform")
        if not platform.avatar:
            return AvatarResult("unavailable", reason=platform.avatar_note or "no public source for the picture")
        reasons: list[str] = []
        for source in platform.avatar:
            try:
                image_url = await self._resolve(source, platform, account)
                if image_url is None:
                    continue
                for old, new in source.replace:
                    image_url = image_url.replace(old, new)
                if any(marker in image_url for marker in source.reject):
                    raise _Skip("only a default or site-wide placeholder image is exposed")
                image = await self._http.get_image(image_url)
            except _NotFound:
                return AvatarResult("not_found", reason="account not found")
            except _Skip as skip:
                reasons.append(skip.reason)
                continue
            except FetchError as exc:
                reasons.append(f"could not download the picture: {_describe(exc)}")
                continue
            return AvatarResult("ok", image_url=image_url, data=image.content)
        reason = reasons[0] if reasons else (platform.avatar_note or "no public source for the picture")
        return AvatarResult("unavailable", reason=reason)

    # ------------------------------------------------------------------ sources

    def _variables(self, account: Account) -> dict[str, str]:
        return {
            "username": quote(account.username, safe="._-~:@"),
            "local": quote(account.local, safe="._-~:@"),
            "instance": account.instance or "",
            "profile_url": account.profile_url,
        }

    async def _resolve(self, source: AvatarSource, platform: Platform, account: Account) -> str | None:
        if source.type == "api":
            handler = self._apis.get(source.name)
            return await handler(account) if handler else None
        if source.type == "image":
            return render(source.url or "", self._variables(account))
        if source.type == "json":
            return await self._from_json(source, account)
        return await self._from_html(source, account)

    async def _from_json(self, source: AvatarSource, account: Account) -> str:
        url = render(source.url or "", self._variables(account))
        headers: dict[str, str] = {}
        if source.auth == "github":
            headers["Accept"] = "application/vnd.github+json"
            token = self._settings.secret("github_token")
            if token:
                headers["Authorization"] = f"Bearer {token}"
        try:
            data = await self._http.get_json(url, headers=headers)
        except FetchError as exc:
            if exc.status in source.missing_status:
                raise _NotFound from exc
            reason = _describe(exc)
            if source.auth == "github" and exc.status in (403, 429):
                reason = "GitHub API rate limit reached (set GITHUB_TOKEN to raise it)"
            raise _Skip(reason) from exc
        variables = {"username": account.local.lower()}
        for alternative in source.path.split("|"):
            value = json_path(data, render(alternative.strip(), variables))
            if isinstance(value, str) and value.strip():
                return urljoin(url, htmllib.unescape(value.strip()))
        raise _Skip("the API response contains no profile picture")

    async def _from_html(self, source: AvatarSource, account: Account) -> str:
        url = render(source.url, self._variables(account)) if source.url else account.profile_url
        try:
            page = await self._http.fetch(url, headers={"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"})
        except FetchError as exc:
            if exc.kind == "not_found":
                raise _NotFound from exc
            raise _Skip(_describe(exc)) from exc
        text = page.text()
        for extractor in source.extract:
            found: str | None
            if extractor == "og_image":
                found = htmlx.og_image(text, page.url)
            elif extractor == "ld_json":
                found = htmlx.ld_json_image(text, page.url)
            else:
                found = htmlx.regex_image(text, source.regex, page.url) if source.regex else None
            if found:
                return found
        raise _Skip("the public page does not expose a profile picture")

    # ------------------------------------------------------------------ keyed official APIs

    async def _api_json(self, url: str, headers: dict[str, str], params: dict[str, str]) -> Any:
        try:
            return await self._http.get_json(url, headers=headers, params=params)
        except FetchError as exc:
            if exc.kind == "not_found":
                raise _NotFound from exc
            raise _Skip(f"official API: {_describe(exc)}") from exc

    async def _api_x(self, account: Account) -> str | None:
        token = self._settings.secret("x_bearer_token")
        if not token:
            return None
        base = "https://api.x.com/2/users"
        url = f"{base}/{quote(account.local)}" if account.is_id else f"{base}/by/username/{quote(account.local)}"
        data = await self._api_json(url, {"Authorization": f"Bearer {token}"}, {"user.fields": "profile_image_url"})
        image = json_path(data, "data.profile_image_url")
        if not isinstance(image, str):
            if json_path(data, "errors[0]") is not None:
                raise _NotFound
            raise _Skip("official API returned no profile picture")
        return image.replace("_normal.", "_400x400.")

    async def _api_youtube(self, account: Account) -> str | None:
        key = self._settings.secret("youtube_api_key")
        if not key:
            return None
        if account.namespace == "channel":
            selector = {"id": account.local}
        elif account.namespace == "user":
            selector = {"forUsername": account.local}
        elif account.namespace == "":
            selector = {"forHandle": f"@{account.local}"}
        else:
            return None  # legacy /c/ names cannot be looked up through the API
        data = await self._api_json(
            "https://www.googleapis.com/youtube/v3/channels",
            {"x-goog-api-key": key},
            {"part": "snippet", **selector},
        )
        if not json_path(data, "items[0]"):
            raise _NotFound
        for size in ("high", "medium", "default"):
            image = json_path(data, f"items[0].snippet.thumbnails.{size}.url")
            if isinstance(image, str):
                return image
        raise _Skip("official API returned no profile picture")

    async def _api_twitch(self, account: Account) -> str | None:
        client_id = self._settings.secret("twitch_client_id")
        secret = self._settings.secret("twitch_client_secret")
        if not client_id or not secret:
            return None
        token = await self._app_token(
            "twitch",
            "https://id.twitch.tv/oauth2/token",
            {"client_id": client_id, "client_secret": secret, "grant_type": "client_credentials"},
            {},
        )
        data = await self._api_json(
            "https://api.twitch.tv/helix/users",
            {"Authorization": f"Bearer {token}", "Client-Id": client_id},
            {"login": account.local},
        )
        if not json_path(data, "data[0]"):
            raise _NotFound
        image = json_path(data, "data[0].profile_image_url")
        if not isinstance(image, str) or not image:
            raise _Skip("official API returned no profile picture")
        return image

    async def _api_reddit(self, account: Account) -> str | None:
        client_id = self._settings.secret("reddit_client_id")
        secret = self._settings.secret("reddit_client_secret")
        if not client_id or not secret:
            return None
        basic = base64.b64encode(f"{client_id}:{secret}".encode()).decode("ascii")
        token = await self._app_token(
            "reddit",
            "https://www.reddit.com/api/v1/access_token",
            {"grant_type": "client_credentials"},
            {"Authorization": f"Basic {basic}"},
        )
        data = await self._api_json(
            f"https://oauth.reddit.com/user/{quote(account.local)}/about",
            {"Authorization": f"Bearer {token}"},
            {"raw_json": "1"},
        )
        for field in ("data.snoovatar_img", "data.icon_img"):
            image = json_path(data, field)
            if isinstance(image, str) and image:
                return htmllib.unescape(image)
        raise _Skip("official API returned no profile picture")

    async def _app_token(self, name: str, url: str, form: dict[str, str], headers: dict[str, str]) -> str:
        """Application-only OAuth token (client credentials), cached for the run."""
        cached = self._tokens.get(name)
        if cached:
            return cached
        try:
            fetched = await self._http.fetch(url, method="POST", data=form, headers=headers)
            token = json_path(fetched.json(), "access_token")
        except FetchError as exc:
            raise _Skip(f"official API login failed: {_describe(exc)}") from exc
        if not isinstance(token, str) or not token:
            raise _Skip("official API login failed: no access token returned")
        self._tokens[name] = token
        return token
