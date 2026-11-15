"""Optional on-disk cache of one run (``--keep-cache``).

By default avatartrace writes nothing: fetched pictures and embeddings exist
only in memory and vanish when the scan ends. With ``--keep-cache`` the query
photo, the matched pictures, the query's embeddings and a JSON report are
written to a per-run folder with owner-only permissions.
"""

from __future__ import annotations

import contextlib
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image

from avatartrace.compare import ImageFeatures
from avatartrace.config import Settings, default_cache_dir
from avatartrace.models import Account, ScanReport

_UNSAFE = re.compile(r"[^A-Za-z0-9._@-]+")


@dataclass(slots=True)
class RunCache:
    path: Path

    @classmethod
    def create(cls, settings: Settings, sha256: str, started: datetime) -> RunCache:
        root = settings.cache_dir or default_cache_dir()
        path = root / f"{started.strftime('%Y%m%dT%H%M%SZ')}-{sha256[:10]}"
        (path / "avatars").mkdir(parents=True, exist_ok=True)
        for folder in (root, path, path / "avatars"):
            with contextlib.suppress(OSError):
                folder.chmod(0o700)
        return cls(path)

    def save_query(self, image: Image.Image) -> None:
        image.save(self.path / "query.jpg", "JPEG", quality=92)

    def save_avatar(self, account: Account, image: Image.Image) -> None:
        name = _UNSAFE.sub("_", f"{account.platform}__{account.namespace}__{account.username}")[:150]
        image.save(self.path / "avatars" / f"{name}.jpg", "JPEG", quality=90)

    def save_embeddings(self, features: ImageFeatures) -> None:
        path = self.path / "query_embeddings.npz"
        faces = np.stack(features.faces) if features.faces else None
        if faces is not None and features.clip is not None:
            np.savez(path, faces=faces, clip=features.clip)
        elif faces is not None:
            np.savez(path, faces=faces)
        elif features.clip is not None:
            np.savez(path, clip=features.clip)

    def save_report(self, report: ScanReport) -> None:
        from avatartrace.report import render_json

        (self.path / "report.json").write_text(render_json(report), encoding="utf-8")


def clear_cache(settings: Settings) -> int:
    """Delete every kept run. Returns the number of run folders removed."""
    root = settings.cache_dir or default_cache_dir()
    if not root.is_dir():
        return 0
    removed = 0
    for child in root.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
            removed += 1
    return removed
