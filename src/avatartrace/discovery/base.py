"""The provider interface: photo in, candidate URLs out."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar, Literal, Self

from PIL import Image

from avatartrace.config import Settings
from avatartrace.models import Hit
from avatartrace.net import FetchError, HttpClient


class ProviderError(Exception):
    """A provider could not complete its search. The message is safe to show."""


@dataclass(frozen=True, slots=True)
class QueryImage:
    """The photo being traced, in the forms providers need."""

    source: str
    image: Image.Image
    upload_jpeg: bytes
    public_url: str | None = None


class Provider(ABC):
    """A reverse-image or face-search service.

    Subclasses declare what leaves the machine (``sends``) so the CLI can show
    an accurate consent notice before anything is uploaded.
    """

    name: ClassVar[str]
    label: ClassVar[str]
    env_keys: ClassVar[tuple[str, ...]]
    sends: ClassVar[Literal["image", "url"]]
    destination: ClassVar[str]
    pricing: ClassVar[str]
    kind: ClassVar[Literal["image", "face"]] = "image"

    @classmethod
    @abstractmethod
    def from_settings(cls, settings: Settings) -> Self | None:
        """Return a configured provider, or ``None`` when its key is not set."""

    @abstractmethod
    async def search(self, query: QueryImage, http: HttpClient) -> list[Hit]:
        """Run the search. Raise :class:`ProviderError` on any failure."""

    def unavailable_reason(self, query: QueryImage) -> str | None:
        """Why this provider cannot handle this query, or ``None`` if it can."""
        if self.sends == "url" and not query.public_url:
            return (
                "needs a public URL of the photo (pass the photo as a URL, or add --image-url); "
                "avatartrace never uploads your photo to a file host on its own"
            )
        return None

    def disclosure(self) -> str:
        what = "the photo itself" if self.sends == "image" else "the photo's public URL"
        return f"{self.label}: sends {what} to {self.destination}"


async def request_json(
    http: HttpClient,
    url: str,
    *,
    method: str = "GET",
    error_status: frozenset[int] = frozenset({400, 401, 402, 403, 404, 422, 429}),
    **kwargs: Any,
) -> tuple[int, Any]:
    """Call a provider API and return ``(status, parsed JSON)``.

    API error statuses are returned rather than raised so the caller can read the
    provider's own error message. Transport failures become ProviderError.
    """
    try:
        fetched = await http.fetch(url, method=method, accept_status=error_status, **kwargs)
    except FetchError as exc:
        raise ProviderError(exc.reason) from exc
    try:
        return fetched.status, fetched.json()
    except FetchError as exc:
        if fetched.status >= 400:
            raise ProviderError(f"HTTP {fetched.status}") from exc
        raise ProviderError("the API returned a response that is not JSON") from exc


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def as_url(value: Any) -> str | None:
    """Return ``value`` if it is an http(s) URL string."""
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return value
    return None
