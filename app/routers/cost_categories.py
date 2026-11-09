"""Kostenkategorien selbst pflegbar machen (ohne Codeänderung).

Der Nutzer legt hier Kategorien an, benennt sie um oder archiviert sie.
Bestehende Kostenpositionen behalten ihre Kategorie-ID – Umbenennen wirkt
damit über alle Positionen (saubere Dashboard-Gruppierung), Archivieren
verhindert Neunutzung ohne Datenverlust. Routen sind NICHT in PUBLIC_PATHS
und damit wie alle internen Seiten authentifiziert.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select

from ..csrf import verify_csrf
from ..deps import DbDep
from ..flash import flash
from ..models import CostCategory, utcnow
from ..templating import render

router = APIRouter()
_MUTATE = [Depends(verify_csrf)]

MAX_LABEL = 80


def _redirect() -> RedirectResponse:
    return RedirectResponse(url="/cost-categories", status_code=303)


def _label_conflict(db, label: str, exclude_id: str | None = None) -> bool:  # type: ignore[no-untyped-def]
    """Gibt es die Kategorie (casefold) bereits?"""
    others = db.scalars(select(CostCategory)).all()
    return any(
        c.id != exclude_id and c.label.casefold() == label.casefold() for c in others
    )


@router.get("/cost-categories")
def cost_categories_list(request: Request, db=DbDep):
    categories = list(
        db.scalars(
            select(CostCategory).order_by(CostCategory.sort_order, CostCategory.label)
        ).all()
    )
    return render(request, "cost_categories.html", categories=categories)


@router.post("/cost-categories", dependencies=_MUTATE)
async def cost_category_new(request: Request, db=DbDep):
    form = await request.form()
    label = str(form.get("label") or "").strip()[:MAX_LABEL]
    if not label:
        flash(request, "Name der Kategorie fehlt.", "error")
    elif _label_conflict(db, label):
        flash(request, f"Diese Kategorie gibt es bereits: {label}", "error")
    else:
        max_sort = db.scalar(select(func.max(CostCategory.sort_order))) or 0
        db.add(CostCategory(label=label, sort_order=max_sort + 1))
        db.commit()
        flash(request, f"Kategorie angelegt: {label}", "success")
    return _redirect()


@router.post("/cost-categories/{category_id}/rename", dependencies=_MUTATE)
async def cost_category_rename(category_id: str, request: Request, db=DbDep):
    category = db.get(CostCategory, category_id)
    if category is None:
        raise HTTPException(status_code=404, detail="Kategorie fehlt.")
    form = await request.form()
    label = str(form.get("label") or "").strip()[:MAX_LABEL]
    if not label:
        flash(request, "Name der Kategorie fehlt.", "error")
    elif _label_conflict(db, label, exclude_id=category.id):
        flash(request, f"Diese Kategorie gibt es bereits: {label}", "error")
    else:
        category.label = label
        db.commit()
        flash(
            request,
            f"Umbenannt in „{label}“ – wirkt sofort über alle bestehenden Positionen.",
            "success",
        )
    return _redirect()


@router.post("/cost-categories/{category_id}/archive", dependencies=_MUTATE)
async def cost_category_archive(
    category_id: str,
    request: Request,
    db=DbDep,
    unarchive: str = Form(default=""),
):
    category = db.get(CostCategory, category_id)
    if category is None:
        raise HTTPException(status_code=404, detail="Kategorie fehlt.")
    if unarchive:
        category.archived_at = None
        flash(request, f"Kategorie wieder aktiv: {category.label}", "success")
    else:
        category.archived_at = utcnow()
        flash(
            request,
            f"Kategorie archiviert: {category.label} (bestehende Positionen behalten sie).",
            "success",
        )
    db.commit()
    return _redirect()
