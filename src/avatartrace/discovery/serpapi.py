"""SerpApi engines: Google Lens, Google Reverse Image, Yandex Images, Bing Reverse Image.

Docs: https://serpapi.com/google-lens-api, /google-reverse-image,
/yandex-reverse-image-api, /bing-reverse-image-api. All four are
``GET https://serpapi.com/search`` with ``engine``, ``api_key`` and the image
URL. They accept a public image URL only, never an upload, so they are skipped
for local files unless the user supplies ``--image-url``.
"""

from __future__ import annotations

from typing import Any, ClassVar, Self

from avatartrace.config import Settings
from avatartrace.discovery.base import (
    Provider,
    ProviderError,
    QueryImage,
    as_dict,
    as_list,
    as_url,
    request_json,
)
from avatartrace.models import Hit, HitMatch
from avatartrace.net import HttpClient

ENDPOINT = "https://serpapi.com/search"
_NO_RESULTS = ("hasn't returned any results", "has not returned any results", "no results")


class SerpApiProvider(Provider):
    """Shared implementation; each subclass is one SerpApi engine."""

    env_keys = ("SERPAPI_API_KEY",)
    sends = "url"
    pricing = "free plan with a monthly search allowance, paid plans beyond it; each request is one search"

    engine: ClassVar[str]
    url_param: ClassVar[str]
    #: (response key, how strong a match the entries under that key are)
    result_keys: ClassVar[tuple[tuple[str, HitMatch], ...]]
    #: extra query parameters; each tuple is one request (one billed search)
    variants: ClassVar[tuple[tuple[tuple[str, str], ...], ...]] = ((),)

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    @classmethod
    def from_settings(cls, settings: Settings) -> Self | None:
        key = settings.secret("serpapi_api_key")
        if not key or cls.engine not in settings.serpapi_engines:
            return None
        return cls(key)

    async def search(self, query: QueryImage, http: HttpClient) -> list[Hit]:
        if not query.public_url:
            raise ProviderError("needs a public URL of the photo")
        hits: list[Hit] = []
        seen: set[tuple[str, str]] = set()
        errors: list[str] = []
        for extra in self.variants:
            params: dict[str, str | int | float] = {
                "engine": self.engine,
                self.url_param: query.public_url,
                "api_key": self._api_key,
                **dict(extra),
            }
            status, data = await request_json(http, ENDPOINT, params=params)
            payload = as_dict(data)
            error = payload.get("error")
            if error:
                if any(marker in str(error).lower() for marker in _NO_RESULTS):
                    continue
                errors.append(str(error))
                continue
            if status >= 400:
                errors.append(f"HTTP {status}")
                continue
            for hit in self._parse(payload):
                key = (hit.kind, hit.url)
                if key not in seen:
                    seen.add(key)
                    hits.append(hit)
        if errors and not hits:
            raise ProviderError(errors[0])
        return hits

    def _parse(self, payload: dict[str, Any]) -> list[Hit]:
        hits: list[Hit] = []
        for key, match in self.result_keys:
            for item in as_list(payload.get(key)):
                item = as_dict(item)
                image_url = _image_url(item)
                title = item.get("title")
                page = as_url(item.get("link"))
                if page:
                    hits.append(
                        Hit(
                            url=page,
                            provider=self.name,
                            kind="page",
                            match=match,
                            title=title if isinstance(title, str) else None,
                            image_url=image_url,
                        )
                    )
                if image_url:
                    hits.append(Hit(url=image_url, provider=self.name, kind="image", match=match))
        return hits


def _image_url(item: dict[str, Any]) -> str | None:
    for key in ("image", "original", "original_image"):
        value = item.get(key)
        if isinstance(value, dict):
            value = value.get("link")
        url = as_url(value)
        if url:
            return url
    return None


class SerpApiGoogleLens(SerpApiProvider):
    name = "serpapi_google_lens"
    label = "Google Lens (via SerpApi)"
    destination = "SerpApi (serpapi.com), which queries Google Lens"
    engine = "google_lens"
    url_param = "url"
    result_keys = (("exact_matches", "full"), ("visual_matches", "similar"))
    variants = ((("type", "exact_matches"),), (("type", "visual_matches"),))


class SerpApiGoogleReverseImage(SerpApiProvider):
    name = "serpapi_google_reverse_image"
    label = "Google Reverse Image (via SerpApi)"
    destination = "SerpApi (serpapi.com), which queries Google Images"
    engine = "google_reverse_image"
    url_param = "image_url"
    result_keys = (("image_results", "partial"), ("inline_images", "similar"))


class SerpApiYandex(SerpApiProvider):
    name = "serpapi_yandex"
    label = "Yandex Images (via SerpApi)"
    destination = "SerpApi (serpapi.com), which queries Yandex Images"
    engine = "yandex_images"
    url_param = "url"
    result_keys = (("image_results", "partial"), ("images_results", "similar"))


class SerpApiBing(SerpApiProvider):
    name = "serpapi_bing"
    label = "Bing Reverse Image (via SerpApi)"
    destination = "SerpApi (serpapi.com), which queries Bing Visual Search"
    engine = "bing_reverse_image"
    url_param = "image_url"
    result_keys = (("pages_with_this_image", "full"), ("related_content", "similar"))
