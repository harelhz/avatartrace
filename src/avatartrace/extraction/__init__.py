"""URL -> account extraction."""

from avatartrace.extraction.registry import (
    AvatarSource,
    CdnMatch,
    Platform,
    PlatformDataError,
    PlatformRegistry,
    default_registry,
)

__all__ = [
    "AvatarSource",
    "CdnMatch",
    "Platform",
    "PlatformDataError",
    "PlatformRegistry",
    "default_registry",
]
