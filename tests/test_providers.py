"""Discovery providers against recorded-style HTTP responses.

Response bodies follow each provider's documented schema (see the module
docstrings in ``avatartrace.discovery``). No request ever reaches the network.
"""

from __future__ import annotations

import base64
import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx
from PIL import Image

from avatartrace.discovery import (
    PROVIDER_NAMES,
    PROVIDERS,
    ProviderError,
    QueryImage,
    UnknownProvider,
    configured_providers,
    manual_search_links,
    parse_provider_selection,
)
from avatartrace.discovery.facecheck import FaceCheckProvider
from avatartrace.discovery.google_vision import GoogleVisionProvider
from avatartrace.discovery.saucenao import SauceNaoProvider
from avatartrace.discovery.serpapi import (
    SerpApiBing,
    SerpApiGoogleLens,
    SerpApiGoogleReverseImage,
    SerpApiProvider,
    SerpApiYandex,
)
from avatartrace.discovery.tineye import TinEyeProvider
from avatartrace.net import HttpClient
from tests.conftest import make_settings

SECRET = "sk-test-SECRET-123"
UPLOAD = b"\xff\xd8\xff\xe0fake-jpeg-bytes"
PUBLIC_URL = "https://img.example.com/me.jpg"


def query(public_url: str | None = None) -> QueryImage:
    return QueryImage(source="me.jpg", image=Image.new("RGB", (8, 8)), upload_jpeg=UPLOAD, public_url=public_url)


def pages(hits: list) -> list[str]:  # type: ignore[type-arg]
    return [h.url for h in hits if h.kind == "page"]


def images(hits: list) -> list[str]:  # type: ignore[type-arg]
    return [h.url for h in hits if h.kind == "image"]


# ------------------------------------------------------------------ Google Cloud Vision

VISION_OK = {
    "responses": [
        {
            "webDetection": {
                "webEntities": [{"entityId": "/m/0abc", "score": 0.9, "description": "Selfie"}],
                "fullMatchingImages": [{"url": "https://pbs.twimg.com/profile_images/1/abc_400x400.jpg"}],
                "partialMatchingImages": [{"url": "https://avatars.githubusercontent.com/u/583231?v=4"}],
                "pagesWithMatchingImages": [
                    {
                        "url": "https://www.instagram.com/john.doe/",
                        "pageTitle": "John Doe (@john.doe) • Instagram",
                        "fullMatchingImages": [{"url": "https://scontent.cdninstagram.com/a.jpg"}],
                    },
                    {
                        "url": "https://x.com/johnd/status/123",
                        "pageTitle": "<b>John</b> on X",
                        "partialMatchingImages": [{"url": "https://pbs.twimg.com/media/x.jpg"}],
                    },
                    {"pageTitle": "entry without url"},
                ],
                "visuallySimilarImages": [{"url": "https://example.com/similar.jpg"}, {"url": "not-a-url"}],
                "bestGuessLabels": [{"label": "person"}],
            }
        }
    ]
}


async def test_google_vision_request_and_parsing(mock_http: respx.MockRouter) -> None:
    route = mock_http.post("https://vision.googleapis.com/v1/images:annotate").respond(200, json=VISION_OK)
    async with HttpClient(make_settings()) as http:
        hits = await GoogleVisionProvider(SECRET, max_results=50).search(query(), http)

    request = route.calls.last.request
    assert request.headers["x-goog-api-key"] == SECRET
    assert SECRET not in str(request.url)
    body = json.loads(request.content)
    assert body["requests"][0]["features"] == [{"type": "WEB_DETECTION", "maxResults": 50}]
    assert base64.b64decode(body["requests"][0]["image"]["content"]) == UPLOAD

    assert pages(hits) == ["https://www.instagram.com/john.doe/", "https://x.com/johnd/status/123"]
    by_url = {h.url: h for h in hits}
    assert by_url["https://www.instagram.com/john.doe/"].match == "full"
    assert by_url["https://www.instagram.com/john.doe/"].image_url == "https://scontent.cdninstagram.com/a.jpg"
    assert by_url["https://x.com/johnd/status/123"].match == "partial"
    assert by_url["https://pbs.twimg.com/profile_images/1/abc_400x400.jpg"].match == "full"
    assert by_url["https://avatars.githubusercontent.com/u/583231?v=4"].match == "partial"
    assert by_url["https://example.com/similar.jpg"].match == "similar"
    assert "not-a-url" not in by_url
    assert all(h.provider == "google_vision" for h in hits)


