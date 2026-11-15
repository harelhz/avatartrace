"""Shared test fixtures. No test in the default run touches the real network."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
import respx
from PIL import Image

from avatartrace.config import Settings
from avatartrace.imaging import decode_image

# Rich reads these when a Console is created, which happens when avatartrace.cli is imported.
# Setting them here, before any test module is imported, keeps CLI output free of colour codes.
os.environ.pop("FORCE_COLOR", None)
os.environ["NO_COLOR"] = "1"

FIXTURES = Path(__file__).parent / "fixtures"

_KEY_VARIABLES = (
    "GOOGLE_VISION_API_KEY",
    "SERPAPI_API_KEY",
    "TINEYE_API_KEY",
    "FACECHECK_API_TOKEN",
    "SAUCENAO_API_KEY",
    "GITHUB_TOKEN",
    "YOUTUBE_API_KEY",
    "TWITCH_CLIENT_ID",
    "TWITCH_CLIENT_SECRET",
    "X_BEARER_TOKEN",
    "REDDIT_CLIENT_ID",
    "REDDIT_CLIENT_SECRET",
)


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No API keys, no user config, no ``.env`` from the developer's machine."""
    for name in list(os.environ):
        if name.startswith("AVATARTRACE_") or name in _KEY_VARIABLES:
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("AVATARTRACE_CHECK_DNS", "false")  # tests never touch real DNS
    monkeypatch.chdir(tmp_path)


def make_settings(**overrides: object) -> Settings:
    """Settings that ignore ``.env`` files and do not wait between requests."""
    values: dict[str, object] = {"per_host_interval": 0.0, "facecheck_poll_interval": 0.0, "check_dns": False}
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def fixture_path() -> Callable[[str], Path]:
    return lambda name: FIXTURES / name


@pytest.fixture
def fixture_bytes() -> Callable[[str], bytes]:
    return lambda name: (FIXTURES / name).read_bytes()


@pytest.fixture
def fixture_image() -> Callable[[str], Image.Image]:
    return lambda name: decode_image((FIXTURES / name).read_bytes())


@pytest.fixture
def mock_http() -> Iterator[respx.MockRouter]:
    """Intercept every HTTP request; anything not explicitly mocked fails the test."""
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        yield router
