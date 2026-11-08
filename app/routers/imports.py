"""Wecatalog.cn-Import: Vorschau → Prüfen/Bearbeiten → ausdrückliche Bestätigung.

Ablauf:
1. POST /imports/new – Inhalte (manuell eingefügt oder optional über freigegebenen
   API-Endpunkt) werden serverseitig preisbereinigt, als Vorschau angelegt und
   Katalogbilder gestaffelt abgelegt (noch KEINem Watch zugeordnet).
2. GET /imports/{id} – Vorschau: für neue Uhr ein vollständiges Formular,
   für bestehende Uhr ein Differenzbericht (was übernommen würde, was nicht).
3. Confirm: erst JETZT entsteht/ändert sich der Datensatz. Einkaufs-/Verkaufs-
   preise und interne Notizen sind technisch nicht Teil des Payloads und werden
   nie berührt; eigene, zwischenzeitlich geänderte Beschreibungen werden nicht
   stillschweigend überschrieben (Vorbehalt im Diff).
4. Reject: Vorschau verwerfen, gestaffelte Dateien entfernen.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import select
from starlette.datastructures import UploadFile

from ..csrf import verify_csrf
from ..deps import DbDep, SettingsDep, load_watch
from ..flash import flash
from ..forms import apply_watch_values, gen_sku, parse_watch_form
from ..models import (
    ImageSource,
    ImportPreview,
    Provenance,
    StatusEvent,
    Watch,
    WatchImage,
    WatchStatus,
    new_id,
    utcnow,
)
from ..services import image_ops
from ..services.wecatalog import (
    SOURCE_NAME,
    WecatalogDisabledError,
    WecatalogFetchError,
    fetch_catalog_entry,
    parse_specs,
    require_enabled,
    sanitize_payload,
    specs_as_text,
)
from ..templating import render

router = APIRouter(tags=["imports"])

_MUTATE = [Depends(verify_csrf)]
MIME_BY_FMT = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
MAX_DESCRIPTION = 20_000


def _guard(settings) -> None:  # type: ignore[no-untyped-def]
    try:
        require_enabled(settings)
    except WecatalogDisabledError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _staging_dir(settings, preview_id: str) -> Path:  # type: ignore[no-untyped-def]
    return settings.data_dir / "staging" / preview_id


def _valid_source_url(raw: str) -> str | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    parts = urlsplit(raw)
    if parts.scheme not in {"http", "https"} or not parts.netloc or len(raw) > 500:
        raise ValueError("Quell-URL muss eine gültige http(s)-URL sein (max. 500 Zeichen).")
    return raw


@router.get("/imports")
def imports_list(request: Request, db=DbDep, settings=SettingsDep):
    _guard(settings)
    previews = db.scalars(
        select(ImportPreview).order_by(ImportPreview.created_at.desc()).limit(100)
    ).all()
    return render(request, "imports_list.html", previews=previews)


@router.get("/imports/new")
def import_new_form(request: Request, db=DbDep, settings=SettingsDep, watch_id: str = ""):
    _guard(settings)
    watch = None
    error = None
    if watch_id:
        try:
            watch = load_watch(db, watch_id)
        except HTTPException:
            error = "Uhr nicht gefunden."
    return render(
        request,
        "import_form.html",
        watch=watch,
        error=error,
        values={"title": "", "brand": "", "model": "", "description": "", "specs_text": ""},
    )


@router.post("/imports/new", dependencies=_MUTATE)
async def import_new_submit(request: Request, db=DbDep, settings=SettingsDep):
    _guard(settings)
    form = await request.form()

    watch = None
    watch_id = str(form.get("watch_id") or "")
    if watch_id:
        watch = load_watch(db, watch_id)

    values = {
        "title": str(form.get("title") or "").strip()[:200],
        "brand": str(form.get("brand") or "").strip()[:120],
        "model": str(form.get("model") or "").strip()[:160],
        "description": str(form.get("description") or "").strip()[:MAX_DESCRIPTION],
        "specs_text": str(form.get("specs_text") or "").strip(),
    }
    error: str | None = None
    source_url_raw = str(form.get("source_url") or "")

    payload: dict = {}
    try:
        source_url = _valid_source_url(source_url_raw)
        fetch_url = str(form.get("fetch_url") or "").strip()
        if fetch_url:
            # Optionaler, streng begrenzter Abruf über den freigegebenen Endpunkt.
            payload = fetch_catalog_entry(settings, fetch_url)
        else:
            specs = parse_specs(values["specs_text"]) if values["specs_text"] else []
            payload = sanitize_payload(
                {
                    "title": values["title"],
                    "brand": values["brand"],
                    "model": values["model"],
                    "description": values["description"],
                    "specs": specs,
                }
            )
    except (ValueError, WecatalogFetchError) as exc:
        error = str(exc)

    if not error and not payload:
        error = "Keine übernehmbaren Inhalte angegeben (Titel/Beschreibung/Daten)."

    if error:
        return render(
            request,
            "import_form.html",
            status_code=400,
            watch=watch,
            error=error,
            values=values,
        )

    preview = ImportPreview(
        id=new_id(),
        kind="update" if watch else "new",
        watch_id=watch.id if watch else None,
        payload=payload,
        source_url=source_url[:500] if source_url else None,
        status="pending",
        pending_images=[],
    )
    db.add(preview)
    db.flush()

    uploads = form.getlist("files")
    next_order = (
        max((img.sort_order for img in watch.images), default=-1) + 1 if watch else 0
    )
    staged: list[dict] = []
    for upload in uploads:
        if not isinstance(upload, UploadFile):
            continue
        filename = getattr(upload, "filename", None) or "bild"
        data = await upload.read()
        try:
            probe = image_ops.probe_image(
                data,
                max_bytes=settings.max_upload_bytes,
                max_pixels=settings.max_image_pixels,
            )
        except image_ops.ImageValidationError as exc:
            flash(request, f"{filename}: {exc}", "error")
            continue
        path = image_ops.store_original(data, probe, _staging_dir(settings, preview.id))
        staged.append(
            {
                "stored": path.name,
                "original": image_ops.sanitize_label(Path(filename).stem) or "bild",
                "mime": MIME_BY_FMT.get(probe.fmt, "application/octet-stream"),
                "width": probe.width,
                "height": probe.height,
                "byte_size": probe.byte_size,
                "sort_order": next_order,
            }
        )
        next_order += 1
    preview.pending_images = staged
    db.commit()
    return Response(status_code=303, headers={"location": f"/imports/{preview.id}"})


def _load_preview(db, preview_id: str) -> ImportPreview:
    preview = db.get(ImportPreview, preview_id) if len(preview_id) == 32 else None
    if preview is None:
        raise HTTPException(status_code=404, detail="Importvorschlag nicht gefunden.")
    return preview


def _diff_rows(watch: Watch, payload: dict) -> list[dict]:
    old_snapshot = watch.source_content or {}
    rows: list[dict] = []

    def describe(value) -> str:  # type: ignore[no-untyped-def]
        if isinstance(value, list):
            return specs_as_text(value) or "—"
        return str(value or "—")

    for field, label in (
        ("title", "Titel"),
        ("brand", "Marke"),
        ("model", "Modell"),
        ("description", "Beschreibung"),
        ("specs", "Technische Daten"),
    ):
        if field not in payload:
            continue
        new_value = payload[field]
        if field in {"description", "specs"}:
            current = watch.description if field == "description" else watch.specs
            old_snapshot_value = old_snapshot.get(field)
            unchanged_since_import = not current or current == old_snapshot_value
            would_apply = unchanged_since_import
            note = (
                "Wird synchronisiert."
                if would_apply
                else "Ihre eigene Fassung bleibt erhalten (kein stiller Überschreiben-"
                "Effekt); die Quelldaten werden im Snapshot aktualisiert."
            )
        else:
            would_apply = False
            note = "Wird NICHT automatisch übernommen – bei Bedarf manuell anpassen."
        rows.append(
            {
                "label": label,
                "old": describe(
                    watch.title
                    if field == "title"
                    else watch.brand
                    if field == "brand"
                    else watch.model
                    if field == "model"
                    else watch.description
                    if field == "description"
                    else watch.specs
                ),
                "new": describe(new_value),
                "applies": would_apply,
                "note": note,
            }
        )
    return rows


@router.get("/imports/{preview_id}")
def import_preview_page(request: Request, preview_id: str, db=DbDep, settings=SettingsDep):
    _guard(settings)
    preview = _load_preview(db, preview_id)
    watch = db.get(Watch, preview.watch_id) if preview.watch_id else None
    if preview.kind == "update" and watch is None:
        raise HTTPException(status_code=404, detail="Ziel-Uhr nicht mehr vorhanden.")

    if preview.kind == "new":
        values = {
            "title": str(preview.payload.get("title") or ""),
            "brand": str(preview.payload.get("brand") or ""),
            "model": str(preview.payload.get("model") or ""),
            "description": str(preview.payload.get("description") or ""),
            "specs_text": specs_as_text(preview.payload.get("specs")),
        }
        return render(
            request,
            "import_confirm_new.html",
            preview=preview,
            values=values,
            errors={},
            statuses=list(WatchStatus),
            watch=watch,
        )

    assert watch is not None  # kind=="new" wurde oben behandelt
    return render(
        request,
        "import_confirm_update.html",
        preview=preview,
        watch=watch,
        rows=_diff_rows(watch, preview.payload),
        payload_specs=specs_as_text(preview.payload.get("specs")),
    )


@router.get("/imports/{preview_id}/images/{index}")
def import_staged_image(
    request: Request, preview_id: str, index: int, db=DbDep, settings=SettingsDep
):
    """Auth-geschützte Vorschau gestaffelter Katalogbilder (vor der Bestätigung)."""
    _guard(settings)
    preview = _load_preview(db, preview_id)
    entries = preview.pending_images or []
    if not 0 <= index < len(entries):
        raise HTTPException(status_code=404, detail="Bild nicht vorhanden.")
    entry = entries[index]
    name = Path(str(entry.get("stored") or "")).name
    path = _staging_dir(settings, preview.id) / name
    if not name or not path.is_file():
        raise HTTPException(status_code=404, detail="Datei fehlt.")
    return Response(
        content=path.read_bytes(),
        media_type=str(entry.get("mime") or "image/jpeg"),
        headers={"Cache-Control": "private, no-store"},
    )


@router.post("/imports/{preview_id}/confirm", dependencies=_MUTATE)
async def import_confirm(request: Request, preview_id: str, db=DbDep, settings=SettingsDep):
    _guard(settings)
    preview = _load_preview(db, preview_id)
    if preview.status != "pending":
        raise HTTPException(status_code=409, detail="Vorschlag wurde bereits entschieden.")

    if preview.kind == "new":
        form = await request.form()
        values, errors = parse_watch_form(form)
        if errors:
            return render(
                request,
                "import_confirm_new.html",
                status_code=400,
                preview=preview,
                values={**values, "specs_text": str(form.get("specs_text") or "")},
                errors=errors,
                statuses=list(WatchStatus),
                watch=None,
            )
        return _create_watch_from_preview(db, settings, preview, values, request)

    watch = db.get(Watch, preview.watch_id) if preview.watch_id else None
    if watch is None:
        raise HTTPException(status_code=404, detail="Ziel-Uhr nicht mehr vorhanden.")
    _apply_update(db, settings, preview, watch, request)
    return Response(status_code=303, headers={"location": f"/watches/{watch.id}"})


def _adopt_staged_images(settings, preview, watch, db) -> int:  # type: ignore[no-untyped-def]
    """Verschiebt gestaffelte Katalogbilder endgültig auf die Uhr."""
    staging = _staging_dir(settings, preview.id)
    adopted = 0
    for entry in preview.pending_images or []:
        source_path = staging / Path(str(entry.get("stored") or "")).name
        if not source_path.is_file():
            continue
        target = settings.images_dir / source_path.name
        try:
            source_path.replace(target)
        except OSError:
            continue
        db.add(
            WatchImage(
                watch=watch,
                stored_name=target.name,
                original_name=str(entry.get("original") or "katalog")[:180],
                source=ImageSource.WECATALOG,
                sort_order=int(entry.get("sort_order") or 0),
                mime=str(entry.get("mime") or "image/jpeg"),
                width=int(entry.get("width") or 0),
                height=int(entry.get("height") or 0),
                byte_size=int(entry.get("byte_size") or 0),
            )
        )
        adopted += 1
    import shutil

    shutil.rmtree(staging, ignore_errors=True)
    return adopted


def _source_fields_of(payload: dict) -> list[str]:
    return [key for key in ("title", "brand", "model", "description", "specs") if key in payload]


def _create_watch_from_preview(db, settings, preview, values, request):  # type: ignore[no-untyped-def]
    watch = Watch()
    apply_watch_values(watch, values)
    watch.sku = values["sku"] or gen_sku(db)
    watch.status = WatchStatus(values["status"])
    watch.status_changed_at = utcnow()
    watch.provenance = Provenance.WECATALOG
    watch.source_name = SOURCE_NAME
    watch.source_url = preview.source_url
    watch.imported_at = preview.created_at
    watch.source_content = dict(preview.payload)
    watch.source_fields = _source_fields_of(preview.payload)
    watch.import_confirmed_at = utcnow()
    watch.import_approved_for_listing = False  # erst separat freizugeben
    db.add(watch)
    db.flush()
    watch.status_events.append(
        StatusEvent(from_status=None, to_status=watch.status, note="Aus Katalog-Import angelegt")
    )
    adopted = _adopt_staged_images(settings, preview, watch, db)
    preview.status = "confirmed"
    preview.decided_at = utcnow()
    preview.watch_id = watch.id
    db.commit()
    flash(
        request,
        f"Uhr {watch.sku} aus Import angelegt ({adopted} Katalogbild(er)). "
        "Inhalte sind intern – vor Anzeigenverwendung ausdrücklich freigeben.",
        "success",
    )
    return Response(status_code=303, headers={"location": f"/watches/{watch.id}"})


def _apply_update(db, settings, preview, watch, request) -> None:  # type: ignore[no-untyped-def]
    old_snapshot = dict(watch.source_content or {})
    payload = dict(preview.payload)
    notes: list[str] = []

    new_description = payload.get("description")
    if new_description is not None:
        if not watch.description or watch.description == old_snapshot.get("description"):
            watch.description = new_description
            notes.append("Beschreibung synchronisiert.")
        else:
            notes.append("Beschreibung: eigene Fassung beibehalten.")

    new_specs = payload.get("specs")
    if new_specs is not None:
        if not watch.specs or watch.specs == old_snapshot.get("specs"):
            watch.specs = new_specs
            notes.append("Technische Daten synchronisiert.")
        else:
            notes.append("Technische Daten: eigene Fassung beibehalten.")

    if any(key in payload for key in ("title", "brand", "model")):
        notes.append("Titel/Markenangaben aus der Quelle wurden NICHT automatisch "
                     "übernommen (Snapshot aktualisiert).")

    # Quell-Snapshot aktualisieren – preisfrei und ohne interne Felder:
    watch.source_content = {**old_snapshot, **payload}
    watch.source_fields = sorted(set(watch.source_fields or []) | set(_source_fields_of(payload)))
    watch.source_url = preview.source_url or watch.source_url
    watch.imported_at = preview.created_at
    watch.import_confirmed_at = utcnow()
    # Einkaufspreise, Verkaufspreise, Zusatzkosten und Notizen:
    # bewusst garantiert NICHT angesprochen (nicht Teil des Payloads).

    adopted = _adopt_staged_images(settings, preview, watch, db)
    preview.status = "confirmed"
    preview.decided_at = utcnow()
    db.commit()
    flash(
        request,
        "Import bestätigt: " + " ".join(f"({i + 1}) {n}" for i, n in enumerate(notes))
        + f" ({adopted} Katalogbild(er) ergänzt).",
        "success",
    )


@router.post("/imports/{preview_id}/reject", dependencies=_MUTATE)
async def import_reject(request: Request, preview_id: str, db=DbDep, settings=SettingsDep):
    _guard(settings)
    preview = _load_preview(db, preview_id)
    if preview.status != "pending":
        raise HTTPException(status_code=409, detail="Vorschlag wurde bereits entschieden.")
    preview.status = "rejected"
    preview.decided_at = utcnow()
    preview.pending_images = []
    db.commit()

    import shutil

    shutil.rmtree(_staging_dir(settings, preview.id), ignore_errors=True)
    flash(request, "Importvorschlag verworfen.", "info")
    location = f"/watches/{preview.watch_id}" if preview.watch_id else "/imports"
    return Response(status_code=303, headers={"location": location})


__all__ = ["router"]
