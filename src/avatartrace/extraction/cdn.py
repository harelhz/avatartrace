"""Map avatar-CDN URLs back to accounts when the platform offers a public lookup.

Example: ``avatars.githubusercontent.com/u/583231`` carries only a numeric user
id; the GitHub API turns it into the login ``octocat``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

from avatartrace.config import Settings
from avatartrace.extraction.registry import CdnMatch, PlatformRegistry
from avatartrace.models import Account
from avatartrace.net import FetchError, HttpClient


class CdnResolver:
    def __init__(self, registry: PlatformRegistry, http: HttpClient, settings: Settings) -> None:
        self._registry = registry
        self._http = http
        self._settings = settings
        self._cache: dict[tuple[str, str, str], Account | None] = {}
        self._resolvers: dict[str, Callable[[CdnMatch], Awaitable[Account | None]]] = {
            "github_user_id": self._github_user_id,
            "mastodon_account_id": self._mastodon_account_id,
            "gravatar_hash": self._gravatar_hash,
        }

    async def resolve(self, url: str) -> Account | None:
        """The account an avatar-CDN URL belongs to, or ``None``."""
        match = self._registry.match_cdn(url)
        if match is None:
            return None
        if match.account is not None:
            return match.account
        resolver = self._resolvers.get(match.resolver or "")
        if resolver is None or not match.identifier:
            return None
        # Only fediverse lookups depend on which host served the picture.
        scope = match.instance or (match.host if match.resolver == "mastodon_account_id" else "")
        key = (match.resolver or "", match.identifier, scope)
        if key not in self._cache:
            try:
                self._cache[key] = await resolver(match)
            except FetchError:
                self._cache[key] = None
        return self._cache[key]

    async def _github_user_id(self, match: CdnMatch) -> Account | None:
        headers = {"Accept": "application/vnd.github+json"}
        token = self._settings.secret("github_token")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        data = await self._http.get_json(f"https://api.github.com/user/{match.identifier}", headers=headers)
        login = data.get("login") if isinstance(data, dict) else None
        return self._registry.build("github", login) if isinstance(login, str) else None

    async def _mastodon_account_id(self, match: CdnMatch) -> Account | None:
        instance = (match.instance or match.host).lower()
        digits = (match.identifier or "").replace("/", "").lstrip("0")
        if not instance or not digits.isdigit():
            return None
        data = await self._http.get_json(f"https://{instance}/api/v1/accounts/{digits}")
        if not isinstance(data, dict):
            return None
        username = data.get("username")
        profile = data.get("url")
        if not isinstance(username, str):
            return None
        home = urlsplit(profile).hostname if isinstance(profile, str) else None
        return self._registry.build("mastodon", username, instance=home or instance)

    async def _gravatar_hash(self, match: CdnMatch) -> Account | None:
        data = await self._http.get_json(f"https://api.gravatar.com/v3/profiles/{match.identifier}")
        profile = data.get("profile_url") if isinstance(data, dict) else None
        if not isinstance(profile, str):
            return None
        slug = urlsplit(profile).path.strip("/")
        return self._registry.build("gravatar", slug) if slug else None
