"""FastAPI-App-Factory.

Aufruf: `uvicorn --factory app.main:create_app --host 0.0.0.0 --port 8080`
Die Factory validiert Pflicht-Secrets (Fail-Fast) und initialisiert Datenbank
und Verzeichnisse unter DATA_DIR.
"""
from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

import PIL.Image as PILImage
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware
from starlette.staticfiles import StaticFiles

from .config import ConfigError, Settings, load_settings
from .db import Base, ensure_schema, make_engine, make_session_factory
from .middleware import (
    AuthGateMiddleware,
    LoginRateLimitMiddleware,
    SecurityHeadersMiddleware,
)
from .routers import (
    auth,
    cost_categories,
    dashboard,
    dump,
    images,
    imports,
    listings,
    templates_admin,
    watches,
)
from .templating import render

logger = logging.getLogger("watch-inventory")


def _cleanup_stale_staging(settings: Settings, max_age_days: int = 7) -> None:
    """Entfernt verwaiste gestaffelte Importdateien älter als max_age_days."""
    staging_root = settings.data_dir / "staging"
    if not staging_root.is_dir():
        return
    now = time.time()
    for entry in staging_root.iterdir():
        if not entry.is_dir():
            continue
        try:
            if now - entry.stat().st_mtime > max_age_days * 86400:
                shutil.rmtree(entry, ignore_errors=True)
        except OSError:
            continue


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        settings = load_settings()  # wirft ConfigError bei fehlenden Secrets
    settings.ensure_dirs()

    # Pixel-Limit auch für Pillow selbst setzen (Decompression-Bomb-Schutz).
    PILImage.MAX_IMAGE_PIXELS = settings.max_image_pixels

    engine = make_engine(settings.database_path)
    Base.metadata.create_all(engine)
    ensure_schema(engine)

    app = FastAPI(
        title="Watch-Inventory (intern)",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,  # keine öffentliche API-Dokumentation
        version="0.1.0",
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    # Reihenfolge: zuletzt hinzugefügt = äußerste Schicht (Session zuerst).
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        LoginRateLimitMiddleware,
        max_attempts=settings.login_max_attempts,
        window_seconds=settings.login_window_seconds,
    )
    app.add_middleware(AuthGateMiddleware)

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        session_cookie="session",
        https_only=settings.cookie_secure,
        same_site="lax",
        max_age=settings.cookie_max_age_seconds,
    )

    app.include_router(auth.router)
    app.include_router(dashboard.router)
    app.include_router(watches.router)
    app.include_router(images.router)
    app.include_router(listings.router)
    app.include_router(templates_admin.router)
    app.include_router(dump.router)
    app.include_router(imports.router)
    app.include_router(cost_categories.router)

    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict:
        return {"status": "ok"}

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        accept = request.headers.get("accept", "")
        if "text/html" in accept or not accept:
            return render(
                request,
                "error.html",
                status_code=exc.status_code,
                status_code_=exc.status_code,
                detail=str(exc.detail),
            )
        return JSONResponse(
            {"detail": exc.detail}, status_code=exc.status_code
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        return PlainTextResponse(
            "Ungültige Eingabe.", status_code=422
        )

    @app.exception_handler(ConfigError)
    async def config_error_handler(request: Request, exc: ConfigError):
        # Fail-Fast-Meldung sichtbar machen, ohne Secrets preiszugeben.
        return PlainTextResponse(f"Konfigurationsfehler: {exc}", status_code=500)

    _cleanup_stale_staging(settings)
    return app
