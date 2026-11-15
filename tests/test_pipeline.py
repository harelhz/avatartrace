"""The whole scan, end to end, with every HTTP request mocked."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import ClassVar, Self

import numpy as np
import pytest
import respx
from PIL import Image

from avatartrace.compare.engines import Vector
from avatartrace.config import Settings
from avatartrace.discovery import Provider, ProviderError, QueryImage
from avatartrace.imaging import decode_image, pixel_sha256
from avatartrace.models import Hit, MatchLabel, Result, ScanReport
from avatartrace.net import HttpClient
from avatartrace.pipeline import ScanError, Scanner, ScanOptions, planned_uploads
from tests.conftest import FIXTURES, make_settings

Bytes = Callable[[str], bytes]


def page(head: str) -> str:
    return f"<!doctype html><html><head>{head}</head><body></body></html>"


def og(url: str) -> str:
    return page(f'<meta property="og:image" content="{url}">')


class FakeProvider(Provider):
    """A provider that returns canned hits and records what it was given."""

    name: ClassVar[str] = "fake"
    label: ClassVar[str] = "Fake search"
    env_keys: ClassVar[tuple[str, ...]] = ("FAKE_KEY",)
    sends = "image"
    destination: ClassVar[str] = "nowhere"
    pricing: ClassVar[str] = "free"

    def __init__(self, hits: list[Hit] | None = None, error: Exception | None = None, name: str = "fake") -> None:
        self.name = name  # type: ignore[misc]
        self._hits = hits or []
        self._error = error
        self.received: list[QueryImage] = []

    @classmethod
    def from_settings(cls, settings: Settings) -> Self | None:
        return None

    async def search(self, query: QueryImage, http: HttpClient) -> list[Hit]:
        self.received.append(query)
        if self._error is not None:
            raise self._error
        return list(self._hits)


class FakeFaces:
    """Face 'model' with fixed answers: both photos of person A share a direction, B is orthogonal."""

    name = "fake-face"

    def __init__(self) -> None:
        def sha(name: str) -> str:
            return pixel_sha256(decode_image((FIXTURES / name).read_bytes()))

        a: Vector = np.asarray([1.0, 0.0, 0.0], dtype=np.float32)
        a_other: Vector = np.asarray([0.8, 0.6, 0.0], dtype=np.float32)
        b: Vector = np.asarray([0.0, 0.0, 1.0], dtype=np.float32)
        self.table: dict[str, list[Vector]] = {
            sha("face_a.jpg"): [a],
            sha("face_a_resized.jpg"): [a],
            sha("face_a_other.jpg"): [a_other],
            sha("face_b.jpg"): [b],
        }

    def embed(self, image: Image.Image) -> list[Vector]:
        return self.table.get(pixel_sha256(image), [])


def by_account(report: ScanReport) -> dict[str, Result]:
    return {f"{r.account.platform}:{r.account.username}": r for r in report.results}


def mock_main_scenario(router: respx.MockRouter, data: Bytes) -> None:
    router.get("https://www.instagram.com/john.doe/").respond(200, html=og("https://cdn.example/ig.jpg"))
    router.get("https://cdn.example/ig.jpg").respond(200, content=data("face_a.jpg"))
    router.get("https://www.tiktok.com/@johnd").respond(200, html="<html><script>challenge()</script></html>")
    router.get("https://api.github.com/user/42").respond(200, json={"login": "jdoe", "id": 42})
    router.get("https://api.github.com/users/jdoe").respond(200, json={"avatar_url": "https://cdn.example/gh.jpg"})
    router.get("https://cdn.example/gh.jpg").respond(200, content=data("face_a_other.jpg"))
    router.get("https://x.com/johnd").respond(
        200,
        html=page(
            '<link rel="preload" as="image" imageSrcSet="https://pbs.twimg.com/profile_images/7/abc_mini.jpg 24w"/>'
        ),
    )
    router.get("https://pbs.twimg.com/profile_images/7/abc_400x400.jpg").respond(
        200, content=data("face_a_resized.jpg")
    )
    router.get("https://soundcloud.com/stranger").respond(200, html=og("https://cdn.example/sc.jpg"))
    router.get("https://cdn.example/sc.jpg").respond(200, content=data("face_b.jpg"))
    router.get("https://www.linkedin.com/in/john-doe-1/").respond(999)
    router.get("https://www.reddit.com/user/gone_user/").respond(404)


MAIN_HITS = [
    Hit("https://www.instagram.com/john.doe/", "fake", "page", "full", title="John"),
    Hit("https://www.tiktok.com/@johnd/video/7234567890", "fake", "page", "partial"),
    Hit("https://github.com/jdoe", "fake", "page", "similar"),
    Hit("https://avatars.githubusercontent.com/u/42?v=4", "fake", "image", "partial"),
    Hit("https://x.com/johnd", "fake", "page", "full", score=91.0),
    Hit("https://soundcloud.com/stranger", "fake", "page", "similar"),
    Hit("https://example.com/blog/post-about-john", "fake", "page", "full"),
    Hit("https://www.linkedin.com/in/john-doe-1/", "fake", "page", "partial"),
    Hit("https://www.reddit.com/user/gone_user/", "fake", "page", "partial"),
    Hit("https://www.instagram.com/p/Cabc123/", "fake", "page", "full"),
]


async def run_main(
    mock_http: respx.MockRouter, data: Bytes, **option_overrides: object
) -> tuple[ScanReport, list[FakeProvider]]:
    mock_main_scenario(mock_http, data)
    providers = [
        FakeProvider(MAIN_HITS),
        FakeProvider([Hit("https://instagram.com/John.Doe?hl=en", "second", "page", "full")], name="second"),
        FakeProvider(error=ProviderError("quota exceeded"), name="broken"),
        FakeProvider(error=RuntimeError("bug"), name="buggy"),
    ]
    options = ScanOptions(upload_consent=True, **option_overrides)  # type: ignore[arg-type]
    scanner = Scanner(make_settings(), options, providers=list(providers), face_engine=FakeFaces())
    report = await scanner.run(str(FIXTURES / "face_a.jpg"))
    return report, providers


async def test_full_scan_labels_every_candidate(mock_http: respx.MockRouter, fixture_bytes: Bytes) -> None:
    report, providers = await run_main(mock_http, fixture_bytes)
    results = by_account(report)

    instagram = results["instagram:john.doe"]
    assert (instagram.label, instagram.confidence) == (MatchLabel.EXACT, 100.0)
    assert instagram.found_via == ["fake", "second"], "the same account from two providers is merged"
    assert instagram.avatar_thumb is not None

    x = results["x:johnd"]
    assert x.label is MatchLabel.NEAR_DUPLICATE
    assert x.confidence is not None
    assert x.confidence >= 80
    assert x.avatar_url == "https://pbs.twimg.com/profile_images/7/abc_400x400.jpg"

    github = results["github:jdoe"]
    assert github.label is MatchLabel.LIKELY_SAME_FACE
    assert github.method == "face"
    assert github.found_via == ["fake", "fake (avatar CDN)"], "profile URL and avatar CDN URL map to one account"

    assert [r.account.platform for r in report.matches()] == ["instagram", "x", "github"]

    tiktok = results["tiktok:johnd"]
    assert tiktok.label is MatchLabel.UNVERIFIED
    assert tiktok.confidence is None
    assert tiktok.found_via == ["fake (post URL)"]
    assert tiktok.match_type == "UNVERIFIED (found via fake (post URL))"
    assert "does not expose" in (tiktok.reason or "")

    assert "999" in (results["linkedin:john-doe-1"].reason or "")
    assert "not found" in (results["reddit:gone_user"].reason or "")
    assert {r.account.platform for r in report.unverified()} == {"tiktok", "linkedin", "reddit"}

    rejected = results["soundcloud:stranger"]
    assert rejected.label is MatchLabel.NOT_A_MATCH
    assert rejected.avatar_thumb is None
    assert rejected not in report.matches()

    assert len(report.results) == 7, "non-profile pages produce no candidates"
    statuses = {p.name: p for p in report.providers}
    assert (statuses["fake"].status, statuses["fake"].hits) == ("ok", len(MAIN_HITS))
    assert (statuses["broken"].status, statuses["broken"].message) == ("error", "quota exceeded")
    assert statuses["buggy"].status == "error"
    assert report.query_faces == 1
    assert report.query_source == "face_a.jpg", "reports show the file name, never the local path"
    assert report.engines == {"face": "fake-face", "clip": "off"}
    assert providers[0].received[0].upload_jpeg.startswith(b"\xff\xd8")
    assert report.stats["candidates"] == 7


async def test_min_confidence_only_hides_rows(mock_http: respx.MockRouter, fixture_bytes: Bytes) -> None:
    report, _ = await run_main(mock_http, fixture_bytes, min_confidence=99.5)
    assert [r.account.platform for r in report.matches()] == ["instagram"]
    assert report.hidden_count() == 2
    assert len(report.unverified()) == 3, "unverified leads are never filtered by confidence"
    assert len(report.matches(0)) == 3


async def test_no_upload_without_consent(mock_http: respx.MockRouter) -> None:
    api = mock_http.post("https://api.tineye.com/rest/search/").respond(
        200, json={"code": 200, "results": {"matches": []}}
    )
    settings = make_settings(tineye_api_key="k")
    report = await Scanner(settings, ScanOptions(use_face=False)).run(str(FIXTURES / "logo.png"))
    assert not api.called, "nothing may be uploaded without explicit consent"
    assert not mock_http.calls
    status = report.providers[0]
    assert (status.name, status.status) == ("tineye", "skipped")
    assert "not confirmed" in (status.message or "")

    report = await Scanner(settings, ScanOptions(use_face=False, upload_consent=True)).run(str(FIXTURES / "logo.png"))
    assert api.call_count == 1
    assert report.providers[0].status == "ok"


async def test_url_only_providers_are_skipped_for_local_files(mock_http: respx.MockRouter) -> None:
    settings = make_settings(serpapi_api_key="k")
    options = ScanOptions(use_face=False, upload_consent=True)
    assert planned_uploads("photo.jpg", options, settings) == []
    report = await Scanner(settings, options).run(str(FIXTURES / "logo.png"))
    assert not mock_http.calls
    assert {p.status for p in report.providers} == {"skipped"}
    assert all("public URL" in (p.message or "") for p in report.providers)
    assert len(planned_uploads("https://img.example/a.jpg", options, settings)) == 4
    assert len(planned_uploads("photo.jpg", ScanOptions(public_url="https://img.example/a.jpg"), settings)) == 4
    assert planned_uploads("https://img.example/a.jpg", ScanOptions(providers=[]), settings) == []


async def test_without_providers_the_scan_still_runs_and_offers_manual_links(mock_http: respx.MockRouter) -> None:
    report = await Scanner(make_settings(), ScanOptions(use_face=False)).run(str(FIXTURES / "logo.png"))
    assert report.results == []
    assert report.providers == []
    assert report.manual_links[0][0].startswith("Google Lens")
    assert not mock_http.calls


async def test_photo_given_as_url_is_traced_through_its_cdn(mock_http: respx.MockRouter, fixture_bytes: Bytes) -> None:
    source = "https://avatars.githubusercontent.com/u/583231?v=4"
    mock_http.get(source).respond(200, content=fixture_bytes("logo.png"))
    mock_http.get("https://api.github.com/user/583231").respond(200, json={"login": "octocat"})
    mock_http.get("https://api.github.com/users/octocat").respond(200, json={"avatar_url": source})
    report = await Scanner(make_settings(), ScanOptions(use_face=False)).run(source)
    (match,) = report.matches()
    assert (match.account.platform, match.account.username, match.label) == ("github", "octocat", MatchLabel.EXACT)
    assert match.found_via == ["input URL (avatar CDN)"]
    assert all("avatars.githubusercontent.com" in link for _, link in report.manual_links)


async def test_manual_candidates_are_verified(mock_http: respx.MockRouter, fixture_bytes: Bytes) -> None:
    mock_http.get("https://api.github.com/users/octocat").respond(200, json={"avatar_url": "https://cdn.example/o.png"})
    mock_http.get("https://cdn.example/o.png").respond(200, content=fixture_bytes("logo_resized.jpg"))
    options = ScanOptions(use_face=False, candidates=["https://github.com/octocat", "https://example.com/nope"])
    report = await Scanner(make_settings(), options).run(str(FIXTURES / "logo.png"))
    (match,) = report.matches()
    assert match.label is MatchLabel.NEAR_DUPLICATE
    assert match.found_via == ["manual"]


async def test_username_expansion(mock_http: respx.MockRouter, fixture_bytes: Bytes) -> None:
    mock_http.get("https://api.github.com/users/octocat").respond(200, json={"avatar_url": "https://cdn.example/o.png"})
    mock_http.get("https://cdn.example/o.png").respond(200, content=fixture_bytes("logo.png"))
    # one site reuses the picture, one has a different picture, one blocks us; everything else has no such account
    mock_http.get("https://gitlab.com/api/v4/users").respond(200, json=[{"avatar_url": "https://cdn.example/gl.jpg"}])
    mock_http.get("https://cdn.example/gl.jpg").respond(200, content=fixture_bytes("logo_resized.jpg"))
    mock_http.get("https://codeberg.org/api/v1/users/octocat").respond(
        200, json={"avatar_url": "https://cdn.example/cb.png"}
    )
    mock_http.get("https://cdn.example/cb.png").respond(200, content=fixture_bytes("logo_other.png"))
    mock_http.get("https://dev.to/api/users/by_username").respond(403)
    mock_http.route().respond(404)

    options = ScanOptions(use_face=False, expand=True, candidates=["https://github.com/octocat"])
    scanner = Scanner(make_settings(), options)
    report = await scanner.run(str(FIXTURES / "logo.png"))

    matches = {r.account.platform: r for r in report.matches()}
    assert set(matches) == {"github", "gitlab"}
    assert matches["gitlab"].label is MatchLabel.NEAR_DUPLICATE
    assert matches["gitlab"].found_via == ["username expansion (from github:octocat)"]
    assert matches["gitlab"].expanded_from == "github:octocat"
    assert report.unverified() == [], "a username alone is not evidence: uncomparable guesses are not reported"
    assert all(r.label is not MatchLabel.NOT_A_MATCH for r in report.results)

    expected_checks = sum(1 for a in scanner.registry.expansion_accounts("octocat") if a.platform != "github")
    assert expected_checks >= 50
    assert report.stats["expansion_checked"] == expected_checks
    assert report.stats["expansion_different_picture"] == 1
    assert report.stats["expansion_not_comparable"] >= 1
    assert report.expanded is True


async def test_expansion_is_off_by_default_and_needs_a_verified_match(
    mock_http: respx.MockRouter, fixture_bytes: Bytes
) -> None:
    mock_http.get("https://api.github.com/users/octocat").respond(200, json={"avatar_url": "https://cdn.example/o.png"})
    mock_http.get("https://cdn.example/o.png").respond(200, content=fixture_bytes("logo_other.png"))
    candidates = ["https://github.com/octocat"]
    report = await Scanner(make_settings(), ScanOptions(use_face=False, candidates=candidates)).run(
        str(FIXTURES / "logo.png")
    )
    assert "expansion_checked" not in report.stats
    report = await Scanner(make_settings(), ScanOptions(use_face=False, expand=True, candidates=candidates)).run(
        str(FIXTURES / "logo.png")
    )
    assert report.stats["expansion_checked"] == 0, "a rejected candidate must not seed expansion"
    assert len(mock_http.calls) == 4


async def test_hidden_matches_do_not_seed_expansion(mock_http: respx.MockRouter, fixture_bytes: Bytes) -> None:
    mock_http.get("https://api.github.com/users/octocat").respond(200, json={"avatar_url": "https://cdn.example/o.png"})
    mock_http.get("https://cdn.example/o.png").respond(200, content=fixture_bytes("logo_resized.jpg"))
    options = ScanOptions(use_face=False, expand=True, min_confidence=99.9, candidates=["https://github.com/octocat"])
    report = await Scanner(make_settings(), options).run(str(FIXTURES / "logo.png"))
    assert report.matches() == []
    assert report.hidden_count() == 1
    assert report.stats["expansion_checked"] == 0


async def test_candidate_cap(mock_http: respx.MockRouter) -> None:
    mock_http.route().respond(404)
    hits = [Hit(f"https://github.com/user{i}", "fake", "page", "similar") for i in range(6)]
    hits.append(Hit("https://github.com/best", "fake", "page", "full"))
    scanner = Scanner(
        make_settings(max_candidates=3),
        ScanOptions(use_face=False, upload_consent=True),
        providers=[FakeProvider(hits)],
    )
    report = await scanner.run(str(FIXTURES / "logo.png"))
    assert len(report.results) == 3
    assert any("only the 3 strongest" in w for w in report.warnings)
    assert "github:best" in by_account(report), "exact matches are verified before merely similar ones"


async def test_unreadable_avatar_is_unverified_not_a_crash(mock_http: respx.MockRouter) -> None:
    mock_http.get("https://api.github.com/users/octocat").respond(200, json={"avatar_url": "https://cdn.example/o.svg"})
    mock_http.get("https://cdn.example/o.svg").respond(200, content=b"<svg xmlns='http://www.w3.org/2000/svg'/>")
    options = ScanOptions(use_face=False, candidates=["https://github.com/octocat"])
    report = await Scanner(make_settings(), options).run(str(FIXTURES / "logo.png"))
    (result,) = report.unverified()
    assert "could not be read" in (result.reason or "")


async def test_nothing_is_written_to_disk_by_default(
    mock_http: respx.MockRouter, fixture_bytes: Bytes, tmp_path: Path
) -> None:
    mock_http.get("https://api.github.com/users/octocat").respond(200, json={"avatar_url": "https://cdn.example/o.png"})
    mock_http.get("https://cdn.example/o.png").respond(200, content=fixture_bytes("logo.png"))
    cache_dir = tmp_path / "kept"
    settings = make_settings(cache_dir=cache_dir)
    options = ScanOptions(use_face=False, candidates=["https://github.com/octocat"])
    await Scanner(settings, options).run(str(FIXTURES / "logo.png"))
    assert not cache_dir.exists()
    assert not (tmp_path / "cache").exists()

    options.keep_cache = True
    report = await Scanner(settings, options).run(str(FIXTURES / "logo.png"))
    (run_dir,) = list(cache_dir.iterdir())
    assert (run_dir / "query.jpg").is_file()
    assert (run_dir / "report.json").is_file()
    assert [p.name for p in (run_dir / "avatars").iterdir()] == ["github____octocat.jpg"]
    assert any("--keep-cache" in w for w in report.warnings)
    assert (run_dir.stat().st_mode & 0o077) == 0, "kept pictures are readable by the owner only"


async def test_face_embeddings_are_kept_only_with_keep_cache(mock_http: respx.MockRouter, tmp_path: Path) -> None:
    settings = make_settings(cache_dir=tmp_path / "kept")
    options = ScanOptions(keep_cache=True)
    await Scanner(settings, options, face_engine=FakeFaces()).run(str(FIXTURES / "face_a.jpg"))
    (run_dir,) = list((tmp_path / "kept").iterdir())
    with np.load(run_dir / "query_embeddings.npz") as stored:
        assert stored["faces"].shape == (1, 3)


async def test_missing_face_extra_is_a_warning_not_an_error(
    mock_http: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("avatartrace.pipeline.face_available", lambda: False)
    monkeypatch.setattr("avatartrace.pipeline.clip_available", lambda: False)
    report = await Scanner(make_settings(), ScanOptions(use_clip=True)).run(str(FIXTURES / "face_a.jpg"))
    assert report.engines == {"face": "off", "clip": "off"}
    assert any("avatartrace[face]" in w for w in report.warnings)
    assert any("avatartrace[clip]" in w for w in report.warnings)
    assert report.query_faces is None


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("/definitely/not/here.jpg", "photo not found"),
        ("https://127.0.0.1/a.jpg", "public http"),
        ("http://localhost:8000/a.jpg", "public http"),
    ],
)
async def test_bad_input_raises_scan_error(mock_http: respx.MockRouter, source: str, message: str) -> None:
    with pytest.raises(ScanError, match=message):
        await Scanner(make_settings(), ScanOptions(use_face=False)).run(source)
    assert not mock_http.calls


async def test_unreadable_photo_raises_scan_error(mock_http: respx.MockRouter, tmp_path: Path) -> None:
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"this is not a picture")
    with pytest.raises(ScanError, match="could not read the photo"):
        await Scanner(make_settings(), ScanOptions(use_face=False)).run(str(broken))
    mock_http.get("https://img.example/gone.jpg").respond(404)
    with pytest.raises(ScanError, match="could not download the photo"):
        await Scanner(make_settings(), ScanOptions(use_face=False)).run("https://img.example/gone.jpg")
    with pytest.raises(ScanError, match="--image-url"):
        await Scanner(make_settings(), ScanOptions(use_face=False, public_url="http://10.0.0.1/a.jpg")).run(
            str(FIXTURES / "logo.png")
        )
