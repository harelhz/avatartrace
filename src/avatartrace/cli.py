"""Command-line interface."""

from __future__ import annotations

import asyncio
import contextlib
import sys
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from avatartrace import REPO_URL, __version__
from avatartrace.compare import (
    Comparator,
    EngineUnavailable,
    FastEmbedClipEngine,
    InsightFaceEngine,
    clip_available,
    face_available,
)
from avatartrace.config import Settings, config_dir, load_settings
from avatartrace.discovery import (
    PROVIDERS,
    Provider,
    UnknownProvider,
    parse_provider_selection,
)
from avatartrace.extraction import default_registry
from avatartrace.imaging import ImageError, decode_image
from avatartrace.models import MatchLabel, Result, ScanReport
from avatartrace.pipeline import ScanError, ScanOptions, planned_uploads, run_scan
from avatartrace.report import ReportError, confidence_text, report_format, write_report

app = typer.Typer(
    name="avatartrace",
    help="Find the accounts that use the same (or a very similar) profile picture.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode=None,
)
# highlight=False: Rich must not recolour numbers or guess markup in text that comes from the web.
console = Console(highlight=False)
err = Console(stderr=True, highlight=False)

LABEL_STYLE: dict[MatchLabel, str] = {
    MatchLabel.EXACT: "bold green",
    MatchLabel.NEAR_DUPLICATE: "bold cyan",
    MatchLabel.LIKELY_SAME_FACE: "bold magenta",
    MatchLabel.WEAK: "yellow",
    MatchLabel.UNVERIFIED: "dim",
    MatchLabel.NOT_A_MATCH: "dim",
}

FIRST_RUN_NOTICE = f"""\
avatartrace finds accounts that use the same profile picture as the photo you give it.

Use it for: checking whether someone you met online is using stolen photos, finding
accounts that impersonate you, and reviewing your own exposure.

Do not use it to stalk, harass, or identify strangers. A match is a lead, not proof:
pictures are copied and reused, and look-alikes exist.

Only public, logged-out data is read. Your photo stays on this machine unless you
enable a search provider, and you are always asked before anything is uploaded.
Full policy: {REPO_URL}#ethics--safety"""


def _fail(message: str, code: int = 1) -> typer.Exit:
    err.print(Text(f"Error: {message}", style="bold red"))
    return typer.Exit(code)


def _settings() -> Settings:
    try:
        return load_settings()
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
        raise _fail(f"invalid configuration ({problems})", 2) from exc


def _first_run_notice() -> None:
    marker = config_dir() / "first-run-shown"
    if marker.exists():
        return
    err.print(Panel(Text(FIRST_RUN_NOTICE), title="Before you start", border_style="yellow"))
    with contextlib.suppress(OSError):
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(f"shown by avatartrace {__version__}\n", encoding="utf-8")


def _interactive() -> bool:
    return sys.stdin.isatty()


def _confirm_upload(providers: list[Provider], accepted: bool) -> bool:
    """Show exactly what will leave the machine and get a yes. Returns consent."""
    lines = Text()
    lines.append("This scan will send your photo outside this machine:\n\n", style="bold")
    for provider in providers:
        lines.append(f"  • {provider.disclosure()}\n")
    lines.append(
        "\nThe copy that is sent is re-encoded first, so EXIF data (GPS position, device, time) is removed.\n"
        "Each service keeps and processes the photo under its own terms. Everything else\n"
        "(comparison, face matching, reports) runs locally."
    )
    err.print(Panel(lines, title="Upload notice", border_style="red"))
    if accepted:
        err.print(Text("Upload confirmed with --i-understand-upload.", style="dim"))
        return True
    if not _interactive():
        raise _fail(
            "confirmation needed. Re-run with --i-understand-upload to allow the upload, "
            "or with --providers none to keep the photo on this machine.",
            2,
        )
    return typer.confirm("Send the photo to these services?", default=False, err=True)


def _version(value: bool) -> None:
    if value:
        console.print(Text(f"avatartrace {__version__}"))
        raise typer.Exit


@app.callback()
def main(
    version: Annotated[
        bool, typer.Option("--version", callback=_version, is_eager=True, help="Show the version and exit.")
    ] = False,
) -> None:
    """Find the accounts that use the same (or a very similar) profile picture."""


