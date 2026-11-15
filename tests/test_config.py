"""Settings: environment variables, ``.env`` files and defaults."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from avatartrace import __version__
from avatartrace.config import DEFAULT_USER_AGENT, Settings, Thresholds, config_dir, load_settings


def test_defaults_are_polite_and_keyless() -> None:
    settings = load_settings()
    assert settings.per_host_interval == 1.0
    assert settings.max_concurrency == 8
    assert settings.user_agent == DEFAULT_USER_AGENT
    assert __version__ in settings.user_agent
    assert "Mozilla" not in settings.user_agent
    for name in (
        "google_vision_api_key",
        "serpapi_api_key",
        "tineye_api_key",
        "facecheck_api_token",
        "saucenao_api_key",
    ):
        assert settings.secret(name) is None
    assert settings.facecheck_demo is False
    assert settings.thresholds == Thresholds()


@pytest.mark.parametrize("variable", ["SERPAPI_API_KEY", "AVATARTRACE_SERPAPI_API_KEY"])
def test_keys_work_with_and_without_the_prefix(monkeypatch: pytest.MonkeyPatch, variable: str) -> None:
    monkeypatch.setenv(variable, "abc123")
    assert load_settings().secret("serpapi_api_key") == "abc123"


def test_env_file_in_the_working_directory_is_read(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("TINEYE_API_KEY=from-dotenv\nAVATARTRACE_MAX_CONCURRENCY=3\n", encoding="utf-8")
    settings = load_settings()
    assert settings.secret("tineye_api_key") == "from-dotenv"
    assert settings.max_concurrency == 3


def test_user_level_env_file_and_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_dir().mkdir(parents=True)
    (config_dir() / ".env").write_text("TINEYE_API_KEY=user-level\nSAUCENAO_API_KEY=user-sauce\n", encoding="utf-8")
    assert load_settings().secret("tineye_api_key") == "user-level"
    (tmp_path / ".env").write_text("TINEYE_API_KEY=project-level\n", encoding="utf-8")
    settings = load_settings()
    assert settings.secret("tineye_api_key") == "project-level", "the project .env overrides the user-level one"
    assert settings.secret("saucenao_api_key") == "user-sauce"
    monkeypatch.setenv("TINEYE_API_KEY", "from-environment")
    assert load_settings().secret("tineye_api_key") == "from-environment", "real environment variables win"
    assert Settings(_env_file=None).secret("saucenao_api_key") is None


def test_dns_check_is_on_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AVATARTRACE_CHECK_DNS")
    assert load_settings().check_dns is True


def test_nested_threshold_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AVATARTRACE_THRESHOLDS__FACE_SAME_MIN", "0.6")
    monkeypatch.setenv("AVATARTRACE_THRESHOLDS__HASH_NEAR_MAX", "5")
    thresholds = load_settings().thresholds
    assert thresholds.face_same_min == 0.6
    assert thresholds.hash_near_max == 5.0
    assert thresholds.hash_weak_max == Thresholds().hash_weak_max


def test_list_and_bool_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AVATARTRACE_SERPAPI_ENGINES", '["google_lens","yandex_images"]')
    monkeypatch.setenv("AVATARTRACE_FACECHECK_DEMO", "true")
    settings = load_settings()
    assert settings.serpapi_engines == ["google_lens", "yandex_images"]
    assert settings.facecheck_demo is True


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("AVATARTRACE_MAX_CONCURRENCY", "0"),
        ("AVATARTRACE_REQUEST_TIMEOUT", "-1"),
        ("AVATARTRACE_PER_HOST_INTERVAL", "-0.5"),
        ("AVATARTRACE_THRESHOLDS__HASH_NEAR_MAX", "99"),
        ("AVATARTRACE_FACECHECK_MIN_SCORE", "101"),
    ],
)
def test_out_of_range_values_are_rejected(monkeypatch: pytest.MonkeyPatch, variable: str, value: str) -> None:
    monkeypatch.setenv(variable, value)
    with pytest.raises(ValidationError):
        load_settings()


def test_unknown_variables_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AVATARTRACE_NOT_A_SETTING", "1")
    load_settings()
