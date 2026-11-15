"""FaceCheck.ID face search.

Docs: https://facecheck.id/Face-Search/API (OpenAPI: /swagger/v1/swagger.json).
Flow: ``POST /api/upload_pic`` (multipart ``images``) returns ``id_search``;
``POST /api/search`` is then polled until ``output`` is present. The token goes
in the ``Authorization`` header as-is. ``demo=true`` searches only the first
100,000 faces and costs no credits.

This is the only provider that matches the *face* rather than the picture, so
it can surface accounts using a different photo of the same person. Treat such
results with extra care: see README, "Ethics & safety".
"""

from __future__ import annotations

import asyncio
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
from avatartrace.models import Hit
from avatartrace.net import HttpClient

BASE = "https://facecheck.id"


class FaceCheckProvider(Provider):
    name = "facecheck"
    label = "FaceCheck.ID (face search)"
    env_keys = ("FACECHECK_API_TOKEN",)
    sends = "image"
    destination = "FaceCheck.ID (facecheck.id)"
    pricing = "paid; 3 credits per search (credits are bought with cryptocurrency); demo mode is free"
    kind = "face"

    def __init__(
        self,
        token: str,
        *,
        demo: bool = False,
        min_score: int = 70,
        timeout: float = 600.0,
        poll_interval: float = 1.5,
    ) -> None:
        self._token = token
        self._demo = demo
        self._min_score = min_score
        self._timeout = timeout
        self._poll_interval = poll_interval

    @classmethod
    def from_settings(cls, settings: Settings) -> Self | None:
        token = settings.secret("facecheck_api_token")
        if not token:
            return None
        return cls(
            token,
            demo=settings.facecheck_demo,
            min_score=settings.facecheck_min_score,
            timeout=settings.facecheck_timeout,
            poll_interval=settings.facecheck_poll_interval,
        )

    async def search(self, query: QueryImage, http: HttpClient) -> list[Hit]:
        headers = {"accept": "application/json", "Authorization": self._token}
        _, data = await request_json(
            http,
            f"{BASE}/api/upload_pic",
            method="POST",
            headers=headers,
            data={"id_search": ""},
            files={"images": ("photo.jpg", query.upload_jpeg, "image/jpeg")},
        )
        upload = as_dict(data)
        _raise_on_error(upload)
        search_id = upload.get("id_search")
        if not isinstance(search_id, str) or not search_id:
            raise ProviderError("upload did not return a search id")

        body = {"id_search": search_id, "with_progress": True, "status_only": False, "demo": self._demo}
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._timeout
        while True:
            _, data = await request_json(http, f"{BASE}/api/search", method="POST", headers=headers, json=body)
            result = as_dict(data)
            _raise_on_error(result)
            output = result.get("output")
            if isinstance(output, dict):
                return self._parse(output)
            if loop.time() >= deadline:
                raise ProviderError(f"search did not finish within {self._timeout:.0f} s")
            await asyncio.sleep(self._poll_interval)

    def _parse(self, output: dict[str, Any]) -> list[Hit]:
        hits: list[Hit] = []
        for item in as_list(output.get("items")):
            item = as_dict(item)
            raw_url = item.get("url")
            if isinstance(raw_url, dict):  # the OpenAPI schema wraps the URL in {"value": ...}
                raw_url = raw_url.get("value")
            url = as_url(raw_url)
            raw_score = item.get("score")
            score = float(raw_score) if isinstance(raw_score, int | float) else None
            if not url or score is None or score < self._min_score:
                continue
            hits.append(Hit(url=url, provider=self.name, kind="page", match="face", score=score))
        return hits


def _raise_on_error(payload: dict[str, Any]) -> None:
    error = payload.get("error")
    if error:
        code = payload.get("code")
        raise ProviderError(f"{error} ({code})" if code else str(error))
