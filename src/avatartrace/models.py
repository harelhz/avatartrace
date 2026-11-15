"""Plain data types shared by every stage of the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal


class MatchLabel(StrEnum):
    """Outcome of comparing a candidate's profile picture with the query photo."""

    EXACT = "EXACT"
    NEAR_DUPLICATE = "NEAR-DUPLICATE"
    LIKELY_SAME_FACE = "LIKELY SAME FACE"
    WEAK = "WEAK"
    NOT_A_MATCH = "NOT A MATCH"
    UNVERIFIED = "UNVERIFIED"

    @property
    def rank(self) -> int:
        """Sort order: strongest evidence first."""
        return _LABEL_RANK[self]


_LABEL_RANK: dict[MatchLabel, int] = {
    MatchLabel.EXACT: 0,
    MatchLabel.NEAR_DUPLICATE: 1,
    MatchLabel.LIKELY_SAME_FACE: 2,
    MatchLabel.WEAK: 3,
    MatchLabel.UNVERIFIED: 4,
    MatchLabel.NOT_A_MATCH: 5,
}

#: Labels that count as a verified match (used to seed username expansion).
STRONG_LABELS: frozenset[MatchLabel] = frozenset(
    {MatchLabel.EXACT, MatchLabel.NEAR_DUPLICATE, MatchLabel.LIKELY_SAME_FACE}
)

HitKind = Literal["page", "image"]
HitMatch = Literal["full", "partial", "similar", "face", "input", "manual", "unknown"]
AccountKind = Literal["profile", "content"]


@dataclass(frozen=True, slots=True)
class Hit:
    """One URL returned by a discovery provider."""

    url: str
    provider: str
    kind: HitKind = "page"
    match: HitMatch = "unknown"
    score: float | None = None
    title: str | None = None
    image_url: str | None = None


@dataclass(frozen=True, slots=True)
class Account:
    """A (platform, username) pair extracted from a URL.

    ``username`` is the canonical account name shown to the user (for the
    fediverse that is ``name@instance``); ``local`` is the bare name captured
    from the URL, which is what API lookups need.
    """

    platform: str
    platform_name: str
    username: str
    profile_url: str
    display: str
    local: str = ""
    kind: AccountKind = "profile"
    namespace: str = ""
    instance: str | None = None
    is_id: bool = False
    case_sensitive: bool = False

    @property
    def key(self) -> tuple[str, str, str]:
        """Identity used for de-duplication across providers."""
        name = self.username if self.case_sensitive else self.username.casefold()
        return (self.platform, self.namespace, name)


@dataclass(slots=True)
class Candidate:
    """An account plus the evidence trail that led to it."""

    account: Account
    found_via: list[str] = field(default_factory=list)
    source_urls: list[str] = field(default_factory=list)
    provider_score: float | None = None
    expanded_from: str | None = None

    def add_source(self, via: str, url: str | None, score: float | None = None) -> None:
        if via not in self.found_via:
            self.found_via.append(via)
        if url and url not in self.source_urls and len(self.source_urls) < 10:
            self.source_urls.append(url)
        if score is not None and (self.provider_score is None or score > self.provider_score):
            self.provider_score = score


@dataclass(frozen=True, slots=True)
class Comparison:
    """Result of comparing two images. ``confidence`` is on a 0-100 scale."""

    label: MatchLabel
    confidence: float
    method: str
    details: dict[str, float | int | str | None] = field(default_factory=dict)


@dataclass(slots=True)
class Result:
    """A candidate after verification. ``confidence`` is ``None`` when unverified."""

    account: Account
    label: MatchLabel
    confidence: float | None
    found_via: list[str]
    method: str | None = None
    details: dict[str, float | int | str | None] = field(default_factory=dict)
    avatar_url: str | None = None
    avatar_thumb: str | None = None
    reason: str | None = None
    source_urls: list[str] = field(default_factory=list)
    expanded_from: str | None = None

    @property
    def match_type(self) -> str:
        if self.label is MatchLabel.UNVERIFIED:
            via = ", ".join(self.found_via) or "unknown"
            return f"UNVERIFIED (found via {via})"
        return self.label.value

    def sort_key(self) -> tuple[int, float, str, str]:
        return (
            self.label.rank,
            -(self.confidence if self.confidence is not None else -1.0),
            self.account.platform,
            self.account.username.casefold(),
        )


@dataclass(slots=True)
class ProviderStatus:
    """What happened with one discovery provider during a scan."""

    name: str
    status: Literal["ok", "skipped", "error"]
    hits: int = 0
    message: str | None = None


@dataclass(slots=True)
class ScanReport:
    """Everything a scan produced; the reports are rendered from this."""

    query_source: str
    query_sha256: str
    query_thumb: str
    query_width: int
    query_height: int
    query_faces: int | None
    started_at: str
    finished_at: str
    min_confidence: float
    results: list[Result] = field(default_factory=list)
    providers: list[ProviderStatus] = field(default_factory=list)
    manual_links: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)
    engines: dict[str, str] = field(default_factory=dict)
    expanded: bool = False

    def matches(self, min_confidence: float | None = None) -> list[Result]:
        """Verified results at or above the confidence cut-off, strongest first."""
        cutoff = self.min_confidence if min_confidence is None else min_confidence
        rows = [
            r
            for r in self.results
            if r.confidence is not None
            and r.label not in (MatchLabel.UNVERIFIED, MatchLabel.NOT_A_MATCH)
            and r.confidence >= cutoff
        ]
        return sorted(rows, key=Result.sort_key)

    def unverified(self) -> list[Result]:
        rows = [r for r in self.results if r.label is MatchLabel.UNVERIFIED]
        return sorted(rows, key=Result.sort_key)

    def hidden_count(self, min_confidence: float | None = None) -> int:
        """Verified, non-rejected results that fall below the confidence cut-off."""
        cutoff = self.min_confidence if min_confidence is None else min_confidence
        return sum(
            1
            for r in self.results
            if r.confidence is not None
            and r.label not in (MatchLabel.UNVERIFIED, MatchLabel.NOT_A_MATCH)
            and r.confidence < cutoff
        )
