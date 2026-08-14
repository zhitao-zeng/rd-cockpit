"""Production web application: read-only API plus prebuilt SPA assets."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def create_web_app(home: Path, dist: Path) -> Any:
    try:
        from fastapi import FastAPI, HTTPException
        from fastapi.responses import FileResponse
        from fastapi.staticfiles import StaticFiles
        from starlette.middleware.gzip import GZipMiddleware
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("web service requires: pip install -e '.[server]'") from exc

    dist = dist.expanduser().resolve()
    index = dist / "index.html"
    if not index.is_file():
        raise RuntimeError(f"frontend build is missing: {index}")

    from .api import create_app

    app = FastAPI(title="R&D Cockpit Web", docs_url=None, redoc_url=None)
    app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=5)
    legacy_api = os.environ.get("RD_ENABLE_LEGACY_API", "0") == "1"
    app.mount("/api", create_app(home, safe_mode=not legacy_api))
    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> Any:
        candidate = (dist / path).resolve()
        try:
            candidate.relative_to(dist)
        except ValueError as exc:
            raise HTTPException(status_code=404) from exc
        if path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index)

    return app
