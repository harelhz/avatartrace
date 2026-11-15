"""HTML, JSON and CSV reports."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

from avatartrace.extraction import default_registry
from avatartrace.models import MatchLabel, ProviderStatus, Result, ScanReport
from avatartrace.report import (
    CSV_COLUMNS,
    ReportError,
    render_csv,
    render_html,
    render_json,
    report_format,
    safe_url,
    write_report,
)

REGISTRY = default_registry()
THUMB = "data:image/jpeg;base64,/9j/4AAQ"


def result(url: str, label: MatchLabel, confidence: float | None, **extra: object) -> Result:
    account = REGISTRY.extract(url)
    assert account is not None
    values: dict[str, object] = {"found_via": ["google_vision"], "method": "perceptual-hash"}
    values.update(extra)
    return Result(account=account, label=label, confidence=confidence, **values)  # type: ignore[arg-type]


def sample_report(**overrides: object) -> ScanReport:
    values: dict[str, object] = {
        "query_source": "photo.jpg",
        "query_sha256": "ab" * 32,
        "query_thumb": THUMB,
        "query_width": 400,
        "query_height": 400,
        "query_faces": 1,
        "started_at": "2026-01-01T10:00:00+00:00",
        "finished_at": "2026-01-01T10:00:09+00:00",
        "min_confidence": 80.0,
        "results": [
            result(
                "https://www.instagram.com/john.doe/",
                MatchLabel.EXACT,
                100.0,
                method="sha256",
                avatar_thumb=THUMB,
                avatar_url="https://cdn.example/a.jpg",
            ),
            result(
                "https://www.tiktok.com/@johnd",
                MatchLabel.NEAR_DUPLICATE,
                91.2,
                avatar_thumb=THUMB,
                details={"hash_distance": 3.7},
            ),
            result(
                "https://github.com/jdoe",
                MatchLabel.LIKELY_SAME_FACE,
                84.0,
                method="face",
                avatar_thumb=THUMB,
                details={"face_similarity": 0.52},
            ),
            result("https://t.me/johndoe", MatchLabel.WEAK, 61.0, avatar_thumb=THUMB),
            result("https://soundcloud.com/stranger", MatchLabel.NOT_A_MATCH, 12.0),
            result(
                "https://www.linkedin.com/in/john-doe-1/",
                MatchLabel.UNVERIFIED,
                None,
                method=None,
                reason="blocked (HTTP 999)",
                found_via=["serpapi_google_lens", "tineye"],
            ),
        ],
        "providers": [
            ProviderStatus("google_vision", "ok", hits=14),
            ProviderStatus("tineye", "error", message="quota"),
        ],
        "manual_links": [("Google Lens", "https://lens.google.com/")],
        "warnings": ["Face matching is off."],
        "stats": {"candidates": 6},
        "engines": {"face": "insightface:buffalo_l", "clip": "off"},
    }
    values.update(overrides)
    return ScanReport(**values)  # type: ignore[arg-type]


def test_matches_are_sorted_strongest_first() -> None:
    report = sample_report()
    assert [r.label for r in report.matches()] == [
        MatchLabel.EXACT,
        MatchLabel.NEAR_DUPLICATE,
        MatchLabel.LIKELY_SAME_FACE,
    ]
    assert report.hidden_count() == 1
    assert [r.label for r in report.matches(50)][-1] is MatchLabel.WEAK
    assert len(report.unverified()) == 1


def test_json_report_structure() -> None:
    data = json.loads(render_json(sample_report()))
    assert data["tool"] == "avatartrace"
    assert "leads, not proof" in data["disclaimer"]
    assert [m["platform"] for m in data["matches"]] == ["instagram", "tiktok", "github"]
    first = data["matches"][0]
    assert first == {
        "platform": "instagram",
        "platform_name": "Instagram",
        "username": "john.doe",
        "display": "@john.doe",
        "profile_url": "https://www.instagram.com/john.doe/",
        "label": "EXACT",
        "match_type": "EXACT",
        "confidence": 100.0,
        "found_via": ["google_vision"],
        "method": "sha256",
        "details": {},
        "avatar_url": "https://cdn.example/a.jpg",
        "reason": None,
        "source_urls": [],
        "expanded_from": None,
    }
    assert [m["label"] for m in data["below_min_confidence"]] == ["WEAK"]
    assert [m["platform"] for m in data["not_a_match"]] == ["soundcloud"]
    unverified = data["unverified"][0]
    assert unverified["confidence"] is None, "an unverified lead never gets a score"
    assert unverified["match_type"] == "UNVERIFIED (found via serpapi_google_lens, tineye)"
    assert unverified["reason"] == "blocked (HTTP 999)"
    assert data["query"]["faces_detected"] == 1
    assert "data:image" not in json.dumps(data), "the JSON report carries no pictures"


def test_csv_report() -> None:
    rows = list(csv.reader(io.StringIO(render_csv(sample_report()))))
    assert tuple(rows[0]) == CSV_COLUMNS
    assert rows[0][:6] == ["Platform", "Username", "Profile URL", "Match type", "Confidence", "Found via"]
    assert [r[0] for r in rows[1:]] == ["Instagram", "TikTok", "GitHub", "LinkedIn"]
    assert rows[1][3:5] == ["EXACT", "100.0"]
    assert rows[4][3] == "UNVERIFIED (found via serpapi_google_lens, tineye)"
    assert rows[4][4] == ""
    assert rows[4][8] == "blocked (HTTP 999)"


def test_csv_neutralises_spreadsheet_formulas() -> None:
    hostile = result(
        "https://github.com/jdoe",
        MatchLabel.EXACT,
        100.0,
        found_via=['=HYPERLINK("http://evil","x")'],
        avatar_url="@SUM(1+1)",
    )
    rows = list(csv.reader(io.StringIO(render_csv(sample_report(results=[hostile])))))
    assert rows[1][5] == '\'=HYPERLINK("http://evil","x")'
    assert rows[1][7] == "'@SUM(1+1)"


def test_html_report_shows_both_pictures_scores_and_links() -> None:
    html = render_html(sample_report())
    assert html.count(THUMB) >= 7, "query photo once in the header and beside each matched avatar"
    for text in ("100%", "91%", "84%", "EXACT", "NEAR-DUPLICATE", "LIKELY SAME FACE"):
        assert text in html
    assert 'href="https://www.instagram.com/john.doe/"' in html
    assert "blocked (HTTP 999)" in html
    assert "Leads, not proof" in html
    assert "1 weaker result(s)" in html
    assert "soundcloud.com/stranger" not in html
    assert "default-src 'none'" in html, "the report must not load anything from the network"
    assert "<script" not in html


def test_html_report_escapes_untrusted_text() -> None:
    hostile = result(
        "https://github.com/jdoe",
        MatchLabel.EXACT,
        100.0,
        found_via=['<script>alert("via")</script>'],
        avatar_thumb=THUMB,
    )
    unverified = result(
        "https://x.com/johnd", MatchLabel.UNVERIFIED, None, reason='"><img src=x onerror=alert(1)>', method=None
    )
    report = sample_report(
        results=[hostile, unverified],
        query_source='<img src=x onerror="alert(2)">',
        warnings=["<b>bold</b>"],
        providers=[ProviderStatus("tineye", "error", message="<script>1</script>")],
        manual_links=[("Evil", "javascript:alert(3)")],
    )
    html = render_html(report)
    assert "<script" not in html
    assert "<img src=x" not in html
    assert "&lt;script&gt;alert(&#34;via&#34;)&lt;/script&gt;" in html
    assert "javascript:alert" not in html
    assert "<b>bold</b>" not in html


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://example.com/a", "https://example.com/a"),
        ("HTTP://EXAMPLE.COM", "HTTP://EXAMPLE.COM"),
        ("javascript:alert(1)", ""),
        ("data:text/html,<script>", ""),
        ("//example.com", ""),
        ("", ""),
        (None, ""),
    ],
)
def test_safe_url(value: object, expected: str) -> None:
    assert safe_url(value) == expected


def test_empty_report_renders() -> None:
    report = sample_report(results=[], providers=[], warnings=[], manual_links=[], query_faces=None)
    assert "No account" in render_html(report)
    assert json.loads(render_json(report))["matches"] == []
    assert render_csv(report).strip() == ",".join(CSV_COLUMNS)


def test_write_report_picks_the_format_from_the_extension(tmp_path: Path) -> None:
    report = sample_report()
    assert write_report(report, tmp_path / "out" / "r.html").read_text(encoding="utf-8").startswith("<!doctype html>")
    assert json.loads(write_report(report, tmp_path / "r.JSON").read_text(encoding="utf-8"))["tool"] == "avatartrace"
    assert write_report(report, tmp_path / "r.csv").read_text(encoding="utf-8").startswith("Platform,")
    assert report_format(Path("x.htm")) == ".html"
    with pytest.raises(ReportError, match="unsupported report format"):
        write_report(report, tmp_path / "r.pdf")
    (tmp_path / "a-file").write_text("x", encoding="utf-8")
    with pytest.raises(ReportError, match="could not write"):
        write_report(report, tmp_path / "a-file" / "nested.html")
