"""Discovery: turn the photo into candidate URLs through optional search providers."""

from __future__ import annotations

from urllib.parse import quote

from avatartrace.config import Settings
from avatartrace.discovery.base import Provider, ProviderError, QueryImage
from avatartrace.discovery.facecheck import FaceCheckProvider
from avatartrace.discovery.google_vision import GoogleVisionProvider
from avatartrace.discovery.saucenao import SauceNaoProvider
from avatartrace.discovery.serpapi import (
    SerpApiBing,
    SerpApiGoogleLens,
    SerpApiGoogleReverseImage,
    SerpApiYandex,
)
from avatartrace.discovery.tineye import TinEyeProvider

#: Every known provider, in the order they are run and listed.
PROVIDERS: tuple[type[Provider], ...] = (
    GoogleVisionProvider,
    SerpApiGoogleLens,
    SerpApiGoogleReverseImage,
    SerpApiYandex,
    SerpApiBing,
    TinEyeProvider,
    FaceCheckProvider,
    SauceNaoProvider,
)

PROVIDER_NAMES: tuple[str, ...] = tuple(p.name for p in PROVIDERS)


class UnknownProvider(ValueError):
    """A provider name passed to ``--providers`` does not exist."""


def parse_provider_selection(selection: str) -> list[str] | None:
    """Parse ``--providers``: ``all`` (-> ``None``), ``none`` (-> ``[]``) or a comma list."""
    names = [part.strip().lower() for part in selection.split(",") if part.strip()]
    if not names or names == ["all"]:
        return None
    if names == ["none"]:
        return []
    unknown = [n for n in names if n not in PROVIDER_NAMES]
    if unknown:
        raise UnknownProvider(
            f"unknown provider(s): {', '.join(unknown)}. Choose from: all, none, {', '.join(PROVIDER_NAMES)}"
        )
    return names


def configured_providers(settings: Settings, names: list[str] | None = None) -> list[Provider]:
    """Providers whose API key is set, optionally restricted to ``names``."""
    providers: list[Provider] = []
    for cls in PROVIDERS:
        if names is not None and cls.name not in names:
            continue
        provider = cls.from_settings(settings)
        if provider is not None:
            providers.append(provider)
    return providers


def manual_search_links(public_url: str | None) -> list[tuple[str, str]]:
    """Reverse-search pages the user can open by hand.

    With a public URL the links run the search directly. Without one they open
    each engine's upload page: avatartrace never uploads a local photo to a file
    host just to obtain a URL.
    """
    if public_url:
        encoded = quote(public_url, safe="")
        return [
            ("Google Lens", f"https://lens.google.com/uploadbyurl?url={encoded}"),
            ("Yandex Images", f"https://yandex.com/images/search?rpt=imageview&url={encoded}"),
            ("Bing Visual Search", f"https://www.bing.com/images/search?view=detailv2&iss=sbi&q=imgurl:{encoded}"),
            ("TinEye", f"https://tineye.com/search?url={encoded}"),
        ]
    return [
        ("Google Lens (upload the photo)", "https://lens.google.com/"),
        ("Yandex Images (upload the photo)", "https://yandex.com/images/"),
        ("Bing Visual Search (upload the photo)", "https://www.bing.com/visualsearch"),
        ("TinEye (upload the photo)", "https://tineye.com/"),
    ]


__all__ = [
    "PROVIDERS",
    "PROVIDER_NAMES",
    "Provider",
    "ProviderError",
    "QueryImage",
    "UnknownProvider",
    "configured_providers",
    "manual_search_links",
    "parse_provider_selection",
]
