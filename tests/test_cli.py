"""The command-line interface, with the network mocked."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
import respx
from typer.testing import CliRunner

from avatartrace import __version__
from avatartrace.cli import app
from avatartrace.config import config_dir
from tests.conftest import FIXTURES

Bytes = Callable[[str], bytes]
runner = CliRunner()
LOGO = str(FIXTURES / "logo.png")


@pytest.fixture(autouse=True)
def fast_and_quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AVATARTRACE_PER_HOST_INTERVAL", "0")
    monkeypatch.setenv("COLUMNS", "200")


def mock_octocat(router: respx.MockRouter, data: Bytes, picture: str = "logo.png") -> None:
    router.get("https://api.github.com/users/octocat").respond(200, json={"avatar_url": "https://cdn.example/o.png"})
    router.get("https://cdn.example/o.png").respond(200, content=data(picture))


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == f"avatartrace {__version__}"


def test_help_lists_the_documented_options() -> None:
    result = runner.invoke(app, ["scan", "--help"])
    assert result.exit_code == 0
    for option in ("--expand", "--providers", "--min-confidence", "--report", "--i-understand-upload", "--keep-cache"):
        assert option in result.output


def test_parse_command() -> None:
    result = runner.invoke(app, ["parse", "https://twitter.com/JohnDoe", "https://example.com/x"])
    assert result.exit_code == 0
    assert "@johndoe" in result.output
    assert "not a profile" in result.output


def test_providers_and_platforms_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TINEYE_API_KEY", "super-secret-key")
    result = runner.invoke(app, ["providers"])
    assert result.exit_code == 0
    assert "tineye" in result.output
    assert "super-secret-key" not in result.output
    result = runner.invoke(app, ["platforms"])
    assert result.exit_code == 0
    assert "Instagram" in result.output
    assert "--expand checks" in result.output


def test_scan_without_keys_prints_manual_links_and_first_run_notice(mock_http: respx.MockRouter) -> None:
    result = runner.invoke(app, ["scan", LOGO, "--no-face"])
    assert result.exit_code == 0, result.output
    assert "Before you start" in result.output
    assert "Do not use it to stalk" in result.output
    assert "No search provider ran" in result.output
    assert "https://lens.google.com/" in result.output
    assert "Upload notice" not in result.output
    assert not mock_http.calls
    assert (config_dir() / "first-run-shown").is_file()

    again = runner.invoke(app, ["scan", LOGO, "--no-face"])
    assert "Before you start" not in again.output, "the long notice is shown on the first run only"


def test_scan_with_candidate_prints_the_results_table_and_writes_reports(
    mock_http: respx.MockRouter, fixture_bytes: Bytes, tmp_path: Path
) -> None:
    mock_octocat(mock_http, fixture_bytes)
    out = tmp_path / "reports"
    result = runner.invoke(
        app,
        ["scan", LOGO, "--no-face", "-c", "https://github.com/octocat", "--report", str(out / "r.html"),
         "--report", str(out / "r.json"), "--report", str(out / "r.csv")],
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    for column in ("Platform", "Username", "Profile URL", "Match type", "Confidence", "Found via"):
        assert column in result.output
    assert "https://github.com/octocat" in result.output
    assert "EXACT" in result.output
    assert "100%" in result.output
    assert json.loads((out / "r.json").read_text(encoding="utf-8"))["matches"][0]["username"] == "octocat"
    assert (out / "r.html").read_text(encoding="utf-8").startswith("<!doctype html>")
    assert "GitHub,octocat" in (out / "r.csv").read_text(encoding="utf-8")


def test_min_confidence_and_show_rejected(mock_http: respx.MockRouter, fixture_bytes: Bytes) -> None:
    mock_octocat(mock_http, fixture_bytes, picture="logo_other.png")
    result = runner.invoke(app, ["scan", LOGO, "--no-face", "-c", "https://github.com/octocat"])
    assert result.exit_code == 0
    assert "No account's profile picture matched at 80% confidence" in result.output
    assert "Not a match" not in result.output
    result = runner.invoke(app, ["scan", LOGO, "--no-face", "-c", "https://github.com/octocat", "--show-rejected"])
    assert "Not a match (1)" in result.output


def test_upload_requires_confirmation_when_not_interactive(
    mock_http: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TINEYE_API_KEY", "k")
    api = mock_http.post("https://api.tineye.com/rest/search/").respond(
        200, json={"code": 200, "results": {"matches": []}}
    )
    result = runner.invoke(app, ["scan", LOGO, "--no-face"])
    assert result.exit_code == 2
    assert "Upload notice" in result.output
    assert "TinEye: sends the photo itself to TinEye (api.tineye.com)" in result.output
    assert "--i-understand-upload" in result.output
    assert not api.called, "nothing is uploaded before the user agrees"

    result = runner.invoke(app, ["scan", LOGO, "--no-face", "--i-understand-upload"])
    assert result.exit_code == 0, result.output
    assert "Upload notice" in result.output
    assert api.call_count == 1

    result = runner.invoke(app, ["scan", LOGO, "--no-face", "--providers", "none"])
    assert result.exit_code == 0
    assert "Upload notice" not in result.output
    assert api.call_count == 1


def test_interactive_decline_keeps_the_photo_local(
    mock_http: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TINEYE_API_KEY", "k")
    monkeypatch.setattr("avatartrace.cli._interactive", lambda: True)
    api = mock_http.post("https://api.tineye.com/rest/search/").respond(
        200, json={"code": 200, "results": {"matches": []}}
    )
    declined = runner.invoke(app, ["scan", LOGO, "--no-face"], input="n\n")
    assert declined.exit_code == 0, declined.output
    assert "Upload declined" in declined.output
    assert not api.called
    accepted = runner.invoke(app, ["scan", LOGO, "--no-face"], input="y\n")
    assert accepted.exit_code == 0
    assert api.call_count == 1


@pytest.mark.parametrize(
    ("arguments", "code", "message"),
    [
        (["scan", "/no/such/photo.jpg", "--no-face"], 1, "photo not found"),
        (["scan", LOGO, "--report", "out.pdf"], 2, "unsupported report format"),
        (["scan", LOGO, "--providers", "pimeyes"], 2, "unknown provider"),
        (["scan", LOGO, "--min-confidence", "150"], 2, ""),
    ],
)
def test_usage_errors(mock_http: respx.MockRouter, arguments: list[str], code: int, message: str) -> None:
    result = runner.invoke(app, arguments)
    assert result.exit_code == code
    assert message in result.output
    assert not mock_http.calls


def test_invalid_configuration_is_reported_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AVATARTRACE_THRESHOLDS__FACE_SAME_MIN", "0.1")
    monkeypatch.setenv("AVATARTRACE_THRESHOLDS__FACE_WEAK_MIN", "0.9")
    result = runner.invoke(app, ["scan", LOGO])
    assert result.exit_code == 2
    assert "invalid configuration" in result.output
    assert "Traceback" not in result.output


def test_compare_command() -> None:
    result = runner.invoke(app, ["compare", LOGO, str(FIXTURES / "logo_resized.jpg"), "--no-face"])
    assert result.exit_code == 0, result.output
    assert "NEAR-DUPLICATE" in result.output
    result = runner.invoke(app, ["compare", LOGO, str(FIXTURES / "make_fixtures.py"), "--no-face"])
    assert result.exit_code == 1


def test_clear_cache_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = tmp_path / "kept"
    (cache / "run1" / "avatars").mkdir(parents=True)
    (cache / "run1" / "query.jpg").write_bytes(b"x")
    monkeypatch.setenv("AVATARTRACE_CACHE_DIR", str(cache))
    result = runner.invoke(app, ["clear-cache"])
    assert result.exit_code == 0
    assert "Removed 1 cached run" in result.output
    assert list(cache.iterdir()) == []
