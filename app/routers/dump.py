"""Pump&Dump wurde in das allgemeine Anlageformular überführt.

Die bestehenden URLs leiten weiter, damit keine alten Lesezeichen ins
Leere laufen; die Schnellanlage (Grunddaten + Fotos in einem Schritt)
geschieht jetzt über `/watches/new`.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

router = APIRouter(tags=["dump"])


@router.get("/dump")
def dump_form(request: Request) -> RedirectResponse:
    return RedirectResponse("/watches/new", status_code=303)


@router.post("/dump")
def dump_create(request: Request) -> RedirectResponse:
    return RedirectResponse("/watches/new", status_code=303)


@router.get("/dump/{watch_id}/review")
def dump_review(request: Request, watch_id: str) -> RedirectResponse:
    return RedirectResponse(f"/watches/{watch_id}", status_code=303)


__all__ = ["router"]
