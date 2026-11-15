"""Settings: API keys, network politeness and match thresholds.

Everything is read from environment variables and an optional ``.env`` file.
Provider keys may be given with or without the ``AVATARTRACE_`` prefix
(``SERPAPI_API_KEY`` and ``AVATARTRACE_SERPAPI_API_KEY`` both work); every
other option uses the prefix. Nested values use a double underscore, e.g.
``AVATARTRACE_THRESHOLDS__FACE_SAME_MIN=0.5``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Self

from pydantic import AliasChoices, BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from avatartrace import REPO_URL, __version__

DEFAULT_USER_AGENT = f"avatartrace/{__version__} (+{REPO_URL})"


def config_dir() -> Path:
    """Directory for the user-level ``.env`` and the first-run marker."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "avatartrace"


def default_cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "avatartrace"


class Thresholds(BaseModel):
    """Decision thresholds. See docs/thresholds.md for how they were calibrated.

    Hash values are a weighted Hamming distance in bits over four 64-bit
    perceptual hashes (0 = identical, ~32 = unrelated). Face and CLIP values are
    cosine similarities (1.0 = identical).
    """

    hash_near_max: float = Field(default=8.0, ge=0, le=64)
    hash_weak_max: float = Field(default=12.0, ge=0, le=64)
    face_same_min: float = Field(default=0.45, ge=-1, le=1)
    face_weak_min: float = Field(default=0.32, ge=-1, le=1)
    clip_near_min: float = Field(default=0.95, ge=-1, le=1)
    clip_weak_min: float = Field(default=0.90, ge=-1, le=1)

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.hash_near_max > self.hash_weak_max:
            raise ValueError("hash_near_max must not exceed hash_weak_max")
        if self.face_weak_min > self.face_same_min:
            raise ValueError("face_weak_min must not exceed face_same_min")
        if self.clip_weak_min > self.clip_near_min:
            raise ValueError("clip_weak_min must not exceed clip_near_min")
        return self


def _key(name: str) -> Any:
    """A secret that may be set as ``AVATARTRACE_<NAME>`` or plain ``<NAME>``."""
    return Field(default=None, validation_alias=AliasChoices(f"AVATARTRACE_{name}", name))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AVATARTRACE_",
        env_nested_delimiter="__",
        env_file=(str(config_dir() / ".env"), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    # --- discovery providers (each one is enabled by setting its key) ---
    google_vision_api_key: SecretStr | None = _key("GOOGLE_VISION_API_KEY")
    serpapi_api_key: SecretStr | None = _key("SERPAPI_API_KEY")
    tineye_api_key: SecretStr | None = _key("TINEYE_API_KEY")
    facecheck_api_token: SecretStr | None = _key("FACECHECK_API_TOKEN")
    saucenao_api_key: SecretStr | None = _key("SAUCENAO_API_KEY")

    # --- optional keys for official profile APIs (raise limits / unlock avatars) ---
    github_token: SecretStr | None = _key("GITHUB_TOKEN")
    youtube_api_key: SecretStr | None = _key("YOUTUBE_API_KEY")
    twitch_client_id: SecretStr | None = _key("TWITCH_CLIENT_ID")
    twitch_client_secret: SecretStr | None = _key("TWITCH_CLIENT_SECRET")
    x_bearer_token: SecretStr | None = _key("X_BEARER_TOKEN")
    reddit_client_id: SecretStr | None = _key("REDDIT_CLIENT_ID")
    reddit_client_secret: SecretStr | None = _key("REDDIT_CLIENT_SECRET")

    # --- provider tuning ---
    google_vision_max_results: int = Field(default=100, ge=1, le=500)
    serpapi_engines: list[str] = Field(
        default_factory=lambda: ["google_lens", "google_reverse_image", "yandex_images", "bing_reverse_image"]
    )
    tineye_limit: int = Field(default=100, ge=1, le=500)
    facecheck_demo: bool = False
    facecheck_min_score: int = Field(default=70, ge=0, le=100)
    facecheck_timeout: float = Field(default=600.0, gt=0)
    facecheck_poll_interval: float = Field(default=1.5, ge=0)
    saucenao_min_similarity: float = Field(default=60.0, ge=0, le=100)

    # --- network politeness ---
    user_agent: str = DEFAULT_USER_AGENT
    request_timeout: float = Field(default=20.0, gt=0)
    per_host_interval: float = Field(default=1.0, ge=0)
    max_concurrency: int = Field(default=8, ge=1, le=64)
    max_candidates: int = Field(default=150, ge=1)
    max_expansion_seeds: int = Field(default=5, ge=1, le=50)
    max_image_bytes: int = Field(default=15_000_000, ge=1024)
    max_html_bytes: int = Field(default=5_000_000, ge=1024)
    #: Resolve host names before connecting and refuse those that point at private addresses.
    check_dns: bool = True

    # --- comparison engines ---
    face_model: str = "buffalo_l"
    face_det_size: int = Field(default=640, ge=160, le=1280)
    face_max_faces: int = Field(default=5, ge=1, le=20)
    clip_model: str = "Qdrant/clip-ViT-B-32-vision"
    thresholds: Thresholds = Field(default_factory=Thresholds)

    # --- extraction ---
    include_content_urls: bool = True
    cache_dir: Path | None = None

    def secret(self, name: str) -> str | None:
        """Plain value of a secret field, or ``None`` when unset or blank."""
        value = getattr(self, name)
        if value is None:
            return None
        text = value.get_secret_value().strip() if isinstance(value, SecretStr) else str(value).strip()
        return text or None


def load_settings(**overrides: Any) -> Settings:
    """Build settings from the environment and ``.env`` files; ``overrides`` win over everything.

    The ``.env`` locations are worked out now rather than at import time, so the
    user-level file follows ``XDG_CONFIG_HOME``. Later files override earlier ones,
    and real environment variables override both.
    """
    env_files = (str(config_dir() / ".env"), ".env")
    return Settings(_env_file=env_files, **overrides)
