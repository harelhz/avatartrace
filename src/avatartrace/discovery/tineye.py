"""TinEye commercial API.

Docs: https://services.tineye.com/developers/tineyeapi/ (reference client:
https://github.com/TinEye/pytineye). ``POST https://api.tineye.com/rest/search/``
with the key in the ``x-api-key`` header and the image as multipart
``image_upload``.
"""

from __future__ import annotations

from typing import Self

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
from avatartrace.models import Hit
from avatartrace.net import HttpClient

ENDPOINT = "https://api.tineye.com/rest/search/"


class TinEyeProvider(Provider):
    name = "tineye"
    label = "TinEye"
    env_keys = ("TINEYE_API_KEY",)
    sends = "image"
    destination = "TinEye (api.tineye.com)"
    pricing = "paid; sold as prepaid search bundles"

    def __init__(self, api_key: str, limit: int = 100) -> None:
        self._api_key = api_key
        self._limit = limit

    @classmethod
    def from_settings(cls, settings: Settings) -> Self | None:
        key = settings.secret("tineye_api_key")
        return cls(key, settings.tineye_limit) if key else None

    async def search(self, query: QueryImage, http: HttpClient) -> list[Hit]:
        status, data = await request_json(
            http,
            ENDPOINT,
            method="POST",
            headers={"x-api-key": self._api_key},
            data={"limit": str(self._limit), "sort": "score", "order": "desc"},
            files={"image_upload": ("photo.jpg", query.upload_jpeg, "image/jpeg")},
        )
        payload = as_dict(data)
        code = payload.get("code", status)
        if status >= 400 or code != 200:
            messages = [str(m) for m in as_list(payload.get("messages"))]
            raise ProviderError("; ".join(messages) or f"HTTP {status}")
        hits: list[Hit] = []
        for match in as_list(as_dict(payload.get("results")).get("matches")):
            match = as_dict(match)
            raw_score = match.get("score")
            score = float(raw_score) if isinstance(raw_score, int | float) else None
            for backlink in as_list(match.get("backlinks")):
                backlink = as_dict(backlink)
                page = as_url(backlink.get("backlink"))
                image = as_url(backlink.get("url"))
                if page:
                    hits.append(
                        Hit(url=page, provider=self.name, kind="page", match="full", score=score, image_url=image)
                    )
                if image:
                    hits.append(Hit(url=image, provider=self.name, kind="image", match="full", score=score))
        return hits
