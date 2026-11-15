"""Reports: HTML (side-by-side pictures), JSON and CSV."""

from __future__ import annotations

import csv
import io
import json
from importlib import resources
from pathlib import Path
from typing import Any

from jinja2 import Environment, select_autoescape

from avatartrace import REPO_URL, __version__
from avatartrace.models import MatchLabel, Result, ScanReport

FORMATS: tuple[str, ...] = (".html", ".json", ".csv")
CSV_COLUMNS: tuple[str, ...] = (
    "Platform",
    "Username",
    "Profile URL",
    "Match type",
    "Confidence",
    "Found via",
    "Method",
    "Avatar URL",
    "Note",
)
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


class ReportError(ValueError):
    """The requested report cannot be written."""


def report_format(path: Path) -> str:
    """Report format for a file name, or raise :class:`ReportError`."""
    suffix = path.suffix.lower()
    if suffix == ".htm":
        suffix = ".html"
    if suffix not in FORMATS:
        raise ReportError(f"unsupported report format '{path.suffix}': use .html, .json or .csv")
    return suffix


def safe_url(value: object) -> str:
    """Only http(s) URLs are ever rendered as links."""
    text = str(value or "").strip()
    return text if text.lower().startswith(("http://", "https://")) else ""


def confidence_text(result: Result) -> str:
    return "n/a" if result.confidence is None else f"{result.confidence:.0f}%"


def result_row(result: Result, *, thumbnails: bool = False) -> dict[str, Any]:
    row: dict[str, Any] = {
        "platform": result.account.platform,
        "platform_name": result.account.platform_name,
        "username": result.account.username,
        "display": result.account.display,
        "profile_url": result.account.profile_url,
        "label": result.label.value,
        "match_type": result.match_type,
        "confidence": result.confidence,
        "found_via": result.found_via,
        "method": result.method,
        "details": result.details,
        "avatar_url": result.avatar_url,
        "reason": result.reason,
        "source_urls": result.source_urls,
        "expanded_from": result.expanded_from,
    }
    if thumbnails:
        row["avatar_thumb"] = result.avatar_thumb
    return row


def report_dict(report: ScanReport, *, thumbnails: bool = False) -> dict[str, Any]:
    """The whole scan as plain data (the JSON report; also used by the web UI)."""
    matches = report.matches()
    shown = {id(r) for r in matches}
    below = [
        r
        for r in report.results
        if id(r) not in shown and r.label not in (MatchLabel.UNVERIFIED, MatchLabel.NOT_A_MATCH)
    ]
    rejected = [r for r in report.results if r.label is MatchLabel.NOT_A_MATCH]
    data: dict[str, Any] = {
        "tool": "avatartrace",
        "version": __version__,
        "disclaimer": "Matches are investigative leads, not proof of identity. Verify independently before acting.",
        "query": {
            "source": report.query_source,
            "pixel_sha256": report.query_sha256,
            "width": report.query_width,
            "height": report.query_height,
            "faces_detected": report.query_faces,
        },
        "started_at": report.started_at,
        "finished_at": report.finished_at,
        "min_confidence": report.min_confidence,
        "username_expansion": report.expanded,
        "engines": report.engines,
        "matches": [result_row(r, thumbnails=thumbnails) for r in matches],
        "below_min_confidence": [result_row(r, thumbnails=thumbnails) for r in below],
        "unverified": [result_row(r) for r in report.unverified()],
        "not_a_match": [result_row(r) for r in rejected],
        "providers": [
            {"name": p.name, "status": p.status, "hits": p.hits, "message": p.message} for p in report.providers
        ],
        "manual_search_links": [{"name": name, "url": url} for name, url in report.manual_links],
        "warnings": report.warnings,
        "stats": report.stats,
    }
    if thumbnails:
        data["query"]["thumb"] = report.query_thumb
    return data


def render_json(report: ScanReport) -> str:
    return json.dumps(report_dict(report), indent=2, ensure_ascii=False) + "\n"


def _csv_cell(value: object) -> str:
    """Neutralise spreadsheet formulas: usernames and URLs come from the open web."""
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(_FORMULA_PREFIXES) else text


def render_csv(report: ScanReport) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for result in (*report.matches(), *report.unverified()):
        writer.writerow(
            _csv_cell(cell)
            for cell in (
                result.account.platform_name,
                result.account.display,
                result.account.profile_url,
                result.match_type,
                "" if result.confidence is None else f"{result.confidence:.1f}",
                "; ".join(result.found_via),
                result.method or "",
                result.avatar_url or "",
                result.reason or "",
            )
        )
    return buffer.getvalue()


def _environment() -> Environment:
    env = Environment(autoescape=select_autoescape(default=True, default_for_string=True))
    env.filters["safe_url"] = safe_url
    return env


def render_html(report: ScanReport) -> str:
    template_text = resources.files("avatartrace").joinpath("report/template.html.j2").read_text(encoding="utf-8")
    template = _environment().from_string(template_text)
    return template.render(
        report=report,
        matches=report.matches(),
        unverified=report.unverified(),
        hidden=report.hidden_count(),
        confidence_text=confidence_text,
        version=__version__,
        repo_url=REPO_URL,
        labels=MatchLabel,
    )


def write_report(report: ScanReport, path: Path) -> Path:
    """Write the report in the format implied by the file extension."""
    fmt = report_format(path)
    text = {".html": render_html, ".json": render_json, ".csv": render_csv}[fmt](report)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise ReportError(f"could not write {path}: {exc.strerror or exc}") from exc
    return path


__all__ = [
    "CSV_COLUMNS",
    "FORMATS",
    "ReportError",
    "confidence_text",
    "render_csv",
    "render_html",
    "render_json",
    "report_dict",
    "report_format",
    "result_row",
    "safe_url",
    "write_report",
]
