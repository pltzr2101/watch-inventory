"""Gemeinsame FastAPI-Dependencies (DB-Session, Settings, Watch-Lookup)."""
from __future__ import annotations

import re

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .config import Settings
from .models import Watch

WATCH_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def safe_next(raw, default: str) -> str:
    """Sicherer interner Redirect-Parameter (nur lokale Pfade)."""
    value = str(raw or "").strip()
    if value.startswith("/") and not value.startswith("//") and "\\" not in value:
        return value
    return default


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request):
    factory = request.app.state.session_factory
    session = factory()
    try:
        yield session
    finally:
        session.close()


def load_watch(db: Session, watch_id: str) -> Watch:
    if not WATCH_ID_RE.match(watch_id or ""):
        raise HTTPException(status_code=404, detail="Unbekannte Uhr.")
    watch = db.get(Watch, watch_id)
    if watch is None:
        raise HTTPException(status_code=404, detail="Uhr nicht gefunden.")
    return watch


SettingsDep = Depends(get_settings)
DbDep = Depends(get_db)
