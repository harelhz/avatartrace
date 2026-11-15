"""Google Cloud Vision, Web Detection feature.

Docs: https://cloud.google.com/vision/docs/detecting-web
``POST https://vision.googleapis.com/v1/images:annotate`` with an API key in the
``x-goog-api-key`` header and the image as base64 ``content``.
"""

from __future__ import annotations

import base64
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

ENDPOINT = "https://vision.googleapis.com/v1/images:annotate"


class GoogleVisionProvider(Provider):
    name = "google_vision"
    label = "Google Cloud Vision (Web Detection)"
    env_keys = ("GOOGLE_VISION_API_KEY",)
    sends = "image"
    destination = "Google Cloud (vision.googleapis.com)"
    pricing = "paid; the first 1,000 Web Detection requests each month are free"

    def __init__(self, api_key: str, max_results: int = 100) -> None:
        self._api_key = api_key
        self._max_results = max_results

    @classmethod
    def from_settings(cls, settings: Settings) -> Self | None:
        key = settings.secret("google_vision_api_key")
        return cls(key, settings.google_vision_max_results) if key else None

    async def search(self, query: QueryImage, http: HttpClient) -> list[Hit]:
        body = {
            "requests": [
                {
                    "image": {"content": base64.b64encode(query.upload_jpeg).decode("ascii")},
                    "features": [{"type": "WEB_DETECTION", "maxResults": self._max_results}],
                }
            ]
        }
        status, data = await request_json(
            http, ENDPOINT, method="POST", headers={"x-goog-api-key": self._api_key}, json=body
        )
        payload = as_dict(data)
        if status >= 400 or "error" in payload:
            raise ProviderError(_error_message(payload) or f"HTTP {status}")
        responses = as_list(payload.get("responses"))
        if not responses:
            return []
        first = as_dict(responses[0])
        if "error" in first:
            raise ProviderError(_error_message(first) or "the API reported an error")
        return self._parse(as_dict(first.get("webDetection")))

    def _parse(self, web: dict[str, Any]) -> list[Hit]:
        hits: list[Hit] = []
        for page in as_list(web.get("pagesWithMatchingImages")):
            page = as_dict(page)
            url = as_url(page.get("url"))
            if not url:
                continue
            full = [as_url(as_dict(i).get("url")) for i in as_list(page.get("fullMatchingImages"))]
            partial = [as_url(as_dict(i).get("url")) for i in as_list(page.get("partialMatchingImages"))]
            match: HitMatch = "full" if any(full) else "partial"
            image_url = next((u for u in (*full, *partial) if u), None)
            title = page.get("pageTitle")
            hits.append(
                Hit(
                    url=url,
                    provider=self.name,
                    kind="page",
                    match=match,
                    title=title if isinstance(title, str) else None,
                    image_url=image_url,
                )
            )
        image_groups: tuple[tuple[str, HitMatch], ...] = (
            ("fullMatchingImages", "full"),
            ("partialMatchingImages", "partial"),
            ("visuallySimilarImages", "similar"),
        )
        for key, match in image_groups:
            for image in as_list(web.get(key)):
                url = as_url(as_dict(image).get("url"))
                if url:
                    hits.append(Hit(url=url, provider=self.name, kind="image", match=match))
        return hits


def _error_message(payload: dict[str, Any]) -> str | None:
    error = as_dict(payload.get("error"))
    message = error.get("message")
    return str(message) if message else None