async def test_google_vision_empty_and_error_responses(mock_http: respx.MockRouter) -> None:
    route = mock_http.post("https://vision.googleapis.com/v1/images:annotate")
    async with HttpClient(make_settings()) as http:
        provider = GoogleVisionProvider(SECRET)
        route.respond(200, json={"responses": [{}]})
        assert await provider.search(query(), http) == []
        route.respond(200, json={})
        assert await provider.search(query(), http) == []

        route.respond(400, json={"error": {"code": 400, "message": "API key not valid.", "status": "INVALID_ARGUMENT"}})
        with pytest.raises(ProviderError, match="API key not valid"):
            await provider.search(query(), http)

        route.respond(200, json={"responses": [{"error": {"code": 3, "message": "Bad image data."}}]})
        with pytest.raises(ProviderError, match="Bad image data"):
            await provider.search(query(), http)

        route.respond(500, text="oops")
        with pytest.raises(ProviderError):
            await provider.search(query(), http)

        route.respond(200, text="<html>not json</html>")
        with pytest.raises(ProviderError, match="not JSON"):
            await provider.search(query(), http)


# ------------------------------------------------------------------ SerpApi

LENS_EXACT = {
    "search_metadata": {"status": "Success"},
    "exact_matches": [
        {
            "position": 1,
            "title": "John (@johnd) | TikTok",
            "link": "https://www.tiktok.com/@johnd",
            "source": "TikTok",
            "thumbnail": "https://encrypted-tbn0.gstatic.com/images?q=1",
        }
    ],
}
LENS_VISUAL = {
    "search_metadata": {"status": "Success"},
    "visual_matches": [
        {
            "position": 1,
            "title": "jdoe (John Doe) · GitHub",
            "link": "https://github.com/jdoe",
            "source": "GitHub",
            "thumbnail": "https://encrypted-tbn0.gstatic.com/images?q=2",
            "image": "https://avatars.githubusercontent.com/u/42?v=4",
        },
        {"position": 2, "title": "no link here"},
    ],
}


async def test_serpapi_google_lens_runs_exact_and_visual_searches(mock_http: respx.MockRouter) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        params = parse_qs(urlsplit(str(request.url)).query)
        assert params["engine"] == ["google_lens"]
        assert params["url"] == [PUBLIC_URL]
        assert params["api_key"] == [SECRET]
        return httpx.Response(200, json=LENS_EXACT if params["type"] == ["exact_matches"] else LENS_VISUAL)

    route = mock_http.get("https://serpapi.com/search").mock(side_effect=respond)
    async with HttpClient(make_settings()) as http:
        hits = await SerpApiGoogleLens(SECRET).search(query(PUBLIC_URL), http)
    assert route.call_count == 2
    assert pages(hits) == ["https://www.tiktok.com/@johnd", "https://github.com/jdoe"]
    assert images(hits) == ["https://avatars.githubusercontent.com/u/42?v=4"]
    by_url = {h.url: h for h in hits}
    assert by_url["https://www.tiktok.com/@johnd"].match == "full"
    assert by_url["https://github.com/jdoe"].match == "similar"
    assert by_url["https://github.com/jdoe"].image_url == "https://avatars.githubusercontent.com/u/42?v=4"


