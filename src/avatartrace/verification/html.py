"""Find a profile picture in a public profile page, using only the standard library."""

from __future__ import annotations

import html as htmllib
import json
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin

_META_KEYS = ("og:image:secure_url", "og:image", "og:image:url", "twitter:image", "twitter:image:src")
_PROFILE_TYPES = ("Person", "Organization", "MusicGroup", "Brand", "Corporation", "LocalBusiness")


class _HeadParser(HTMLParser):
    """Collects <meta> image tags, <link rel=image_src> and JSON-LD blocks."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.image_src: str | None = None
        self.ld_json: list[str] = []
        self._in_ld = False
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            key = (values.get("property") or values.get("name") or "").strip().lower()
            content = values.get("content", "").strip()
            if key in _META_KEYS and content and key not in self.meta:
                self.meta[key] = content
        elif tag == "link":
            if values.get("rel", "").strip().lower() == "image_src" and values.get("href") and not self.image_src:
                self.image_src = values["href"].strip()
        elif tag == "script" and values.get("type", "").strip().lower() == "application/ld+json":
            self._in_ld = True
            self._buffer = []

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._in_ld:
            self._in_ld = False
            self.ld_json.append("".join(self._buffer))

    def handle_data(self, data: str) -> None:
        if self._in_ld:
            self._buffer.append(data)


def _parse(text: str) -> _HeadParser:
    parser = _HeadParser()
    try:
        parser.feed(text)
        parser.close()
    except (AssertionError, ValueError):  # html.parser can raise on badly broken markup
        pass
    return parser


def og_image(text: str, base_url: str) -> str | None:
    """The page's Open Graph / Twitter-card image, as an absolute URL."""
    parsed = _parse(text)
    for key in _META_KEYS:
        value = parsed.meta.get(key)
        if value:
            return urljoin(base_url, value)
    if parsed.image_src:
        return urljoin(base_url, parsed.image_src)
    return None


def ld_json_image(text: str, base_url: str) -> str | None:
    """The image of the schema.org ProfilePage / Person described by the page."""
    documents: list[Any] = []
    for block in _parse(text).ld_json:
        try:
            documents.append(json.loads(block))
        except ValueError:
            continue
    nodes: list[dict[str, Any]] = []
    for document in documents:
        items = document if isinstance(document, list) else [document]
        for item in items:
            if isinstance(item, dict):
                nodes.append(item)
                graph = item.get("@graph")
                if isinstance(graph, list):
                    nodes.extend(n for n in graph if isinstance(n, dict))
    for node in nodes:
        if _has_type(node, ("ProfilePage",)):
            entity = node.get("mainEntity")
            if isinstance(entity, dict):
                url = _image_of(entity)
                if url:
                    return urljoin(base_url, url)
    for node in nodes:
        if _has_type(node, _PROFILE_TYPES):
            url = _image_of(node)
            if url:
                return urljoin(base_url, url)
    return None


def _has_type(node: dict[str, Any], names: tuple[str, ...]) -> bool:
    value = node.get("@type")
    types = value if isinstance(value, list) else [value]
    return any(isinstance(t, str) and t in names for t in types)


def _image_of(node: dict[str, Any]) -> str | None:
    image = node.get("image")
    if isinstance(image, list):
        image = image[0] if image else None
    if isinstance(image, dict):
        image = image.get("contentUrl") or image.get("url")
    return image if isinstance(image, str) and image else None


def regex_image(text: str, pattern: re.Pattern[str], base_url: str) -> str | None:
    """First capture group of ``pattern``, with JSON and HTML escaping undone."""
    found = pattern.search(text)
    if not found or not found.groups():
        return None
    value = found.group(1)
    if not value:
        return None
    if "\\" in value:
        try:
            decoded = json.loads(f'"{value}"')
        except ValueError:
            decoded = value.replace("\\/", "/")
        if isinstance(decoded, str):
            value = decoded
    return urljoin(base_url, htmllib.unescape(value).strip())
