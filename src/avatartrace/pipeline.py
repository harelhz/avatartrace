"""The scan pipeline: discovery -> extraction -> verification -> expansion.

Privacy properties enforced here:

* nothing is sent to a search provider unless ``ScanOptions.upload_consent`` is true;
* fetched pictures and face/CLIP embeddings live in memory only and are dropped
  when the scan ends, unless ``keep_cache`` asks for them to be written to disk.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx
from PIL import Image

from avatartrace.cache import RunCache
from avatartrace.compare import (
    ClipEngine,
    Comparator,
    EngineUnavailable,
    FaceEngine,
    FastEmbedClipEngine,
    ImageFeatures,
    InsightFaceEngine,
    clip_available,
    face_available,
)
from avatartrace.config import Settings
from avatartrace.discovery import (
    Provider,
    ProviderError,
    QueryImage,
    configured_providers,
    manual_search_links,
)
from avatartrace.extraction import PlatformRegistry, default_registry
from avatartrace.extraction.cdn import CdnResolver
from avatartrace.imaging import ImageError, decode_image, thumbnail_data_uri, to_upload_jpeg
from avatartrace.models import (
    STRONG_LABELS,
    Account,
    Candidate,
    Hit,
    MatchLabel,
    ProviderStatus,
    Result,
    ScanReport,
)
from avatartrace.net import FetchError, HttpClient, is_public_url
from avatartrace.verification import AvatarFetcher

Progress = Callable[[str], None]

_URL = re.compile(r"^https?://", re.IGNORECASE)
_MATCH_PRIORITY = {"input": 0, "manual": 0, "full": 1, "face": 2, "partial": 3, "similar": 4, "unknown": 5}
_MAX_FILE_BYTES = 40_000_000


class ScanError(Exception):
    """The scan could not start (bad input). The message is safe to show."""


@dataclass(slots=True)
class ScanOptions:
    expand: bool = False
    providers: list[str] | None = None
    min_confidence: float = 80.0
    candidates: list[str] = field(default_factory=list)
    public_url: str | None = None
    use_face: bool = True
    use_clip: bool = False
    keep_cache: bool = False
    upload_consent: bool = False


def is_url(source: str) -> bool:
    return bool(_URL.match(source.strip()))


def planned_uploads(source: str, options: ScanOptions, settings: Settings) -> list[Provider]:
    """Providers that would receive the photo (or its URL) for this scan."""
    has_public_url = is_url(source) or bool(options.public_url)
    stub = QueryImage(
        source=source,
        image=Image.new("RGB", (8, 8)),
        upload_jpeg=b"",
        public_url="https://placeholder.invalid/" if has_public_url else None,
    )
    return [
        provider
        for provider in configured_providers(settings, options.providers)
        if provider.unavailable_reason(stub) is None
    ]


def _read_photo_file(source: str) -> bytes:
    path = Path(source).expanduser()
    if not path.is_file():
        raise ScanError(f"photo not found: {source}")
    try:
        if path.stat().st_size > _MAX_FILE_BYTES:
            raise ScanError("the photo file is larger than 40 MB")
        return path.read_bytes()
    except OSError as exc:
        raise ScanError(f"could not read the photo: {exc.strerror or exc}") from exc


async def load_query(source: str, options: ScanOptions, http: HttpClient) -> QueryImage:
    """Read the photo from a file or URL and prepare the sanitised upload copy."""
    source = source.strip()
    public_url = options.public_url
    if is_url(source):
        if not is_public_url(source):
            raise ScanError("the photo URL must be a public http(s) address")
        try:
            data = (await http.get_image(source)).content
        except FetchError as exc:
            raise ScanError(f"could not download the photo: {exc.reason}") from exc
        public_url = public_url or source
    else:
        data = await asyncio.to_thread(_read_photo_file, source)
    if public_url and not is_public_url(public_url):
        raise ScanError("--image-url must be a public http(s) address")
    try:
        image = decode_image(data)
    except ImageError as exc:
        raise ScanError(f"could not read the photo: {exc}") from exc
    return QueryImage(source=source, image=image, upload_jpeg=to_upload_jpeg(image), public_url=public_url)


class Scanner:
    """One scan. Create it, ``await run()``, read the :class:`ScanReport`."""

    def __init__(
        self,
        settings: Settings,
        options: ScanOptions,
        *,
        registry: PlatformRegistry | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        face_engine: FaceEngine | None = None,
        clip_engine: ClipEngine | None = None,
        providers: list[Provider] | None = None,
        progress: Progress | None = None,
    ) -> None:
        self.settings = settings
        self.options = options
        self.registry = registry or default_registry()
        self._transport = transport
        self._face_engine = face_engine
        self._clip_engine = clip_engine
        self._providers = providers
        self._progress = progress or (lambda _message: None)
        self._cpu = asyncio.Lock()
        self._warnings: list[str] = []
        self._stats: dict[str, int] = {}

    # ------------------------------------------------------------------ engines

    async def _engines(self) -> tuple[FaceEngine | None, ClipEngine | None]:
        face = self._face_engine
        clip = self._clip_engine
        if face is None and self.options.use_face:
            if face_available():
                engine = InsightFaceEngine(
                    self.settings.face_model, self.settings.face_det_size, self.settings.face_max_faces
                )
                self._progress("Loading the face model (first run downloads it)")
                try:
                    await asyncio.to_thread(engine.warm_up)
                    face = engine
                except EngineUnavailable as exc:
                    self._warnings.append(f"Face matching disabled: {exc}")
            else:
                self._warnings.append(
                    "Face matching is off (install it with: pip install 'avatartrace[face]'). "
                    "Only identical and near-duplicate pictures can be confirmed."
                )
        if clip is None and self.options.use_clip:
            if clip_available():
                clip_engine = FastEmbedClipEngine(self.settings.clip_model)
                self._progress("Loading the CLIP model (first run downloads it)")
                try:
                    await asyncio.to_thread(clip_engine.warm_up)
                    clip = clip_engine
                except EngineUnavailable as exc:
                    self._warnings.append(f"CLIP similarity disabled: {exc}")
            else:
                self._warnings.append("CLIP similarity is off (install it with: pip install 'avatartrace[clip]').")
        return face, clip

    # ------------------------------------------------------------------ run

    async def run(self, source: str) -> ScanReport:
        started = datetime.now(UTC)
        async with HttpClient(
            self.settings, rate_overrides=self.registry.rate_overrides(), transport=self._transport
        ) as http:
            query = await load_query(source, self.options, http)
            face, clip = await self._engines()
            comparator = Comparator(self.settings.thresholds, face, clip)
            try:
                async with self._cpu:
                    features = await asyncio.to_thread(comparator.query_features, query.image)
            except EngineUnavailable as exc:
                self._warnings.append(f"Embedding models disabled: {exc}")
                comparator = Comparator(self.settings.thresholds)
                features = comparator.query_features(query.image)
            self._warnings.extend(features.notes)
            if face is not None and not features.faces:
                self._warnings.append(
                    "No face was found in the photo, so only identical and near-duplicate pictures can be confirmed"
                    + (" (plus CLIP similarity)." if clip is not None else ".")
                )

            cache = RunCache.create(self.settings, features.sha256, started) if self.options.keep_cache else None
            if cache is not None:
                cache.save_query(query.image)

            statuses, hits = await self._discover(query, http)
            if is_url(query.source):
                hits.insert(0, Hit(url=query.source, provider="input URL", kind="image", match="input"))
                hits.insert(0, Hit(url=query.source, provider="input URL", kind="page", match="input"))
            for url in self.options.candidates:
                hits.append(Hit(url=url, provider="manual", kind="page", match="manual"))
            self._stats["hits"] = len(hits)

            candidates = await self._extract(hits, http)
            self._stats["candidates"] = len(candidates)

            fetcher = AvatarFetcher(self.registry, http, self.settings)
            self._progress(f"Verifying {len(candidates)} candidate account(s)")
            results = await self._verify_all(candidates, fetcher, comparator, features, cache, expansion=False)

            if self.options.expand:
                expansion = self._expansion_candidates(results, {c.account.key for c in candidates})
                if expansion:
                    self._progress(f"Checking the same username on {len(expansion)} more site(s)")
                    results.extend(
                        await self._verify_all(expansion, fetcher, comparator, features, cache, expansion=True)
                    )
                self._stats["expansion_checked"] = len(expansion)

            if cache is not None:
                cache.save_embeddings(features)
            self._stats["http_requests"] = http.request_count

        report = ScanReport(
            # Reports get shared: show the file name, not a path that reveals the local user and folders.
            query_source=query.source if is_url(query.source) else Path(query.source).name,
            query_sha256=features.sha256,
            query_thumb=thumbnail_data_uri(query.image),
            query_width=features.width,
            query_height=features.height,
            query_faces=len(features.faces) if features.faces is not None else None,
            started_at=started.isoformat(timespec="seconds"),
            finished_at=datetime.now(UTC).isoformat(timespec="seconds"),
            min_confidence=self.options.min_confidence,
            results=sorted(results, key=Result.sort_key),
            providers=statuses,
            manual_links=manual_search_links(query.public_url),
            warnings=self._warnings,
            stats=self._stats,
            engines={
                "face": face.name if face is not None else "off",
                "clip": clip.name if clip is not None else "off",
            },
            expanded=self.options.expand,
        )
        features.clear_embeddings()
        if cache is not None:
            cache.save_report(report)
            report.warnings.append(f"--keep-cache: pictures and embeddings were kept in {cache.path}")
        return report

    # ------------------------------------------------------------------ discovery

    async def _discover(self, query: QueryImage, http: HttpClient) -> tuple[list[ProviderStatus], list[Hit]]:
        providers = (
            self._providers
            if self._providers is not None
            else configured_providers(self.settings, self.options.providers)
        )
        statuses: list[ProviderStatus] = []
        runnable: list[Provider] = []
        for provider in providers:
            reason = provider.unavailable_reason(query)
            if reason:
                statuses.append(ProviderStatus(provider.name, "skipped", message=reason))
            elif not self.options.upload_consent:
                statuses.append(
                    ProviderStatus(provider.name, "skipped", message="upload not confirmed (--i-understand-upload)")
                )
            else:
                runnable.append(provider)
        if runnable:
            self._progress(f"Asking {len(runnable)} search provider(s)")
        outcomes = await asyncio.gather(*(self._run_provider(p, query, http) for p in runnable))
        hits: list[Hit] = []
        for status, found in outcomes:
            statuses.append(status)
            hits.extend(found)
        return statuses, hits

    @staticmethod
    async def _run_provider(
        provider: Provider, query: QueryImage, http: HttpClient
    ) -> tuple[ProviderStatus, list[Hit]]:
        try:
            hits = await provider.search(query, http)
        except ProviderError as exc:
            return ProviderStatus(provider.name, "error", message=str(exc)), []
        except Exception as exc:  # a provider bug must not take the whole scan down
            return ProviderStatus(provider.name, "error", message=f"unexpected {type(exc).__name__}"), []
        return ProviderStatus(provider.name, "ok", hits=len(hits)), hits

    # ------------------------------------------------------------------ extraction

    async def _extract(self, hits: list[Hit], http: HttpClient) -> list[Candidate]:
        resolver = CdnResolver(self.registry, http, self.settings)
        include_content = self.settings.include_content_urls
        found: dict[tuple[str, str, str], Candidate] = {}
        order: dict[tuple[str, str, str], int] = {}

        def add(account: Account, hit: Hit, note: str = "") -> None:
            via = hit.provider + note
            existing = found.get(account.key)
            if existing is None:
                existing = found[account.key] = Candidate(account=account)
                order[account.key] = _MATCH_PRIORITY.get(hit.match, 5)
            elif existing.account.kind == "content" and account.kind == "profile":
                existing.account = account
            order[account.key] = min(order[account.key], _MATCH_PRIORITY.get(hit.match, 5))
            existing.add_source(via, hit.url, hit.score)

        for hit in hits:
            if hit.kind == "page":
                account = self.registry.extract(hit.url, include_content=include_content)
                if account is not None:
                    add(account, hit, " (post URL)" if account.kind == "content" else "")
            image_urls = [hit.url] if hit.kind == "image" else []
            if hit.image_url:
                image_urls.append(hit.image_url)
            for image_url in image_urls:
                account = await resolver.resolve(image_url)
                if account is not None:
                    add(account, hit, " (avatar CDN)")

        ranked = sorted(
            found.values(),
            key=lambda c: (order[c.account.key], -(c.provider_score or 0.0)),
        )
        limit = self.settings.max_candidates
        if len(ranked) > limit:
            self._warnings.append(
                f"{len(ranked)} candidate accounts were found; only the {limit} strongest were verified "
                "(raise AVATARTRACE_MAX_CANDIDATES to check more)."
            )
            ranked = ranked[:limit]
        return ranked

    # ------------------------------------------------------------------ verification

    async def _verify_all(
        self,
        candidates: list[Candidate],
        fetcher: AvatarFetcher,
        comparator: Comparator,
        query: ImageFeatures,
        cache: RunCache | None,
        *,
        expansion: bool,
    ) -> list[Result]:
        outcomes = await asyncio.gather(
            *(self._verify(c, fetcher, comparator, query, cache, expansion=expansion) for c in candidates)
        )
        return [result for result in outcomes if result is not None]

    def _count(self, key: str) -> None:
        self._stats[key] = self._stats.get(key, 0) + 1

    async def _verify(
        self,
        candidate: Candidate,
        fetcher: AvatarFetcher,
        comparator: Comparator,
        query: ImageFeatures,
        cache: RunCache | None,
        *,
        expansion: bool,
    ) -> Result | None:
        account = candidate.account

        def unverified(reason: str) -> Result | None:
            if expansion:
                # A username alone is not evidence: guesses we cannot compare are not reported.
                self._count("expansion_not_comparable")
                return None
            self._count("unverified")
            return Result(
                account=account,
                label=MatchLabel.UNVERIFIED,
                confidence=None,
                found_via=list(candidate.found_via),
                reason=reason,
                source_urls=list(candidate.source_urls),
            )

        try:
            avatar = await fetcher.fetch(account)
        except Exception as exc:  # never let one site abort the scan
            return unverified(f"unexpected error while fetching the picture ({type(exc).__name__})")
        if avatar.status == "not_found":
            if expansion:
                self._count("expansion_no_account")
                return None
            # A provider did return this profile, so keep the lead instead of silently dropping it.
            return unverified(
                "the profile page answers 'not found': the account was deleted or renamed, "
                "or the site hides it from logged-out clients"
            )
        if avatar.status != "ok" or avatar.data is None:
            return unverified(avatar.reason or "the profile picture could not be fetched")
        try:
            image = decode_image(avatar.data)
        except ImageError as exc:
            return unverified(f"the profile picture could not be read: {exc}")
        try:
            async with self._cpu:
                features = await asyncio.to_thread(comparator.candidate_features, image, query)
                comparison = comparator.compare(query, features)
        except Exception as exc:  # model/runtime failure on one picture
            return unverified(f"the profile picture could not be compared ({type(exc).__name__})")
        # ``features`` (the other account's embeddings) goes out of scope here and is never stored.

        if comparison.label is MatchLabel.NOT_A_MATCH:
            self._count("expansion_different_picture" if expansion else "not_a_match")
            if expansion:
                return None
        else:
            self._count("matches")
        if cache is not None and comparison.label is not MatchLabel.NOT_A_MATCH:
            cache.save_avatar(account, image)
        return Result(
            account=account,
            label=comparison.label,
            confidence=comparison.confidence,
            found_via=list(candidate.found_via),
            method=comparison.method,
            details=dict(comparison.details),
            avatar_url=avatar.image_url,
            avatar_thumb=thumbnail_data_uri(image) if comparison.label is not MatchLabel.NOT_A_MATCH else None,
            source_urls=list(candidate.source_urls),
            expanded_from=candidate.expanded_from,
        )

    # ------------------------------------------------------------------ expansion

    def _expansion_candidates(self, results: list[Result], known: set[tuple[str, str, str]]) -> list[Candidate]:
        seeds: dict[str, str] = {}
        for result in sorted(results, key=Result.sort_key):
            if result.label not in STRONG_LABELS or (result.confidence or 0.0) < self.options.min_confidence:
                continue  # only matches the user is shown can seed expansion
            seed = self.registry.expansion_seed(result.account)
            if seed and seed not in seeds:
                seeds[seed] = f"{result.account.platform}:{result.account.display}"
        limit = self.settings.max_expansion_seeds
        if len(seeds) > limit:
            self._warnings.append(
                f"{len(seeds)} distinct usernames were verified; only the first {limit} were expanded "
                "(raise AVATARTRACE_MAX_EXPANSION_SEEDS to expand more)."
            )
        candidates: list[Candidate] = []
        seen = set(known)
        for seed, origin in list(seeds.items())[:limit]:
            for account in self.registry.expansion_accounts(seed):
                if account.key in seen:
                    continue
                seen.add(account.key)
                candidates.append(
                    Candidate(account=account, found_via=[f"username expansion (from {origin})"], expanded_from=origin)
                )
        return candidates


async def run_scan(
    source: str,
    options: ScanOptions,
    settings: Settings,
    *,
    progress: Progress | None = None,
) -> ScanReport:
    """Convenience wrapper around :class:`Scanner`."""
    return await Scanner(settings, options, progress=progress).run(source)