@pytest.mark.parametrize(
    ("provider_class", "engine", "url_param", "payload", "expected_pages"),
    [
        (
            SerpApiGoogleReverseImage,
            "google_reverse_image",
            "image_url",
            {
                "image_results": [
                    {"position": 1, "title": "John Doe - Facebook", "link": "https://www.facebook.com/john.doe.37"},
                    {"position": 2, "title": "Pinterest", "link": "https://www.pinterest.com/pin/123/"},
                ],
                "inline_images": [{"link": "https://www.google.com/search?q=x", "original": "https://e.x/a.jpg"}],
            },
            [
                "https://www.facebook.com/john.doe.37",
                "https://www.pinterest.com/pin/123/",
                "https://www.google.com/search?q=x",
            ],
        ),
        (
            SerpApiYandex,
            "yandex_images",
            "url",
            {
                "image_results": [
                    {
                        "link": "https://vk.com/id12345",
                        "source": "vk.com",
                        "original_image": {"link": "https://sun9-1.userapi.com/a.jpg", "width": 400, "height": 400},
                    }
                ]
            },
            ["https://vk.com/id12345"],
        ),
        (
            SerpApiBing,
            "bing_reverse_image",
            "image_url",
            {
                "pages_with_this_image": [
                    {"title": "John", "link": "https://t.me/johndoe", "original": "https://cdn4.telesco.pe/a.jpg"}
                ],
                "related_content": [{"title": "Other", "link": "https://www.reddit.com/user/jdoe_99/"}],
            },
            ["https://t.me/johndoe", "https://www.reddit.com/user/jdoe_99/"],
        ),
    ],
)
async def test_serpapi_engines(
    mock_http: respx.MockRouter,
    provider_class: type[SerpApiProvider],
    engine: str,
    url_param: str,
    payload: dict,  # type: ignore[type-arg]
    expected_pages: list[str],
) -> None:
    route = mock_http.get("https://serpapi.com/search").respond(200, json=payload)
    async with HttpClient(make_settings()) as http:
        hits = await provider_class(SECRET).search(query(PUBLIC_URL), http)
    params = parse_qs(urlsplit(str(route.calls.last.request.url)).query)
    assert params["engine"] == [engine]
    assert params[url_param] == [PUBLIC_URL]
    assert route.call_count == 1
    assert pages(hits) == expected_pages
    assert all(h.provider == provider_class.name for h in hits)


async def test_serpapi_errors_and_empty_results(mock_http: respx.MockRouter) -> None:
    route = mock_http.get("https://serpapi.com/search")
    async with HttpClient(make_settings()) as http:
        provider = SerpApiBing(SECRET)
        route.respond(200, json={"error": "Bing hasn't returned any results for this query."})
        assert await provider.search(query(PUBLIC_URL), http) == []

        route.respond(
            401, json={"error": "Invalid API key. Your API key should be here: https://serpapi.com/manage-api-key"}
        )
        with pytest.raises(ProviderError, match="Invalid API key") as caught:
            await provider.search(query(PUBLIC_URL), http)
        assert SECRET not in str(caught.value)

        route.respond(429, json={"error": "Your account has run out of searches."})
        with pytest.raises(ProviderError, match="run out of searches"):
            await provider.search(query(PUBLIC_URL), http)


async def test_serpapi_needs_a_public_url(mock_http: respx.MockRouter) -> None:
    provider = SerpApiGoogleLens(SECRET)
    reason = provider.unavailable_reason(query())
    assert reason is not None
    assert "public URL" in reason
    assert provider.unavailable_reason(query(PUBLIC_URL)) is None
    async with HttpClient(make_settings()) as http:
        with pytest.raises(ProviderError):
            await provider.search(query(), http)
    assert not mock_http.calls


