"""CSRF-Prüfung als wiederverwendbare FastAPI-Route-Dependency."""
from __future__ import annotations

from fastapi import HTTPException, Request

from .middleware import CSRF_KEY
from .security import csrf_token_equal


async def verify_csrf(request: Request) -> None:
    """Verwirft state-changing Requests ohne gültiges Session-CSRF-Token.

    Basiert auf `request.form()` (geparst von FastAPI, Body bleibt erhalten)
    bzw. dem Header `X-CSRF-Token` für nicht-Formular-Clients.
    """
    expected = request.session.get(CSRF_KEY)
    provided = request.headers.get("x-csrf-token")
    if not provided:
        form = await request.form()
        raw = form.get("csrf_token")
        provided = raw if isinstance(raw, str) else None
    if not csrf_token_equal(expected, provided):
        raise HTTPException(status_code=403, detail="CSRF-Token fehlt oder ist ungültig.")
