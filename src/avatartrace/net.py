"""A small, polite HTTP layer shared by providers and avatar fetchers.

Design rules (see README, "Ethics & safety"):

* one honest User-Agent, no browser impersonation, no proxy support;
* a minimum delay between requests to the same site, plus a global concurrency cap;
* a 403/429/login redirect is reported as such and never retried or worked around;
* responses are size-capped and private/loopback addresses are refused.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json as jsonlib
import re
import socket
from collections.abc import Mapping
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Literal, Self
from urllib.parse import urlsplit

import httpx

from avatartrace.config import Settings

ErrorKind = Literal[
    "not_found",
    "login_wall",
    "blocked",
    "rate_limited",
    "timeout",
    "network",
    "too_large",
    "invalid",
    "server_error",
    "error",
]

_LOGIN_PATH = re.compile(
    r"/(?:accounts/login|login|signin|sign_in|sign-in|authwall|checkpoint|uas/login|oauth|sso)(?:[/?.]|$)",
    re.IGNORECASE,
)
_LOGIN_HOST = re.compile(r"^(?:consent|accounts|login|auth|signin|id)\.", re.IGNORECASE)


#: Pause before the single retry of a transient failure, in seconds.
RETRY_DELAY = 1.5


def _is_global(address: str) -> bool:
    try:
        return ipaddress.ip_address(address).is_global
    except ValueError:
        return False


def _is_transient(status: int) -> bool:
    """Server-side hiccups worth one more try. Refusals (403, 429, 999...) are never retried."""
    return 500 <= status <= 600


class FetchError(Exception):
    """A request that did not produce a usable response."""

    def __init__(self, reason: str, *, kind: ErrorKind = "error", status: int | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.kind: ErrorKind = kind
        self.status = status


@dataclass(frozen=True, slots=True)
class Fetched:
    status: int
    url: str
    headers: Mapping[str, str]
    content: bytes
    redirected: bool = False

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "").split(";")[0].strip().lower()

    def text(self) -> str:
        match = re.search(r"charset=([\w-]+)", self.headers.get("content-type", ""), re.IGNORECASE)
        encoding = match.group(1) if match else "utf-8"
        try:
            return self.content.decode(encoding, errors="replace")
        except LookupError:
            return self.content.decode("utf-8", errors="replace")

    def json(self) -> Any:
        try:
            return jsonlib.loads(self.content)
        except (ValueError, UnicodeDecodeError) as exc:
            raise FetchError("response was not valid JSON", kind="invalid", status=self.status) from exc


def site_key(host: str) -> str:
    """Rate-limit bucket for a host: its last two labels (``api.github.com`` -> ``github.com``)."""
    host = host.lower().rstrip(".")
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        parts = host.split(".")
        return ".".join(parts[-2:]) if len(parts) >= 2 else host
    return host


def is_public_url(url: str) -> bool:
    """True for http(s) URLs whose host is not obviously local or private."""
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or not host:
        return False
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".lan", ".home.arpa")):
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return "." in host
    return address.is_global


async def resolve_host(host: str) -> list[str]:
    """IP addresses a host name resolves to (through the system resolver)."""
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [str(info[4][0]).split("%")[0] for info in infos]


class HostRateLimiter:
    """Guarantees a minimum interval between requests to the same site."""

    def __init__(self, default_interval: float, overrides: Mapping[str, float] | None = None) -> None:
        self._default = default_interval
        self._overrides = dict(overrides or {})
        self._locks: dict[str, asyncio.Lock] = {}
        self._next_at: dict[str, float] = {}

    def interval_for(self, host: str) -> float:
        key = site_key(host)
        return max(self._default, self._overrides.get(key, 0.0)) if self._default > 0 else 0.0

    async def wait(self, host: str) -> None:
        interval = self.interval_for(host)
        if interval <= 0:
            return
        key = site_key(host)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            loop = asyncio.get_running_loop()
            delay = self._next_at.get(key, 0.0) - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)
            self._next_at[key] = loop.time() + interval


class HttpClient:
    """Async HTTP client with rate limiting, size caps and uniform errors."""

    def __init__(
        self,
        settings: Settings,
        *,
        rate_overrides: Mapping[str, float] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._limiter = HostRateLimiter(settings.per_host_interval, rate_overrides)
        self._semaphore = asyncio.Semaphore(settings.max_concurrency)
        self._client = httpx.AsyncClient(
            headers={
                "User-Agent": settings.user_agent,
                "Accept-Language": "en-US,en;q=0.8",
            },
            timeout=httpx.Timeout(settings.request_timeout),
            follow_redirects=True,
            max_redirects=5,
            transport=transport,
            event_hooks={"request": [self._guard_request]},
        )
        self.request_count = 0
        self._public_hosts: dict[str, bool] = {}

    async def _guard_request(self, request: httpx.Request) -> None:
        """Runs before every request, redirects included: only public addresses are contacted."""
        host = request.url.host
        if not is_public_url(str(request.url)):
            raise FetchError(f"refusing non-public address: {host}", kind="invalid")
        if not self._settings.check_dns:
            return
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            return  # a literal address was already checked above
        if host not in self._public_hosts:
            try:
                addresses = await resolve_host(host)
            except OSError as exc:
                raise FetchError(f"could not resolve {host}", kind="network") from exc
            self._public_hosts[host] = bool(addresses) and all(_is_global(a) for a in addresses)
        if not self._public_hosts[host]:
            raise FetchError(f"refusing {host}: it resolves to a private address", kind="invalid")

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, str | int | float] | None = None,
        json: Any = None,
        data: Mapping[str, str] | None = None,
        files: Mapping[str, tuple[str, bytes, str]] | None = None,
        max_bytes: int | None = None,
        accept_status: frozenset[int] = frozenset(),
    ) -> Fetched:
        """Perform one request. Raises :class:`FetchError` for anything unusable.

        Statuses listed in ``accept_status`` are returned instead of raised, so a
        caller can read an API's JSON error body.
        """
        if not is_public_url(url):
            raise FetchError("not a public http(s) URL", kind="invalid")
        host = urlsplit(url).hostname or ""
        limit = max_bytes if max_bytes is not None else self._settings.max_html_bytes
        idempotent = method.upper() in ("GET", "HEAD")
        attempts = 2 if idempotent else 1
        last_error: FetchError | None = None
        for attempt in range(attempts):
            if attempt:
                await asyncio.sleep(RETRY_DELAY)
            async with self._semaphore:
                await self._limiter.wait(host)
                self.request_count += 1
                try:
                    fetched = await self._send(method, url, headers, params, json, data, files, limit)
                except FetchError as exc:
                    if exc.kind in ("network", "timeout") and attempt + 1 < attempts:
                        last_error = exc
                        continue
                    raise
            if _is_transient(fetched.status) and fetched.status not in accept_status and attempt + 1 < attempts:
                last_error = FetchError(f"HTTP {fetched.status}", kind="server_error", status=fetched.status)
                continue
            if fetched.status in accept_status:
                return fetched
            self._raise_for_status(fetched)
            return fetched
        raise last_error or FetchError("request failed")

    async def _send(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str] | None,
        params: Mapping[str, str | int | float] | None,
        json: Any,
        data: Mapping[str, str] | None,
        files: Mapping[str, tuple[str, bytes, str]] | None,
        limit: int,
    ) -> Fetched:
        request = self._client.build_request(
            method,
            url,
            headers=dict(headers) if headers else None,
            params=dict(params) if params else None,
            json=json,
            data=dict(data) if data else None,
            files=dict(files) if files else None,
        )
        try:
            response = await self._client.send(request, stream=True)
        except httpx.TimeoutException as exc:
            raise FetchError("timed out", kind="timeout") from exc
        except httpx.TooManyRedirects as exc:
            raise FetchError("too many redirects", kind="blocked") from exc
        except httpx.InvalidURL as exc:
            raise FetchError("invalid URL", kind="invalid") from exc
        except httpx.HTTPError as exc:
            raise FetchError(f"network error ({type(exc).__name__})", kind="network") from exc
        try:
            declared = response.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > limit:
                raise FetchError("response too large", kind="too_large", status=response.status_code)
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > limit:
                    raise FetchError("response too large", kind="too_large", status=response.status_code)
                chunks.append(chunk)
        except httpx.TimeoutException as exc:
            raise FetchError("timed out", kind="timeout") from exc
        except httpx.HTTPError as exc:
            raise FetchError(f"network error ({type(exc).__name__})", kind="network") from exc
        finally:
            await response.aclose()
        return Fetched(
            status=response.status_code,
            url=str(response.url),
            headers={k.lower(): v for k, v in response.headers.items()},
            content=b"".join(chunks),
            redirected=bool(response.history),
        )

    @staticmethod
    def _raise_for_status(fetched: Fetched) -> None:
        status = fetched.status
        final = urlsplit(fetched.url)
        if 200 <= status < 300:
            if fetched.redirected and (_LOGIN_PATH.search(final.path) or _LOGIN_HOST.match(final.hostname or "")):
                raise FetchError("redirected to a login page", kind="login_wall", status=status)
            return
        if status in (404, 410):
            raise FetchError(f"HTTP {status}", kind="not_found", status=status)
        if status == 429:
            raise FetchError("rate limited (HTTP 429)", kind="rate_limited", status=status)
        if status in (401, 403, 451, 999):
            raise FetchError(f"blocked (HTTP {status})", kind="blocked", status=status)
        if status >= 500:
            raise FetchError(f"server error (HTTP {status})", kind="server_error", status=status)
        raise FetchError(f"unexpected HTTP {status}", kind="error", status=status)

    async def get_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, str | int | float] | None = None,
        accept_status: frozenset[int] = frozenset(),
    ) -> Any:
        merged = {"Accept": "application/json", **(headers or {})}
        fetched = await self.fetch(url, headers=merged, params=params, accept_status=accept_status)
        return fetched.json()

    async def get_image(self, url: str) -> Fetched:
        """Download an image, enforcing the image size cap."""
        fetched = await self.fetch(
            url,
            headers={"Accept": "image/avif,image/webp,image/png,image/jpeg,image/*;q=0.8"},
            max_bytes=self._settings.max_image_bytes,
        )
        if not fetched.content:
            raise FetchError("empty image response", kind="invalid", status=fetched.status)
        return fetched
