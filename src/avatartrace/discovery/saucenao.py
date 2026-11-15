"""SauceNAO: reverse image search specialised in artwork, anime and illustrations.

Useful for non-photographic avatars. ``POST https://saucenao.com/search.php``
with ``output_type=2`` (JSON), ``api_key`` and the image as multipart ``file``.
A free account key allows a limited number of searches per day.
"""

from __future__ import annotations

from typing import Any, Self

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

ENDPOINT = "https://saucenao.com/search.php"
_FULL_MATCH_SIMILARITY = 90.0


class SauceNaoProvider(Provider):
    name = "saucenao"
    label = "SauceNAO (artwork and anime)"
    env_keys = ("SAUCENAO_API_KEY",)
    sends = "image"
    destination = "SauceNAO (saucenao.com)"
    pricing = "free account key with daily search limits; paid upgrades raise them"

    def __init__(self, api_key: str, min_similarity: float = 60.0) -> None:
        self._api_key = api_key
        self._min_similarity = min_similarity

    @classmethod
    def from_settings(cls, settings: Settings) -> Self | None:
        key = settings.secret("saucenao_api_key")
        return cls(key, settings.saucenao_min_similarity) if key else None

    async def search(self, query: QueryImage, http: HttpClient) -> list[Hit]:
        status, data = await request_json(
            http,
            ENDPOINT,
            method="POST",
            params={"output_type": 2, "api_key": self._api_key, "numres": 16},
            files={"file": ("photo.jpg", query.upload_jpeg, "image/jpeg")},
        )
        payload = as_dict(data)
        header = as_dict(payload.get("header"))
        api_status = header.get("status", 0)
        if status >= 400 or (isinstance(api_status, int) and api_status < 0):
            message = header.get("message") or f"HTTP {status}"
            raise ProviderError(str(message))
        hits: list[Hit] = []
        for result in as_list(payload.get("results")):
            result = as_dict(result)
            similarity = _similarity(as_dict(result.get("header")))
            if similarity is None or similarity < self._min_similarity:
                continue
            match: HitMatch = "full" if similarity >= _FULL_MATCH_SIMILARITY else "partial"
            for url in _urls(as_dict(result.get("data"))):
                hits.append(Hit(url=url, provider=self.name, kind="page", match=match, score=similarity))
        return hits


def _similarity(header: dict[str, Any]) -> float | None:
    try:
        return float(header.get("similarity", ""))
    except (TypeError, ValueError):
        return None


def _urls(data: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for value in (*as_list(data.get("ext_urls")), data.get("author_url"), data.get("source")):
        url = as_url(value)
        if url and url not in urls:
            urls.append(url)
    handle = data.get("twitter_user_handle")
    if isinstance(handle, str) and handle:
        urls.append(f"https://x.com/{handle}")
    return urls