async def test_serpapi_partial_failure_keeps_the_successful_half(mock_http: respx.MockRouter) -> None:
    mock_http.get("https://serpapi.com/search").mock(
        side_effect=[httpx.Response(200, json={"error": "Lens timed out"}), httpx.Response(200, json=LENS_VISUAL)]
    )
    async with HttpClient(make_settings()) as http:
        hits = await SerpApiGoogleLens(SECRET).search(query(PUBLIC_URL), http)
    assert pages(hits) == ["https://github.com/jdoe"]


# ------------------------------------------------------------------ TinEye

TINEYE_OK = {
    "code": 200,
    "messages": [],
    "stats": {"total_results": 1},
    "results": {
        "matches": [
            {
                "image_url": "https://img.tineye.com/result/abc",
                "domain": "soundcloud.com",
                "score": 67.3,
                "width": 500,
                "height": 500,
                "backlinks": [
                    {
                        "url": "https://i1.sndcdn.com/avatars-000-t500x500.jpg",
                        "backlink": "https://soundcloud.com/johndoe",
                        "crawl_date": "2024-03-01",
                    },
                    {"url": "https://example.com/copy.jpg", "backlink": "", "crawl_date": "2023-01-01"},
                ],
            }
        ]
    },
}


async def test_tineye_request_and_parsing(mock_http: respx.MockRouter) -> None:
    route = mock_http.post("https://api.tineye.com/rest/search/").respond(200, json=TINEYE_OK)
    async with HttpClient(make_settings()) as http:
        hits = await TinEyeProvider(SECRET, limit=25).search(query(), http)
    request = route.calls.last.request
    assert request.headers["x-api-key"] == SECRET
    assert request.headers["content-type"].startswith("multipart/form-data")
    body = request.content
    assert b'name="image_upload"' in body
    assert UPLOAD in body
    assert b'name="limit"\r\n\r\n25' in body
    assert pages(hits) == ["https://soundcloud.com/johndoe"]
    assert images(hits) == ["https://i1.sndcdn.com/avatars-000-t500x500.jpg", "https://example.com/copy.jpg"]
    assert hits[0].score == pytest.approx(67.3)
    assert hits[0].image_url == "https://i1.sndcdn.com/avatars-000-t500x500.jpg"


async def test_tineye_errors(mock_http: respx.MockRouter) -> None:
    route = mock_http.post("https://api.tineye.com/rest/search/")
    async with HttpClient(make_settings()) as http:
        provider = TinEyeProvider(SECRET)
        route.respond(
            401, json={"results": [], "stats": {}, "code": 401, "messages": ["Please provide a valid API key."]}
        )
        with pytest.raises(ProviderError, match="valid API key"):
            await provider.search(query(), http)
        route.respond(200, json={"code": 400, "messages": ["Image too simple", "Try another"]})
        with pytest.raises(ProviderError, match="Image too simple; Try another"):
            await provider.search(query(), http)
        route.respond(200, json={"code": 200, "results": {"matches": []}})
        assert await provider.search(query(), http) == []


# ------------------------------------------------------------------ FaceCheck.ID


def facecheck(**overrides: object) -> FaceCheckProvider:
    options: dict = {"demo": True, "min_score": 70, "timeout": 5.0, "poll_interval": 0.0}  # type: ignore[type-arg]
    options.update(overrides)
    return FaceCheckProvider(SECRET, **options)


