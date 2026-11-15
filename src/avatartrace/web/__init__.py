"""Local web UI: drag-and-drop a photo, get the results table.

The server only ever binds to 127.0.0.1. Three further guards keep other
websites and other machines out: the Host header must be a loopback name
(stops DNS rebinding), cross-origin requests are refused, and every scan must
carry a random token that only the served page knows (stops cross-site forms).
"""

from __future__ import annotations

import contextlib
import secrets
import tempfile
from importlib import resources
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.middleware.trustedhost import TrustedHostMiddleware

from avatartrace import __version__
from avatartrace.config import Settings
from avatartrace.discovery import UnknownProvider, parse_provider_selection
from avatartrace.pipeline import ScanError, Scanner, ScanOptions, planned_uploads
from avatartrace.report import render_csv, render_html, report_dict

MAX_UPLOAD_BYTES = 40_000_000
_LOOPBACK_HOSTS = ["127.0.0.1", "localhost", "[::1]"]


def _allowed_origins(port: int) -> set[str]:
    return {f"http://127.0.0.1:{port}", f"http://localhost:{port}", f"http://[::1]:{port}"}


def create_app(settings: Settings, *, port: int = 8765, extra_hosts: tuple[str, ...] = ()) -> FastAPI:
    """Build the app. ``extra_hosts`` exists for tests; the CLI never passes it."""
    app = FastAPI(title="avatartrace", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[*_LOOPBACK_HOSTS, *extra_hosts])
    origins = _allowed_origins(port) | {f"http://{host}" for host in extra_hosts}
    token = secrets.token_urlsafe(32)
    page = (
        resources.files("avatartrace")
        .joinpath("web/index.html")
        .read_text(encoding="utf-8")
        .replace("__TOKEN__", token)
        .replace("__VERSION__", __version__)
    )
    last_report: dict[str, Any] = {}

    def check(request: Request, supplied: str | None) -> None:
        origin = request.headers.get("origin")
        if origin and origin not in origins:
            raise HTTPException(status_code=403, detail="cross-origin requests are not allowed")
        if not supplied or not secrets.compare_digest(supplied, token):
            raise HTTPException(status_code=403, detail="missing or wrong session token; reload the page")

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Any) -> Response:
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; img-src data: blob:; style-src 'unsafe-inline'; "
            "script-src 'unsafe-inline'; connect-src 'self'; form-action 'none'; frame-ancestors 'none'"
        )
        return response

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return page

    @app.post("/api/plan")
    async def plan(
        request: Request,
        has_url: Annotated[bool, Form()] = False,
        providers: Annotated[str, Form()] = "all",
        x_avatartrace_token: Annotated[str | None, Header()] = None,
    ) -> JSONResponse:
        """Which providers would receive the photo, so the page can ask for consent."""
        check(request, x_avatartrace_token)
        try:
            selection = parse_provider_selection(providers)
        except UnknownProvider as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        source = "https://placeholder.invalid/x.jpg" if has_url else "photo.jpg"
        uploads = planned_uploads(source, ScanOptions(providers=selection), settings)
        return JSONResponse({"uploads": [provider.disclosure() for provider in uploads]})

    @app.post("/api/scan")
    async def scan(
        request: Request,
        photo: Annotated[UploadFile | None, File()] = None,
        url: Annotated[str, Form()] = "",
        image_url: Annotated[str, Form()] = "",
        providers: Annotated[str, Form()] = "all",
        expand: Annotated[bool, Form()] = False,
        consent: Annotated[bool, Form()] = False,
        use_face: Annotated[bool, Form()] = True,
        use_clip: Annotated[bool, Form()] = False,
        min_confidence: Annotated[float, Form(ge=0, le=100)] = 80.0,
        x_avatartrace_token: Annotated[str | None, Header()] = None,
    ) -> JSONResponse:
        check(request, x_avatartrace_token)
        try:
            selection = parse_provider_selection(providers)
        except UnknownProvider as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        options = ScanOptions(
            expand=expand,
            providers=selection,
            min_confidence=min_confidence,
            public_url=image_url.strip() or None,
            use_face=use_face,
            use_clip=use_clip,
            upload_consent=consent,
        )
        temp_dir: tempfile.TemporaryDirectory[str] | None = None
        try:
            if photo is not None and photo.filename:
                data = await photo.read(MAX_UPLOAD_BYTES + 1)
                if len(data) > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="the photo is larger than 40 MB")
                temp_dir = tempfile.TemporaryDirectory(prefix="avatartrace-")
                path = Path(temp_dir.name) / "upload"
                path.write_bytes(data)
                source = str(path)
            elif url.strip():
                source = url.strip()
            else:
                raise HTTPException(status_code=400, detail="choose a photo or enter a photo URL")
            try:
                report = await Scanner(settings, options).run(source)
            except ScanError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            if temp_dir is not None:
                with contextlib.suppress(OSError):
                    temp_dir.cleanup()
        if photo is not None and photo.filename:
            report.query_source = photo.filename
        last_report["html"] = render_html(report)
        last_report["csv"] = render_csv(report)
        return JSONResponse(report_dict(report, thumbnails=True))

    @app.get("/api/report.{fmt}")
    async def download(fmt: str, token_query: str = "") -> Response:
        """Download the most recent scan as HTML or CSV."""
        if not token_query or not secrets.compare_digest(token_query, token):
            raise HTTPException(status_code=403, detail="missing or wrong session token; reload the page")
        if fmt not in ("html", "csv") or fmt not in last_report:
            raise HTTPException(status_code=404, detail="no report available yet")
        media = "text/html; charset=utf-8" if fmt == "html" else "text/csv; charset=utf-8"
        return Response(
            last_report[fmt],
            media_type=media,
            headers={"Content-Disposition": f'attachment; filename="avatartrace-report.{fmt}"'},
        )

    return app


__all__ = ["MAX_UPLOAD_BYTES", "create_app"]
