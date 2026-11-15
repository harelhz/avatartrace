"""The local web UI and its safety guards."""

from __future__ import annotations

import re
from collections.abc import Callable

import pytest
import respx

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from avatartrace.web import create_app
from tests.conftest import FIXTURES, make_settings

Bytes = Callable[[str], bytes]
PORT = 8765


def client(**settings: object) -> tuple[TestClient, str]:
    app = create_app(make_settings(**settings), port=PORT, extra_hosts=("testserver",))
    test_client = TestClient(app)
    page = test_client.get("/")
    assert page.status_code == 200
    token = re.search(r'const TOKEN = "([^"]+)"', page.text)
    assert token is not None
    return test_client, token.group(1)


def headers(token: str) -> dict[str, str]:
    return {"X-AvatarTrace-Token": token}


def test_page_is_served_with_strict_headers() -> None:
    test_client, token = client()
    page = test_client.get("/")
    assert "Drop a profile picture here" in page.text
    assert "__TOKEN__" not in page.text
    assert len(token) >= 32
    assert page.headers["x-frame-options"] == "DENY"
    assert page.headers["cache-control"] == "no-store"
    assert "default-src 'none'" in page.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]


def test_each_server_start_gets_its_own_token() -> None:
    assert client()[1] != client()[1]


def test_scan_requires_the_session_token(mock_http: respx.MockRouter) -> None:
    test_client, _ = client()
    files = {"photo": ("logo.png", (FIXTURES / "logo.png").read_bytes(), "image/png")}
    assert test_client.post("/api/scan", files=files).status_code == 403
    assert test_client.post("/api/scan", files=files, headers=headers("wrong")).status_code == 403
    assert test_client.post("/api/plan", headers=headers("wrong")).status_code == 403
    assert not mock_http.calls


def test_other_websites_cannot_call_the_api(mock_http: respx.MockRouter) -> None:
    test_client, token = client()
    files = {"photo": ("logo.png", (FIXTURES / "logo.png").read_bytes(), "image/png")}
    cross = test_client.post("/api/scan", files=files, headers={**headers(token), "Origin": "https://evil.example"})
    assert cross.status_code == 403
    same = test_client.post(
        "/api/scan",
        files=files,
        data={"use_face": "false"},
        headers={**headers(token), "Origin": f"http://127.0.0.1:{PORT}"},
    )
    assert same.status_code == 200


def test_dns_rebinding_is_refused() -> None:
    test_client, _ = client()
    assert test_client.get("/", headers={"Host": "evil.example"}).status_code == 400
    assert test_client.get("/", headers={"Host": "127.0.0.1:8765"}).status_code == 200
    assert test_client.get("/", headers={"Host": "localhost"}).status_code == 200


def test_upload_scan_returns_the_results_table(mock_http: respx.MockRouter, fixture_bytes: Bytes) -> None:
    test_client, token = client()
    files = {"photo": ("my-photo.png", fixture_bytes("logo.png"), "image/png")}
    response = test_client.post("/api/scan", files=files, data={"use_face": "false"}, headers=headers(token))
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["query"]["source"] == "my-photo.png", "the temporary upload path is never exposed"
    assert data["query"]["thumb"].startswith("data:image/jpeg;base64,")
    assert data["matches"] == []
    assert len(data["manual_search_links"]) == 4
    assert not mock_http.calls


def test_url_scan_and_report_download(mock_http: respx.MockRouter, fixture_bytes: Bytes) -> None:
    source = "https://avatars.githubusercontent.com/u/583231?v=4"
    mock_http.get(source).respond(200, content=fixture_bytes("logo.png"))
    mock_http.get("https://api.github.com/user/583231").respond(200, json={"login": "octocat"})
    mock_http.get("https://api.github.com/users/octocat").respond(200, json={"avatar_url": source})
    test_client, token = client()
    assert test_client.get(f"/api/report.html?token_query={token}").status_code == 404

    response = test_client.post("/api/scan", data={"url": source, "use_face": "false"}, headers=headers(token))
    assert response.status_code == 200, response.text
    (match,) = response.json()["matches"]
    assert (match["platform"], match["username"], match["label"]) == ("github", "octocat", "EXACT")
    assert match["avatar_thumb"].startswith("data:image/jpeg;base64,")

    html = test_client.get(f"/api/report.html?token_query={token}")
    assert html.status_code == 200
    assert "attachment" in html.headers["content-disposition"]
    assert "octocat" in html.text
    assert test_client.get(f"/api/report.csv?token_query={token}").text.startswith("Platform,")
    assert test_client.get("/api/report.html?token_query=wrong").status_code == 403
    assert test_client.get(f"/api/report.pdf?token_query={token}").status_code == 404


def test_consent_is_needed_before_the_web_ui_uploads(mock_http: respx.MockRouter, fixture_bytes: Bytes) -> None:
    api = mock_http.post("https://api.tineye.com/rest/search/").respond(
        200, json={"code": 200, "results": {"matches": []}}
    )
    test_client, token = client(tineye_api_key="k")
    plan = test_client.post("/api/plan", data={"has_url": "false"}, headers=headers(token))
    assert plan.json() == {"uploads": ["TinEye: sends the photo itself to TinEye (api.tineye.com)"]}

    files = {"photo": ("logo.png", fixture_bytes("logo.png"), "image/png")}
    response = test_client.post("/api/scan", files=files, data={"use_face": "false"}, headers=headers(token))
    assert response.status_code == 200
    assert not api.called
    assert response.json()["providers"][0]["status"] == "skipped"

    response = test_client.post(
        "/api/scan", files=files, data={"use_face": "false", "consent": "true"}, headers=headers(token)
    )
    assert api.call_count == 1
    assert response.json()["providers"][0]["status"] == "ok"


@pytest.mark.parametrize(
    ("data", "files", "status", "message"),
    [
        ({}, None, 400, "choose a photo"),
        ({"url": "http://127.0.0.1/x.jpg"}, None, 400, "public http"),
        ({"providers": "pimeyes"}, {"photo": ("a.png", b"x", "image/png")}, 400, "unknown provider"),
        ({"use_face": "false"}, {"photo": ("a.png", b"not a picture", "image/png")}, 400, "could not read the photo"),
        ({"min_confidence": "500"}, {"photo": ("a.png", b"x", "image/png")}, 422, ""),
    ],
)
def test_bad_requests(
    mock_http: respx.MockRouter,
    data: dict[str, str],
    files: dict[str, tuple[str, bytes, str]] | None,
    status: int,
    message: str,
) -> None:
    test_client, token = client()
    response = test_client.post("/api/scan", data=data, files=files, headers=headers(token))
    assert response.status_code == status
    assert message in response.text
    assert not mock_http.calls


def test_api_docs_are_not_exposed() -> None:
    test_client, _ = client()
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert test_client.get(path).status_code == 404
