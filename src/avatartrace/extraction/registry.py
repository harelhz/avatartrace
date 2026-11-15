"""Data-driven URL -> (platform, username) parsing.

All knowledge about platforms lives in ``data/platforms.json``; this module only
interprets it. See CONTRIBUTING.md for the file format.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlsplit

from avatartrace.models import Account, AccountKind

_WAYBACK = re.compile(r"^https?://(?:web\.)?archive\.org/web/\d{4,14}(?:[a-z]{2}_)?/(?P<url>.+)$", re.IGNORECASE)
_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")
_TEMPLATE_VAR = re.compile(r"\{(\w+)\}")
_HOSTISH = re.compile(r"^[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?:[:/?#]|$)")
_MAX_URL_LENGTH = 4096
_MAX_UNWRAP = 3


class PlatformDataError(ValueError):
    """``platforms.json`` is malformed."""


def _compile(pattern: str, where: str) -> re.Pattern[str]:
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise PlatformDataError(f"{where}: invalid regex {pattern!r}: {exc}") from exc


def render(template: str, variables: dict[str, str]) -> str:
    """Fill ``{name}`` placeholders; unknown names are left untouched."""
    return _TEMPLATE_VAR.sub(lambda m: variables.get(m.group(1), m.group(0)), template)


def _quote(value: str) -> str:
    return quote(value, safe="._-~:@")


@dataclass(frozen=True, slots=True)
class PatternSpec:
    host: re.Pattern[str] | None
    path: re.Pattern[str] | None
    query: tuple[tuple[str, re.Pattern[str]], ...]
    kind: str
    namespace: str
    profile_url: str | None
    display: str | None
    is_id: bool
    validate: bool
    case_sensitive: bool | None


@dataclass(frozen=True, slots=True)
class AvatarSource:
    """One way of obtaining a platform's profile picture."""

    type: str
    name: str = ""
    url: str | None = None
    extract: tuple[str, ...] = ()
    regex: re.Pattern[str] | None = None
    reject: tuple[str, ...] = ()
    replace: tuple[tuple[str, str], ...] = ()
    path: str = ""
    missing_status: frozenset[int] = frozenset({404, 410})
    auth: str | None = None


@dataclass(frozen=True, slots=True)
class Platform:
    id: str
    name: str
    hosts: re.Pattern[str]
    username: re.Pattern[str]
    profile_url: str
    display: str
    account: str
    case_sensitive: bool
    reserved: frozenset[str]
    patterns: tuple[PatternSpec, ...]
    avatar: tuple[AvatarSource, ...]
    avatar_note: str | None
    expand: bool
    expand_instances: tuple[str, ...]
    expand_username: str | None
    expand_username_regex: re.Pattern[str] | None
    seed_regex: re.Pattern[str] | None
    generic: bool
    example: str | None
    example_instance: str | None
    rate_limit: float | None

    def pattern_for(self, namespace: str) -> PatternSpec | None:
        for pattern in self.patterns:
            if pattern.namespace == namespace and pattern.kind != "skip":
                return pattern
        return None


@dataclass(frozen=True, slots=True)
class Redirector:
    host: re.Pattern[str]
    path: re.Pattern[str] | None
    param: str | None
    wayback: bool


@dataclass(frozen=True, slots=True)
class Mirror:
    host: re.Pattern[str]
    path: re.Pattern[str]
    platform: str
    namespace: str
    kind: str


@dataclass(frozen=True, slots=True)
class CdnRule:
    host: re.Pattern[str]
    path: re.Pattern[str]
    platform: str | None
    resolver: str | None
    instance: str | None


@dataclass(frozen=True, slots=True)
class CdnMatch:
    """An avatar-CDN URL that identifies an account, directly or via a lookup."""

    account: Account | None = None
    resolver: str | None = None
    identifier: str | None = None
    instance: str | None = None
    host: str = ""


@dataclass(frozen=True, slots=True)
class _Url:
    host: str
    path: str
    query: dict[str, str] = field(default_factory=dict)