async def test_facecheck_upload_poll_and_parse(mock_http: respx.MockRouter) -> None:
    upload = mock_http.post("https://facecheck.id/api/upload_pic").respond(
        200, json={"id_search": "abc-123", "message": "uploaded", "error": None, "code": None}
    )
    search = mock_http.post("https://facecheck.id/api/search").mock(
        side_effect=[
            httpx.Response(200, json={"message": "Searching", "progress": 10, "error": None, "output": None}),
            httpx.Response(200, json={"message": "Searching", "progress": 80, "error": None, "output": None}),
            httpx.Response(
                200,
                json={
                    "error": None,
                    "output": {
                        "items": [
                            {
                                "score": 96,
                                "url": "https://www.instagram.com/john.doe/",
                                "base64": "data:image/webp;base64,AA",
                            },
                            {"score": 84, "url": {"value": "https://github.com/jdoe"}, "base64": ""},
                            {"score": 55, "url": "https://example.com/weak"},
                            {"score": 90, "url": None},
                            {"url": "https://example.com/no-score"},
                        ]
                    },
                },
            ),
        ]
    )
    async with HttpClient(make_settings()) as http:
        hits = await facecheck().search(query(), http)

    upload_request = upload.calls.last.request
    assert upload_request.headers["authorization"] == SECRET
    assert b'name="images"' in upload_request.content
    assert UPLOAD in upload_request.content
    assert search.call_count == 3
    body = json.loads(search.calls.last.request.content)
    assert body == {"id_search": "abc-123", "with_progress": True, "status_only": False, "demo": True}

    assert [(h.url, h.score, h.match) for h in hits] == [
        ("https://www.instagram.com/john.doe/", 96.0, "face"),
        ("https://github.com/jdoe", 84.0, "face"),
    ]


async def test_facecheck_errors(mock_http: respx.MockRouter) -> None:
    upload = mock_http.post("https://facecheck.id/api/upload_pic")
    search = mock_http.post("https://facecheck.id/api/search")
    async with HttpClient(make_settings()) as http:
        upload.respond(200, json={"error": "Missing API Token.", "code": "INVALID_TOKEN"})
        with pytest.raises(ProviderError, match=r"Missing API Token\. \(INVALID_TOKEN\)"):
            await facecheck().search(query(), http)

        upload.respond(200, json={"error": None, "id_search": None})
        with pytest.raises(ProviderError, match="search id"):
            await facecheck().search(query(), http)

        upload.respond(200, json={"error": None, "id_search": "abc"})
        search.respond(200, json={"error": "No face detected", "code": "NO_FACE"})
        with pytest.raises(ProviderError, match="No face detected"):
            await facecheck().search(query(), http)

        search.respond(200, json={"error": None, "output": None, "progress": 1})
        with pytest.raises(ProviderError, match="did not finish"):
            await facecheck(timeout=0.01).search(query(), http)


def test_facecheck_is_flagged_as_face_search() -> None:
    assert FaceCheckProvider.kind == "face"
    assert all(p.kind == "image" for p in PROVIDERS if p is not FaceCheckProvider)


# ------------------------------------------------------------------ SauceNAO

SAUCENAO_OK = {
    "header": {"status": 0, "results_returned": 3, "short_remaining": 3, "long_remaining": 99},
    "results": [
        {
            "header": {"similarity": "95.12", "thumbnail": "https://img1.saucenao.com/a.jpg", "index_id": 41},
            "data": {
                "ext_urls": ["https://twitter.com/artist_x/status/123"],
                "twitter_user_handle": "artist_x",
                "twitter_user_id": "42",
            },
        },
        {
            "header": {"similarity": "71.50", "index_id": 34},
            "data": {
                "ext_urls": ["https://deviantart.com/view/1"],
                "author_name": "painter",
                "author_url": "https://www.deviantart.com/painter",
            },
        },
        {"header": {"similarity": "31.00"}, "data": {"ext_urls": ["https://example.com/low"]}},
        {"header": {"similarity": "n/a"}, "data": {"ext_urls": ["https://example.com/bad"]}},
    ],
}


async def test_saucenao_request_and_parsing(mock_http: respx.MockRouter) -> None:
    route = mock_http.post("https://saucenao.com/search.php").respond(200, json=SAUCENAO_OK)
    async with HttpClient(make_settings()) as http:
        hits = await SauceNaoProvider(SECRET, min_similarity=60).search(query(), http)
    request = route.calls.last.request
    params = parse_qs(urlsplit(str(request.url)).query)
    assert params["output_type"] == ["2"]
    assert params["api_key"] == [SECRET]
    assert b'name="file"' in request.content
    assert [(h.url, h.match, h.score) for h in hits] == [
        ("https://twitter.com/artist_x/status/123", "full", 95.12),
        ("https://x.com/artist_x", "full", 95.12),
        ("https://deviantart.com/view/1", "partial", 71.5),
        ("https://www.deviantart.com/painter", "partial", 71.5),
    ]