@app.command()
def scan(
    photo: Annotated[str, typer.Argument(help="Path or public URL of the profile picture to trace.")],
    expand: Annotated[
        bool, typer.Option("--expand/--no-expand", help="Also try each verified username on 50+ other sites.")
    ] = False,
    providers: Annotated[
        str, typer.Option(help="Search providers to use: 'all' (every configured one), 'none', or a comma list.")
    ] = "all",
    min_confidence: Annotated[
        float, typer.Option(min=0, max=100, help="Hide verified results below this confidence (0-100).")
    ] = 80.0,
    report: Annotated[
        list[Path] | None,
        typer.Option("--report", help="Write a report; format from the extension (.html, .json, .csv). Repeatable."),
    ] = None,
    candidate: Annotated[
        list[str] | None,
        typer.Option("--candidate", "-c", help="A profile URL to verify in addition to discovered ones. Repeatable."),
    ] = None,
    image_url: Annotated[
        str | None,
        typer.Option(help="Public URL of the same photo, for providers that only accept URLs (SerpApi)."),
    ] = None,
    i_understand_upload: Annotated[
        bool,
        typer.Option("--i-understand-upload", help="Confirm that the photo may be sent to the enabled providers."),
    ] = False,
    no_face: Annotated[bool, typer.Option("--no-face", help="Disable face matching.")] = False,
    clip: Annotated[
        bool, typer.Option("--clip", help="Use CLIP similarity for photos without a face (needs the 'clip' extra).")
    ] = False,
    keep_cache: Annotated[
        bool, typer.Option("--keep-cache", help="Keep fetched pictures and embeddings on disk after the run.")
    ] = False,
    show_rejected: Annotated[
        bool, typer.Option("--show-rejected", help="Also list candidates whose picture did not match.")
    ] = False,
) -> None:
    """Trace one profile picture to the accounts that use it."""
    settings = _settings()
    try:
        selection = parse_provider_selection(providers)
    except UnknownProvider as exc:
        raise _fail(str(exc), 2) from exc
    report_paths = list(report or [])
    try:
        for path in report_paths:
            report_format(path)
    except ReportError as exc:
        raise _fail(str(exc), 2) from exc

    _first_run_notice()
    options = ScanOptions(
        expand=expand,
        providers=selection,
        min_confidence=min_confidence,
        candidates=list(candidate or []),
        public_url=image_url,
        use_face=not no_face,
        use_clip=clip,
        keep_cache=keep_cache,
    )
    uploads = planned_uploads(photo, options, settings)
    if uploads:
        options.upload_consent = _confirm_upload(uploads, i_understand_upload)
        if not options.upload_consent:
            err.print(Text("Upload declined: continuing without search providers.", style="yellow"))

    try:
        with err.status("Starting") as status:
            result = asyncio.run(run_scan(photo, options, settings, progress=lambda message: status.update(message)))
    except ScanError as exc:
        raise _fail(str(exc)) from exc
    except KeyboardInterrupt:
        raise _fail("interrupted", 130) from None

    _print_report(result, show_rejected=show_rejected)
    for path in report_paths:
        try:
            written = write_report(result, path)
        except ReportError as exc:
            raise _fail(str(exc)) from exc
        err.print(Text(f"Report written: {written}", style="green"))


def _results_table(title: str, rows: list[Result]) -> Table:
    table = Table(title=title, title_justify="left", title_style="bold", expand=False)
    for column in ("Platform", "Username", "Profile URL", "Match type", "Confidence", "Found via"):
        table.add_column(column, overflow="fold", justify="right" if column == "Confidence" else "left")
    for row in rows:
        table.add_row(
            Text(row.account.platform_name),
            Text(row.account.display),
            Text(row.account.profile_url),
            Text(row.match_type, style=LABEL_STYLE[row.label]),
            Text(confidence_text(row)),
            Text(", ".join(row.found_via)),
        )
    return table


def _print_report(report: ScanReport, *, show_rejected: bool) -> None:
    for status in report.providers:
        if status.status == "ok":
            err.print(Text(f"{status.name}: {status.hits} URL(s)", style="dim"))
        else:
            style = "yellow" if status.status == "skipped" else "red"
            err.print(Text(f"{status.name}: {status.status}: {status.message}", style=style))

    matches = report.matches()
    if matches:
        console.print(_results_table(f"Matches ({len(matches)})", matches))
    else:
        console.print(
            Text(f"No account's profile picture matched at {report.min_confidence:.0f}% confidence or above.")
        )
    hidden = report.hidden_count()
    if hidden:
        console.print(Text(f"{hidden} weaker result(s) hidden; lower --min-confidence to see them.", style="dim"))

    unverified = report.unverified()
    if unverified:
        console.print()
        console.print(_results_table(f"Unverified ({len(unverified)}): picture not fetchable, no score", unverified))
        for row in unverified:
            console.print(Text(f"  {row.account.platform}:{row.account.display}: {row.reason}", style="dim"))

    if show_rejected:
        rejected = [r for r in report.results if r.label is MatchLabel.NOT_A_MATCH]
        if rejected:
            console.print()
            console.print(_results_table(f"Not a match ({len(rejected)})", rejected))

    if not any(p.status == "ok" for p in report.providers):
        console.print()
        console.print(
            Text("No search provider ran. Search by hand, then pass what you find with --candidate:", style="bold")
        )
        for name, url in report.manual_links:
            line = Text(f"  {name}: ")
            line.append(url, style="underline")
            console.print(line)

    for warning in report.warnings:
        err.print(Text(f"Note: {warning}", style="yellow"))
    stats = report.stats
    err.print(
        Text(
            f"{stats.get('candidates', 0)} candidate(s) verified, "
            f"{stats.get('expansion_checked', 0)} expansion check(s), "
            f"{stats.get('http_requests', 0)} HTTP request(s). "
            "Matches are leads, not proof.",
            style="dim",
        )
    )