class PlatformRegistry:
    """Parsed ``platforms.json`` plus the matching logic."""

    def __init__(self, document: dict[str, Any]) -> None:
        if document.get("schema") != 1:
            raise PlatformDataError("unsupported platforms.json schema")
        self.platforms: dict[str, Platform] = {}
        for raw in document.get("platforms", []):
            platform = self._load_platform(raw)
            if platform.id in self.platforms:
                raise PlatformDataError(f"duplicate platform id {platform.id!r}")
            self.platforms[platform.id] = platform
        self.redirectors = tuple(self._load_redirector(r) for r in document.get("redirectors", []))
        self.mirrors = tuple(self._load_mirror(m) for m in document.get("mirrors", []))
        self.cdn = tuple(self._load_cdn(c) for c in document.get("cdn", []))

    # ------------------------------------------------------------------ loading

    @classmethod
    def from_json(cls, text: str) -> PlatformRegistry:
        try:
            document = json.loads(text)
        except ValueError as exc:
            raise PlatformDataError(f"platforms.json is not valid JSON: {exc}") from exc
        if not isinstance(document, dict):
            raise PlatformDataError("platforms.json must contain a JSON object")
        return cls(document)

    def _load_platform(self, raw: dict[str, Any]) -> Platform:
        try:
            pid = str(raw["id"])
            username_regex = str(raw["username"])
            where = f"platform {pid!r}"
            patterns = tuple(
                self._load_pattern(p, username_regex, f"{where} pattern {i}") for i, p in enumerate(raw["patterns"])
            )
            expand_regex = raw.get("expand_username_regex")
            seed_regex = raw.get("seed_regex")
            return Platform(
                id=pid,
                name=str(raw["name"]),
                hosts=_compile(str(raw["hosts"]), where),
                username=_compile(username_regex, where),
                profile_url=str(raw["profile_url"]),
                display=str(raw.get("display", "{username}")),
                account=str(raw.get("account", "{username}")),
                case_sensitive=bool(raw.get("case_sensitive", False)),
                reserved=frozenset(str(r).lower() for r in raw.get("reserved", [])),
                patterns=patterns,
                avatar=tuple(self._load_source(s, f"{where} avatar {i}") for i, s in enumerate(raw["avatar"])),
                avatar_note=raw.get("avatar_note"),
                expand=bool(raw.get("expand", False)),
                expand_instances=tuple(str(i).lower() for i in raw.get("expand_instances", [])),
                expand_username=raw.get("expand_username"),
                expand_username_regex=_compile(str(expand_regex), where) if expand_regex else None,
                seed_regex=_compile(str(seed_regex), where) if seed_regex else None,
                generic=bool(raw.get("generic", False)),
                example=raw.get("example"),
                example_instance=raw.get("example_instance"),
                rate_limit=float(raw["rate_limit"]) if "rate_limit" in raw else None,
            )
        except KeyError as exc:
            raise PlatformDataError(f"platform {raw.get('id', '?')!r}: missing field {exc}") from exc

    @staticmethod
    def _load_pattern(raw: dict[str, Any], username_regex: str, where: str) -> PatternSpec:
        def expand(pattern: str) -> re.Pattern[str]:
            return _compile(pattern.replace("{U}", f"(?:{username_regex})"), where)

        kind = str(raw.get("kind", "profile"))
        if kind not in ("profile", "content", "skip"):
            raise PlatformDataError(f"{where}: unknown kind {kind!r}")
        host = expand(str(raw["host"])) if "host" in raw else None
        path = expand(str(raw["path"])) if "path" in raw else None
        query = tuple((str(k), expand(str(v))) for k, v in raw.get("query", {}).items())
        if kind != "skip":
            groups: set[str] = set()
            for pattern in (host, path, *(q for _, q in query)):
                if pattern is not None:
                    groups.update(pattern.groupindex)
            if "username" not in groups:
                raise PlatformDataError(f"{where}: no (?P<username>...) group")
        return PatternSpec(
            host=host,
            path=path,
            query=query,
            kind=kind,
            namespace=str(raw.get("namespace", "")),
            profile_url=raw.get("profile_url"),
            display=raw.get("display"),
            is_id=bool(raw.get("id", False)),
            validate=bool(raw.get("validate", True)),
            case_sensitive=raw.get("case_sensitive"),
        )

    @staticmethod
    def _load_source(raw: dict[str, Any], where: str) -> AvatarSource:
        kind = str(raw.get("type", ""))
        if kind not in ("html", "json", "image", "api"):
            raise PlatformDataError(f"{where}: unknown avatar source type {kind!r}")
        extract = tuple(str(e) for e in raw.get("extract", []))
        for name in extract:
            if name not in ("og_image", "ld_json", "regex"):
                raise PlatformDataError(f"{where}: unknown extractor {name!r}")
        if "regex" in extract and "regex" not in raw:
            raise PlatformDataError(f"{where}: extractor 'regex' needs a regex")
        if kind == "json" and not (raw.get("url") and raw.get("path")):
            raise PlatformDataError(f"{where}: json source needs url and path")
        if kind == "image" and not raw.get("url"):
            raise PlatformDataError(f"{where}: image source needs url")
        if kind == "api" and not raw.get("name"):
            raise PlatformDataError(f"{where}: api source needs name")
        return AvatarSource(
            type=kind,
            name=str(raw.get("name", "")),
            url=raw.get("url"),
            extract=extract,
            regex=_compile(str(raw["regex"]), where) if "regex" in raw else None,
            reject=tuple(str(r) for r in raw.get("reject", [])),
            replace=tuple((str(a), str(b)) for a, b in raw.get("replace", [])),
            path=str(raw.get("path", "")),
            missing_status=frozenset(int(s) for s in raw.get("missing_status", [404, 410])),
            auth=raw.get("auth"),
        )

    @staticmethod
    def _load_redirector(raw: dict[str, Any]) -> Redirector:
        where = f"redirector {raw.get('host')!r}"
        return Redirector(
            host=_compile(str(raw["host"]), where),
            path=_compile(str(raw["path"]), where) if "path" in raw else None,
            param=raw.get("param"),
            wayback=bool(raw.get("wayback", False)),
        )

    def _load_mirror(self, raw: dict[str, Any]) -> Mirror:
        where = f"mirror {raw.get('host')!r}"
        platform = str(raw["platform"])
        if platform not in self.platforms:
            raise PlatformDataError(f"{where}: unknown platform {platform!r}")
        path = _compile(str(raw["path"]), where)
        if "username" not in path.groupindex:
            raise PlatformDataError(f"{where}: no (?P<username>...) group")
        return Mirror(
            host=_compile(str(raw["host"]), where),
            path=path,
            platform=platform,
            namespace=str(raw.get("namespace", "")),
            kind=str(raw.get("kind", "profile")),
        )

    def _load_cdn(self, raw: dict[str, Any]) -> CdnRule:
        where = f"cdn {raw.get('host')!r}"
        platform = raw.get("platform")
        resolver = raw.get("resolver")
        if bool(platform) == bool(resolver):
            raise PlatformDataError(f"{where}: exactly one of platform/resolver is required")
        if platform and platform not in self.platforms:
            raise PlatformDataError(f"{where}: unknown platform {platform!r}")
        path = _compile(str(raw["path"]), where)
        needed = "username" if platform else "id"
        if needed not in path.groupindex:
            raise PlatformDataError(f"{where}: no (?P<{needed}>...) group")
        return CdnRule(
            host=_compile(str(raw["host"]), where),
            path=path,
            platform=platform,
            resolver=resolver,
            instance=raw.get("instance"),
        )

    # ------------------------------------------------------------------ URL preparation

    def _prepare(self, url: str) -> _Url | None:
        """Normalise a URL and peel off redirect wrappers and archive copies."""
        current = url.strip()
        for _ in range(_MAX_UNWRAP + 1):
            if not current or len(current) > _MAX_URL_LENGTH:
                return None
            if current.startswith("//"):
                current = "https:" + current
            elif not _SCHEME.match(current):
                if not _HOSTISH.match(current):
                    return None
                current = "https://" + current
            wayback = _WAYBACK.match(current)
            if wayback:
                inner = wayback.group("url")
                # archive.org collapses "https://" to "https:/" in some links
                current = re.sub(r"^(https?):/(?!/)", r"\1://", inner)
                continue
            try:
                parts = urlsplit(current)
                host = (parts.hostname or "").lower().rstrip(".")
            except ValueError:
                return None
            if parts.scheme.lower() not in ("http", "https") or not host:
                return None
            query = {k: v[0] for k, v in parse_qs(parts.query).items() if v}
            path = parts.path or "/"
            target = self._unwrap(host, path, query)
            if target is not None:
                current = target
                continue
            if parts.fragment.startswith("!/") and path in ("", "/"):
                path = parts.fragment[1:]  # legacy "twitter.com/#!/user" links
            path = re.sub(r"/{2,}", "/", unquote(path))
            return _Url(host=host, path=path, query=query)
        return None

    def _unwrap(self, host: str, path: str, query: dict[str, str]) -> str | None:
        for redirector in self.redirectors:
            if redirector.wayback or redirector.param is None:
                continue
            if not redirector.host.fullmatch(host):
                continue
            if redirector.path is not None and not redirector.path.match(path):
                continue
            target = query.get(redirector.param)
            if target:
                return target
        return None

    # ------------------------------------------------------------------ extraction

    def extract(self, url: str, *, include_content: bool = True) -> Account | None:
        """Return the account a URL points at, or ``None`` if it is not a profile.

        ``include_content=False`` keeps only URLs that are profile pages; with the
        default, posts that name their author (``x.com/<user>/status/1``) are
        attributed to that author with ``kind="content"``.
        """
        prepared = self._prepare(url)
        if prepared is None:
            return None
        account = self._match(prepared)
        if account is None or (account.kind == "content" and not include_content):
            return None
        return account

    def _match(self, url: _Url) -> Account | None:
        for platform in self.platforms.values():
            if platform.hosts.fullmatch(url.host):
                return self._match_platform(platform, url)
        for mirror in self.mirrors:
            if mirror.host.fullmatch(url.host):
                found = mirror.path.match(url.path)
                if found:
                    account = self.build(
                        mirror.platform,
                        found.group("username"),
                        namespace=mirror.namespace,
                        kind="content" if mirror.kind == "content" else "profile",
                    )
                    if account is not None:
                        return account
        if "." in url.host and not re.fullmatch(r"[\d.]+", url.host):
            for platform in self.platforms.values():
                if platform.generic:
                    account = self._match_platform(platform, url)
                    if account is not None:
                        return account
        return None

    def _match_platform(self, platform: Platform, url: _Url) -> Account | None:
        for pattern in platform.patterns:
            groups: dict[str, str] = {}
            if pattern.host is not None:
                found = pattern.host.search(url.host)
                if not found:
                    continue
                groups.update({k: v for k, v in found.groupdict().items() if v})
            if pattern.path is not None:
                found = pattern.path.match(url.path)
                if not found:
                    continue
                groups.update({k: v for k, v in found.groupdict().items() if v})
            matched = True
            for name, regex in pattern.query:
                value = url.query.get(name)
                found = regex.fullmatch(value) if value is not None else None
                if not found:
                    matched = False
                    break
                groups.update({k: v for k, v in found.groupdict().items() if v})
            if not matched:
                continue
            if pattern.kind == "skip":
                return None
            username = groups.get("username")
            if not username:
                continue
            instance = groups.get("instance") or _strip_www(url.host)
            account = self._account(platform, pattern, username, instance)
            if account is not None:
                return account
        return None

    def _account(
        self,
        platform: Platform,
        pattern: PatternSpec | None,
        username: str,
        instance: str | None,
        kind: str | None = None,
    ) -> Account | None:
        local = username.strip().lstrip("@").strip()
        if not local or len(local) > 200:
            return None
        case_sensitive = platform.case_sensitive
        if pattern is not None and pattern.case_sensitive is not None:
            case_sensitive = pattern.case_sensitive
        if not case_sensitive:
            local = local.lower()
        validate = pattern.validate if pattern is not None else True
        is_id = pattern.is_id if pattern is not None else False
        if validate and not platform.username.fullmatch(local):
            return None
        if not is_id and local.lower() in platform.reserved:
            return None
        instance = (instance or "").lower()
        canonical = render(platform.account, {"username": local, "instance": instance})
        variables = {"username": canonical, "local": local, "instance": instance}
        url_variables = {k: _quote(v) for k, v in variables.items()}
        display_template = (pattern.display if pattern is not None else None) or platform.display
        url_template = (pattern.profile_url if pattern is not None else None) or platform.profile_url
        resolved_kind: AccountKind = (
            "content" if (kind or (pattern.kind if pattern else "profile")) == "content" else "profile"
        )
        return Account(
            platform=platform.id,
            platform_name=platform.name,
            username=canonical,
            local=local,
            profile_url=render(url_template, url_variables),
            display=render(display_template, variables),
            kind=resolved_kind,
            namespace=pattern.namespace if pattern is not None else "",
            instance=instance or None,
            is_id=is_id,
            case_sensitive=case_sensitive,
        )

    def build(
        self,
        platform_id: str,
        username: str,
        *,
        namespace: str = "",
        instance: str | None = None,
        kind: AccountKind = "profile",
    ) -> Account | None:
        """Build an account from a known username (used by mirrors, CDNs and expansion)."""
        platform = self.platforms.get(platform_id)
        if platform is None:
            return None
        pattern = platform.pattern_for(namespace)
        if pattern is None:
            return None
        if instance is None and platform.account != "{username}":
            return None
        return self._account(platform, pattern, username, instance, kind=kind)

    # ------------------------------------------------------------------ avatar CDNs

    def match_cdn(self, url: str) -> CdnMatch | None:
        """Recognise avatar-CDN URLs that can be traced back to an account."""
        try:
            parts = urlsplit(url.strip())
            host = (parts.hostname or "").lower().rstrip(".")
        except ValueError:
            return None
        if parts.scheme.lower() not in ("http", "https") or not host:
            return None
        path = unquote(parts.path)
        for rule in self.cdn:
            if not rule.host.fullmatch(host):
                continue
            found = rule.path.match(path)
            if not found:
                continue
            if rule.platform is not None:
                account = self.build(rule.platform, found.group("username"))
                if account is not None:
                    return CdnMatch(account=account, host=host)
                continue
            return CdnMatch(
                resolver=rule.resolver,
                identifier=found.group("id"),
                instance=rule.instance,
                host=host,
            )
        return None

    # ------------------------------------------------------------------ expansion

    def expansion_seed(self, account: Account) -> str | None:
        """The plain username worth trying on other sites, or ``None``."""
        if account.is_id or account.namespace:
            return None
        platform = self.platforms.get(account.platform)
        if platform is None:
            return None
        seed = account.local
        if platform.seed_regex is not None:
            found = platform.seed_regex.match(seed)
            if not found:
                return None
            seed = found.group("seed")
        if len(seed) < 3 or seed.isdigit():
            return None
        return seed

    def expansion_accounts(self, seed: str) -> Iterator[Account]:
        """Accounts with the same username on every expansion-enabled site."""
        for platform in self.platforms.values():
            if not platform.expand:
                continue
            if platform.expand_username_regex is not None and not platform.expand_username_regex.fullmatch(seed):
                continue
            local = render(platform.expand_username, {"username": seed}) if platform.expand_username else seed
            instances: tuple[str | None, ...] = platform.expand_instances or (None,)
            for instance in instances:
                account = self.build(platform.id, local, instance=instance)
                if account is not None:
                    yield account

    def expansion_site_count(self) -> int:
        return sum(max(1, len(p.expand_instances)) for p in self.platforms.values() if p.expand)

    def rate_overrides(self) -> dict[str, float]:
        """Per-site minimum request intervals declared in the data file."""
        overrides: dict[str, float] = {}
        for platform in self.platforms.values():
            if platform.rate_limit is None:
                continue
            host = urlsplit(platform.profile_url).hostname
            if host:
                overrides[".".join(host.split(".")[-2:])] = platform.rate_limit
        return overrides


def _strip_www(host: str) -> str:
    return host[4:] if host.startswith("www.") else host


@lru_cache(maxsize=1)
def default_registry() -> PlatformRegistry:
    """The registry built from the bundled ``platforms.json``."""
    text = resources.files("avatartrace").joinpath("data/platforms.json").read_text(encoding="utf-8")
    return PlatformRegistry.from_json(text)
