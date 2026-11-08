"""Pump&Dump-Schnellanlage („ein Bildschirm"): Grunddaten + Bilder → Review.

- Ein Formular für die fristkritischen Grunddaten; danach führt die
  Review-Seite (Text, Bilder, ZIP, Status) durch bis zum Veröffentlichungs-
  Tracking. Für alle Details steht weiterhin das vollständige Uhr-Formular.
- Uploads werden VOR dem Anlegen des Datensatzes vollständig validiert
  (echter Typ, Größe, Pixelmaße); bei Speicherfehlern werden bereits
  abgelegte Dateien wieder entfernt (kein inkonsistenter Rest).
- Es fallen keine ratenden Pflichtangaben an: fehlende Felder werden in der
  Review-Ansicht als „nicht gepflegt" ausgewiesen.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from starlette.datastructures import UploadFile

from ..csrf import verify_csrf
from ..deps import DbDep, SettingsDep, load_watch
from ..flash import flash
from ..forms import MAX_LONG, MAX_TEXT, gen_sku
from ..models import BOX_OPTIONS, ImageSource, Provenance, Watch, WatchImage, WatchStatus
from ..money import MoneyError, parse_money
from ..services import image_classify, image_ops
from ..templating import render
from .images import MIME_BY_FMT, _original_filename
from .listings import (
    PLATFORM_LABELS,
    _current_text,
    _export_view,
    _listing_extra,
    _platform,
)

router = APIRouter(tags=["dump"])

_MUTATE = [Depends(verify_csrf)]

CONDITION_SUGGESTIONS = [
    "Neu, fully stickered",
    "Neu",
    "Wie neu",
    "Sehr gut",
    "Gut",
    "Gebraucht",
]


def _values_from(form) -> dict:
    return {
        key: str(form.get(key) or "").strip()
        for key in (
            "title",
            "brand",
            "model",
            "factory",
            "movement",
            "condition",
            "description",
            "price",
            "box",
            "status",
        )
    }


def _validate_dump(values: dict, status_raw: str, box_raw: str) -> dict[str, str]:
    errors: dict[str, str] = {}
    if not values["title"]:
        errors["title"] = "Pflichtfeld (Marke/Modell oder Titel)."
    elif len(values["title"]) > MAX_TEXT["title"]:
        errors["title"] = f"Maximal {MAX_TEXT['title']} Zeichen."
    for key, limit in (("brand", 120), ("model", 160), ("condition", 80),
                       ("factory", 80), ("movement", 80)):
        if len(values[key]) > limit:
            errors[key] = f"Maximal {limit} Zeichen."
    if len(values["description"]) > MAX_LONG:
        errors["description"] = f"Maximal {MAX_LONG} Zeichen."
    if status_raw not in {s.value for s in WatchStatus}:
        errors["status"] = "Unbekannter Status."
    if box_raw and box_raw not in BOX_OPTIONS:
        errors["box"] = "Unbekannte Box-Angabe."
    try:
        parse_money(values["price"])
    except MoneyError as exc:
        errors["price"] = str(exc)
    return errors


def _render_dump(request: Request, values: dict, errors: dict[str, str], status_code=200):
    return render(
        request,
        "dump.html",
        status_code=status_code,
        values=values,
        errors=errors,
        statuses=list(WatchStatus),
        box_options=BOX_OPTIONS,
        conditions=CONDITION_SUGGESTIONS,
    )


@router.get("/dump")
def dump_form(request: Request):
    return _render_dump(request, {"title": "", "price": ""}, {})


@router.post("/dump", dependencies=_MUTATE)
async def dump_create(request: Request, db=DbDep, settings=SettingsDep):
    form = await request.form()
    values = _values_from(form)
    status_raw = str(form.get("status") or WatchStatus.IN_GERMANY.value)
    box_raw = str(form.get("box") or "")
    errors = _validate_dump(values, status_raw, box_raw)

    # Uploads zuerst vollständig prüfen – erst danach Datensatz anlegen.
    uploads = [u for u in form.getlist("files") if isinstance(u, UploadFile)]
    prepared: list[tuple[str, bytes, image_ops.ImageProbe]] = []
    for upload in uploads:
        filename = getattr(upload, "filename", None) or "bild"
        data = await upload.read()
        try:
            probe = image_ops.probe_image(
                data,
                max_bytes=settings.max_upload_bytes,
                max_pixels=settings.max_image_pixels,
            )
        except image_ops.ImageValidationError as exc:
            errors["files"] = f"{filename}: {exc}"
            break
        prepared.append((filename, data, probe))
    if errors:
        return _render_dump(request, values, errors, status_code=400)

    specs = []
    if values["factory"]:
        specs.append({"key": "Factory", "value": values["factory"]})
    if values["movement"]:
        specs.append({"key": "Movement", "value": values["movement"]})

    watch = Watch(
        sku=gen_sku(db),
        title=values["title"],
        brand=values["brand"] or None,
        model=values["model"] or None,
        status=WatchStatus(status_raw),
        target_price_cents=parse_money(values["price"]),
        condition=values["condition"] or None,
        description=values["description"] or None,
        box_option=box_raw or None,
        specs=specs,
        provenance=Provenance.MANUAL,
    )
    db.add(watch)
    db.flush()

    stored_paths = []
    try:
        for position, (filename, data, probe) in enumerate(prepared):
            path = image_ops.store_original(data, probe, settings.images_dir)
            stored_paths.append(path)
            db.add(
                WatchImage(
                    watch=watch,
                    stored_name=path.name,
                    original_name=_original_filename(filename),
                    source=ImageSource.OWNER,
                    sort_order=position,
                    mime=MIME_BY_FMT.get(probe.fmt, "application/octet-stream"),
                    width=probe.width,
                    height=probe.height,
                    byte_size=len(data),
                )
            )
        db.flush()
        image_classify.auto_order(list(watch.images))
        db.commit()
    except OSError:
        db.rollback()
        for path in stored_paths:
            path.unlink(missing_ok=True)
        flash(request, "Speichern fehlgeschlagen – es wurden keine Daten übernommen.", "error")
        return _render_dump(
            request,
            values,
            {"files": "Dateien konnten nicht gespeichert werden."},
            status_code=400,
        )

    flash(request, f"{watch.sku} angelegt – bitte prüfen und Text/Bilder finalisieren.", "success")
    return Response(status_code=303, headers={"location": f"/dump/{watch.id}/review"})


@router.get("/dump/{watch_id}/review")
def dump_review(
    request: Request,
    watch_id: str,
    db=DbDep,
    settings=SettingsDep,
    platform: str = "reddit",
):
    watch = load_watch(db, watch_id)
    plat = _platform(platform)
    try:
        title, body, missing, hints, is_saved = _current_text(db, watch, plat, settings)
        error = None
    except ValueError as exc:
        title, body, missing, hints, is_saved = "", "", [], None, False
        error = str(exc)

    export_pairs, excluded_count = _export_view(watch)
    context = _listing_extra(db, watch, plat, settings)
    # Im Dump-Flow immer zurück zur Review-Seite, nicht zur Listing-Seite:
    context["next_value"] = f"/dump/{watch.id}/review"
    return render(
        request,
        "dump_review.html",
        status_code=400 if error else 200,
        watch=watch,
        platform=plat,
        platforms=list(type(plat)),
        platform_labels=PLATFORM_LABELS,
        draft_title=title,
        draft_body=body,
        missing_fields=missing,
        hints=hints,
        is_saved=is_saved,
        error=error,
        export_pairs=export_pairs,
        excluded_count=excluded_count,
        errors={},
        **context,
    )


__all__ = ["router"]
