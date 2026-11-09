"""Box-Inventar-Seite: Einkäufe erfassen, Typen pflegen, Bestände sehen.

Alles authentifiziert (nicht in PUBLIC_PATHS); Box-EK ist interne Einkaufs-
information und fließt nie in Anzeigen- oder ZIP-Kontexte (dort stehen nur
Verkaufsangaben).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select

from ..csrf import verify_csrf
from ..deps import DbDep
from ..flash import flash
from ..forms import MoneyError, parse_money
from ..models import BoxPurchase, BoxType, new_id, utcnow
from ..money import format_money
from ..services.boxes import overview, unit_cost
from ..templating import render

router = APIRouter(tags=["boxes"])
_MUTATE = [Depends(verify_csrf)]


def _redirect() -> RedirectResponse:
    return RedirectResponse(url="/boxes", status_code=303)


def _recent_purchases(db) -> list[dict]:  # type: ignore[no-untyped-def]
    rows = db.execute(
        select(BoxPurchase, BoxType.label)
        .join(BoxType, BoxPurchase.box_type_id == BoxType.id)
        .order_by(BoxPurchase.bought_at.desc(), BoxPurchase.id.desc())
        .limit(25)
    ).all()
    return [
        {
            "bought_at": p.bought_at,
            "type_label": label,
            "quantity": p.quantity,
            "total_cost_cents": p.total_cost_cents,
            "unit": unit_cost(p.quantity, p.total_cost_cents),
            "note": p.note,
        }
        for p, label in rows
    ]


@router.get("/boxes")
def boxes_page(request: Request, db=DbDep):
    return render(
        request,
        "boxes.html",
        rows=overview(db),
        purchases=_recent_purchases(db),
    )


@router.post("/boxes/purchases", dependencies=_MUTATE)
async def box_purchase_new(request: Request, db=DbDep):
    form = await request.form()
    type_id = str(form.get("box_type_id") or "").strip()
    box_type = db.get(BoxType, type_id) if type_id else None
    if box_type is None:
        flash(request, "Bitte einen Box-Typ wählen.", "error")
        return _redirect()
    if box_type.archived_at is not None:
        flash(request, f"Typ archiviert: {box_type.label} – zuerst aktivieren.", "error")
        return _redirect()
    try:
        quantity = int(str(form.get("quantity") or "0"))
    except ValueError:
        quantity = 0
    if quantity < 1 or quantity > 9999:
        flash(request, "Menge muss zwischen 1 und 9999 liegen.", "error")
        return _redirect()
    try:
        total = parse_money(str(form.get("total_cost") or ""))
    except MoneyError as exc:
        flash(request, f"Gesamtpreis: {exc}", "error")
        return _redirect()
    if total is None or total <= 0:
        flash(request, "Gesamtpreis fehlt oder ist <= 0.", "error")
        return _redirect()

    purchase = BoxPurchase(
        box_type_id=box_type.id,
        quantity=quantity,
        total_cost_cents=total,
        note=(str(form.get("note") or "").strip()[:80] or None),
        bought_at=utcnow(),
    )
    db.add(purchase)
    db.commit()
    flash(
        request,
        f"Einkauf gespeichert: {quantity}× {box_type.label} für "
        f"{format_money(total)} (Ø {format_money(unit_cost(quantity, total))}/Box).",
        "success",
    )
    return _redirect()


@router.post("/boxes/types", dependencies=_MUTATE)
async def box_type_new(request: Request, db=DbDep):
    form = await request.form()
    label = str(form.get("label") or "").strip()[:80]
    if not label:
        flash(request, "Name des Box-Typs fehlt.", "error")
        return _redirect()
    exists = db.scalars(select(BoxType)).all()
    if any(c.label.casefold() == label.casefold() for c in exists):
        flash(request, f"Diesen Box-Typ gibt es bereits: {label}", "error")
        return _redirect()
    max_sort = db.scalar(select(func.max(BoxType.sort_order))) or 0
    db.add(BoxType(id=new_id(), label=label, sort_order=max_sort + 1))
    db.commit()
    flash(request, f"Box-Typ angelegt: {label}", "success")
    return _redirect()


@router.post("/boxes/types/{box_type_id}/archive", dependencies=_MUTATE)
async def box_type_archive(
    box_type_id: str,
    request: Request,
    db=DbDep,
    unarchive: str = Form(default=""),
):
    box_type = db.get(BoxType, box_type_id)
    if box_type is None:
        flash(request, "Box-Typ fehlt.", "error")
        return _redirect()
    if unarchive:
        box_type.archived_at = None
        flash(request, f"Box-Typ wieder aktiv: {box_type.label}", "success")
    else:
        box_type.archived_at = utcnow()
        flash(
            request,
            f"Box-Typ archiviert: {box_type.label} (Bestände bleiben erhalten).",
            "success",
        )
    db.commit()
    return _redirect()
