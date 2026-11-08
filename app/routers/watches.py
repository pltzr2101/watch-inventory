"""CRUD, Filter/Suche, Statushistorik, Archiv/Löschen für den Bestand."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import or_, select

from ..csrf import verify_csrf
from ..deps import DbDep, SettingsDep, load_watch
from ..flash import flash
from ..forms import apply_watch_values, gen_sku, parse_watch_form
from ..models import (
    StatusEvent,
    Watch,
    WatchStatus,
    utcnow,
)
from ..templating import render

router = APIRouter(tags=["watches"])

_MUTATE = [Depends(verify_csrf)]


def _redirect(location: str) -> RedirectResponse:
    return RedirectResponse(location, status_code=303)


def set_status(watch: Watch, to_status: WatchStatus, note: str | None = None) -> None:
    """Statuswechsel inkl. nachvollziehbarem History-Eintrag und Verkaufszeit."""
    if watch.status == to_status:
        return
    watch.status_events.append(
        StatusEvent(from_status=watch.status, to_status=to_status, note=note or None)
    )
    watch.status = to_status
    watch.status_changed_at = utcnow()
    if to_status == WatchStatus.SOLD:
        watch.sold_at = utcnow()
    elif watch.sold_at is not None:
        watch.sold_at = None


def _watch_form_context(watch: Watch | None, values: dict | None, errors: dict) -> dict:
    if watch is not None and values is None:
        from ..money import format_money
        from ..services.wecatalog import specs_as_text

        values = {
            "sku": watch.sku,
            "title": watch.title,
            "brand": watch.brand or "",
            "model": watch.model or "",
            "status": watch.status.value,
            "purchase_price": (
                format_money(watch.purchase_price_cents).replace(" €", "")
                if watch.purchase_price_cents is not None
                else ""
            ),
            "extra_costs": (
                format_money(watch.extra_costs_cents).replace(" €", "")
                if watch.extra_costs_cents is not None
                else ""
            ),
            "extra_costs_note": watch.extra_costs_note or "",
            "target_price": (
                format_money(watch.target_price_cents).replace(" €", "")
                if watch.target_price_cents is not None
                else ""
            ),
            "sale_price": (
                format_money(watch.sale_price_cents).replace(" €", "")
                if watch.sale_price_cents is not None
                else ""
            ),
            "condition": watch.condition or "",
            "condition_description": watch.condition_description or "",
            "included_accessories": watch.included_accessories or "",
            "shipping_info": watch.shipping_info or "",
            "description": watch.description or "",
            "specs_text": specs_as_text(watch.specs),
            "internal_notes": watch.internal_notes or "",
            "public_approved": watch.public_approved,
        }
    return {
        "values": values or {},
        "errors": errors or {},
        "statuses": list(WatchStatus),
        "watch": watch,
    }


@router.get("/watches")
def watches_list(
    request: Request,
    db=DbDep,
    q: str = "",
    status: str = "",
    archived: str = "",
):
    query = select(Watch).order_by(Watch.updated_at.desc())
    q = q.strip()
    if q:
        pattern = f"%{q}%"
        query = query.where(
            or_(
                Watch.title.ilike(pattern),
                Watch.sku.ilike(pattern),
                Watch.brand.ilike(pattern),
                Watch.model.ilike(pattern),
                Watch.internal_notes.ilike(pattern),
            )
        )
    if status and status in {s.value for s in WatchStatus}:
        query = query.where(Watch.status == status)
    if archived == "1":
        query = query.where(Watch.archived_at.is_not(None))
    else:
        query = query.where(Watch.archived_at.is_(None))

    watches = db.scalars(query.limit(500)).all()
    return render(
        request,
        "watches_list.html",
        watches=watches,
        q=q,
        status=status,
        archived=archived == "1",
        statuses=list(WatchStatus),
    )


@router.get("/watches/new")
def watch_new_form(request: Request):
    return render(request, "watch_form.html", **_watch_form_context(None, None, {}))


@router.post("/watches/new", dependencies=_MUTATE)
async def watch_new(request: Request, db=DbDep):
    form = await request.form()
    values, errors = parse_watch_form(form)
    if errors:
        return render(
            request,
            "watch_form.html",
            status_code=400,
            **_watch_form_context(None, values, errors),
        )

    watch = Watch()
    apply_watch_values(watch, values)
    watch.sku = values["sku"] or gen_sku(db)
    watch.status = WatchStatus(values["status"])
    watch.status_changed_at = utcnow()
    db.add(watch)
    db.flush()
    watch.status_events.append(
        StatusEvent(from_status=None, to_status=watch.status, note="Angelegt")
    )
    db.commit()
    flash(request, f"Uhr {watch.sku} angelegt.", "success")
    return _redirect(f"/watches/{watch.id}")


@router.get("/watches/{watch_id}")
def watch_detail(request: Request, watch_id: str, db=DbDep, settings=SettingsDep):
    watch = load_watch(db, watch_id)
    return render(
        request,
        "watch_detail.html",
        watch=watch,
        statuses=list(WatchStatus),
        wecatalog_enabled=settings.wecatalog_enabled,
    )


@router.get("/watches/{watch_id}/edit")
def watch_edit_form(request: Request, watch_id: str, db=DbDep):
    watch = load_watch(db, watch_id)
    return render(request, "watch_form.html", **_watch_form_context(watch, None, {}))


@router.post("/watches/{watch_id}/edit", dependencies=_MUTATE)
async def watch_edit(request: Request, watch_id: str, db=DbDep):
    watch = load_watch(db, watch_id)
    form = await request.form()
    values, errors = parse_watch_form(form)
    if errors:
        return render(
            request,
            "watch_form.html",
            status_code=400,
            **_watch_form_context(watch, values, errors),
        )

    apply_watch_values(watch, values)
    new_status = WatchStatus(values["status"])
    if new_status != watch.status:
        set_status(watch, new_status, note="Über Bearbeitungsformular")
    # SKU bleibt erhalten, sofern keine gültige neue angegeben wurde:
    if values.get("sku") and values["sku"] != watch.sku:
        taken = db.scalar(select(Watch.id).where(Watch.sku == values["sku"]))
        if taken:
            return render(
                request,
                "watch_form.html",
                status_code=400,
                **_watch_form_context(
                    watch, values, {**errors, "sku": "Artikelnummer bereits vergeben."}
                ),
            )
        watch.sku = values["sku"]
    db.commit()
    flash(request, "Gespeichert.", "success")
    return _redirect(f"/watches/{watch.id}")


@router.post("/watches/{watch_id}/status", dependencies=_MUTATE)
async def watch_status(
    request: Request,
    watch_id: str,
    db=DbDep,
    status: str = Form(...),
    note: str = Form(default=""),
):
    watch = load_watch(db, watch_id)
    try:
        new_status = WatchStatus(status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Unbekannter Status.") from exc
    set_status(watch, new_status, note=note.strip()[:300] or None)
    db.commit()
    flash(request, f"Status → {new_status.label}.", "success")
    return _redirect(f"/watches/{watch.id}")


@router.post("/watches/{watch_id}/archive", dependencies=_MUTATE)
async def watch_archive(
    request: Request, watch_id: str, db=DbDep, unarchive: str = Form(default="")
):
    watch = load_watch(db, watch_id)
    if unarchive:
        watch.archived_at = None
        flash(request, "Archivierung aufgehoben.", "success")
    else:
        watch.archived_at = utcnow()
        flash(request, "Uhr archiviert (Daten bleiben vollständig erhalten).", "success")
    db.commit()
    return _redirect(f"/watches/{watch.id}")


@router.post("/watches/{watch_id}/delete", dependencies=_MUTATE)
async def watch_delete(request: Request, watch_id: str, db=DbDep, settings=SettingsDep):
    watch = load_watch(db, watch_id)
    image_paths = [settings.images_dir / img.stored_name for img in watch.images]

    db.delete(watch)
    db.commit()

    removed = 0
    for path in image_paths:
        try:
            path.unlink()
            removed += 1
        except FileNotFoundError:
            continue
        except OSError:
            flash(request, f"Datei konnte nicht entfernt werden: {path.name}", "warning")
    flash(
        request,
        f"Uhr und {removed} zugehörige Datei(en) gelöscht.",
        "success",
    )
    return _redirect("/watches")


@router.post("/watches/{watch_id}/public-approve", dependencies=_MUTATE)
async def watch_public_approve(request: Request, watch_id: str, db=DbDep):
    watch = load_watch(db, watch_id)
    form = await request.form()
    watch.public_approved = str(form.get("approve") or "1") in {"1", "on", "true"}
    db.commit()
    state = "gesetzt" if watch.public_approved else "zurückgenommen"
    flash(request, f"Öffentliche Freigabe {state} (wirkt erst mit späterer Funktion).", "success")
    return _redirect(f"/watches/{watch.id}")


@router.post("/watches/{watch_id}/import-approve", dependencies=_MUTATE)
async def watch_import_approve(request: Request, watch_id: str, db=DbDep):
    watch = load_watch(db, watch_id)
    form = await request.form()
    watch.import_approved_for_listing = str(form.get("approve") or "1") in {"1", "on", "true"}
    db.commit()
    if watch.import_approved_for_listing:
        flash(request, "Importierte Inhalte sind jetzt für Anzeigen freigegeben.", "success")
    else:
        flash(request, "Freigabe für importierte Inhalte zurückgenommen.", "info")
    return _redirect(f"/watches/{watch.id}")


__all__ = ["router", "set_status", "_redirect"]