@app.command()
def providers() -> None:
    """List the search providers and whether each one is configured."""
    settings = _settings()
    table = Table(title="Search providers", title_justify="left")
    for column in ("Name", "Service", "Configured", "Receives", "Key", "Pricing"):
        table.add_column(column, overflow="fold")
    for cls in PROVIDERS:
        configured = cls.from_settings(settings) is not None
        table.add_row(
            cls.name,
            cls.label,
            Text("yes", style="green") if configured else Text("no", style="dim"),
            "the photo" if cls.sends == "image" else "the photo's URL",
            ", ".join(cls.env_keys),
            cls.pricing,
        )
    console.print(table)
    console.print(Text("Set a key in the environment or in a .env file to enable its provider.", style="dim"))


@app.command()
def platforms() -> None:
    """List the supported platforms and how each profile picture is obtained."""
    registry = default_registry()
    table = Table(title=f"Platforms ({len(registry.platforms)})", title_justify="left")
    for column in ("Id", "Platform", "Picture source", "Username expansion", "Note"):
        table.add_column(column, overflow="fold")
    for platform in registry.platforms.values():
        sources = ", ".join(s.name if s.type == "api" else s.type for s in platform.avatar) or "none"
        table.add_row(
            platform.id,
            platform.name,
            sources,
            "yes" if platform.expand else "no",
            platform.avatar_note or "",
        )
    console.print(table)
    console.print(Text(f"--expand checks {registry.expansion_site_count()} sites.", style="dim"))


@app.command()
def parse(urls: Annotated[list[str], typer.Argument(help="One or more URLs.")]) -> None:
    """Show which account each URL points at (no network access)."""
    registry = default_registry()
    table = Table(title_justify="left")
    for column in ("URL", "Platform", "Username", "Kind", "Profile URL"):
        table.add_column(column, overflow="fold")
    for url in urls:
        account = registry.extract(url)
        if account is None:
            cdn = registry.match_cdn(url)
            account = cdn.account if cdn else None
        if account is None:
            table.add_row(Text(url), Text("not a profile", style="dim"), "", "", "")
        else:
            table.add_row(
                Text(url), account.platform_name, Text(account.display), account.kind, Text(account.profile_url)
            )
    console.print(table)


@app.command()
def compare(
    first: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    second: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    no_face: Annotated[bool, typer.Option("--no-face", help="Disable face matching.")] = False,
    clip: Annotated[bool, typer.Option("--clip", help="Also use CLIP similarity.")] = False,
) -> None:
    """Compare two local pictures with the same logic a scan uses (fully offline)."""
    settings = _settings()
    try:
        image_a = decode_image(first.read_bytes())
        image_b = decode_image(second.read_bytes())
    except (ImageError, OSError) as exc:
        raise _fail(str(exc)) from exc
    face = None
    clip_engine = None
    try:
        if not no_face and face_available():
            face = InsightFaceEngine(settings.face_model, settings.face_det_size, settings.face_max_faces)
            face.warm_up()
        if clip and clip_available():
            clip_engine = FastEmbedClipEngine(settings.clip_model)
            clip_engine.warm_up()
    except EngineUnavailable as exc:
        raise _fail(str(exc)) from exc
    comparator = Comparator(settings.thresholds, face, clip_engine)
    query = comparator.query_features(image_a)
    result = comparator.compare(query, comparator.candidate_features(image_b, query))
    console.print(Text(f"{result.label.value}  {result.confidence:.1f}%  (decided by {result.method})", style="bold"))
    for key, value in result.details.items():
        console.print(Text(f"  {key}: {value}", style="dim"))


@app.command()
def web(
    port: Annotated[int, typer.Option(min=1024, max=65535, help="Port on 127.0.0.1.")] = 8765,
    open_browser: Annotated[bool, typer.Option("--open/--no-open", help="Open the page in your browser.")] = False,
) -> None:
    """Start the local web UI on http://127.0.0.1:<port> (never exposed to the network)."""
    try:
        import uvicorn

        from avatartrace.web import create_app
    except ImportError as exc:
        raise _fail("the web UI needs the optional extra: pip install 'avatartrace[web]'", 2) from exc
    settings = _settings()
    _first_run_notice()
    url = f"http://127.0.0.1:{port}"
    err.print(Text(f"avatartrace web UI: {url}  (Ctrl+C to stop)", style="bold"))
    if open_browser:
        import webbrowser

        webbrowser.open(url)
    uvicorn.run(create_app(settings, port=port), host="127.0.0.1", port=port, log_level="warning")


@app.command("clear-cache")
def clear_cache_command() -> None:
    """Delete everything kept by earlier --keep-cache runs."""
    from avatartrace.cache import clear_cache

    removed = clear_cache(_settings())
    console.print(Text(f"Removed {removed} cached run(s)."))


if __name__ == "__main__":
    app()