async def test_saucenao_errors(mock_http: respx.MockRouter) -> None:
    route = mock_http.post("https://saucenao.com/search.php")
    async with HttpClient(make_settings()) as http:
        provider = SauceNaoProvider(SECRET)
        route.respond(
            200, json={"header": {"status": -1, "message": "The anonymous account type does not permit API usage."}}
        )
        with pytest.raises(ProviderError, match="does not permit API usage"):
            await provider.search(query(), http)
        route.respond(429, json={"header": {"status": -2, "message": "Daily Search Limit Exceeded."}})
        with pytest.raises(ProviderError, match="Limit Exceeded"):
            await provider.search(query(), http)
        route.respond(200, json={"header": {"status": 0}, "results": []})
        assert await provider.search(query(), http) == []


# ------------------------------------------------------------------ registry and selection


def test_no_keys_means_no_providers() -> None:
    assert configured_providers(make_settings()) == []


def test_each_key_enables_its_provider() -> None:
    settings = make_settings(
        google_vision_api_key="g",
        serpapi_api_key="s",
        tineye_api_key="t",
        facecheck_api_token="f",
        saucenao_api_key="n",
    )
    assert [p.name for p in configured_providers(settings)] == list(PROVIDER_NAMES)
    assert [p.name for p in configured_providers(settings, ["tineye", "facecheck"])] == ["tineye", "facecheck"]
    assert configured_providers(settings, []) == []
    only_lens = make_settings(serpapi_api_key="s", serpapi_engines=["google_lens"])
    assert [p.name for p in configured_providers(only_lens)] == ["serpapi_google_lens"]


def test_blank_keys_do_not_enable_providers() -> None:
    assert configured_providers(make_settings(tineye_api_key="   ", serpapi_api_key="")) == []


def test_provider_selection_parsing() -> None:
    assert parse_provider_selection("all") is None
    assert parse_provider_selection("") is None
    assert parse_provider_selection("none") == []
    assert parse_provider_selection(" TinEye , google_vision ") == ["tineye", "google_vision"]
    with pytest.raises(UnknownProvider, match="pimeyes"):
        parse_provider_selection("tineye,pimeyes")


def test_every_provider_declares_what_it_sends() -> None:
    for provider in PROVIDERS:
        assert provider.sends in ("image", "url")
        assert provider.destination
        assert provider.pricing
        assert provider.env_keys
    settings = make_settings(google_vision_api_key="g", serpapi_api_key="s")
    text = [p.disclosure() for p in configured_providers(settings)]
    assert any("the photo itself" in line and "Google Cloud" in line for line in text)
    assert any("public URL" in line and "SerpApi" in line for line in text)


def test_secrets_never_appear_in_settings_repr() -> None:
    settings = make_settings(tineye_api_key=SECRET, github_token=SECRET)
    assert SECRET not in repr(settings)
    assert SECRET not in str(settings.model_dump())
    assert settings.secret("tineye_api_key") == SECRET


def test_manual_links() -> None:
    with_url = manual_search_links("https://img.example.com/a b.jpg?x=1&y=2")
    assert [name for name, _ in with_url] == ["Google Lens", "Yandex Images", "Bing Visual Search", "TinEye"]
    for _, link in with_url:
        assert "https%3A%2F%2Fimg.example.com%2Fa%20b.jpg%3Fx%3D1%26y%3D2" in link
    without = manual_search_links(None)
    assert len(without) == 4
    assert all("upload" in name for name, _ in without)
    assert all("img.example.com" not in link for _, link in without)
