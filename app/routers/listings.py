"""Anzeigengenerator: Entwurf ansehen/bearbeiten/speichern, TXT/MD und ZIP-Export.

- Zwei Vorlagen (Reddit, Facebook) als austauschbare Dateien in
  app/listing_templates/ (Override per LISTING_TEMPLATE_DIR, siehe README).
- Es werden ausschließlich Bestands-/Freigabedaten verwendet; vertrauliche
  Daten (Einkauf, Notizen, Quell-Snapshots) gelangen nie in den Kontext.
- Kein automatisches Veröffentlichen – der Nutzer kopiert/exportiert selbst.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..csrf import verify_csrf
from ..deps import DbDep, SettingsDep, load_watch
from ..flash import flash
from ..models import (
    ImageSource,
    ListingPlatform,
    Watch,
    WatchImage,
)
from ..models import (
    ListingDraft as ListingDraftRow,
)
from ..services import image_ops, zip_export
from ..services.listing_generator import (
    PLATFORM_LABELS,
    generate,
    listing_approved,
)
from ..templating import render

router = APIRouter(tags=["listings"])

_MUTATE = [Depends(verify_csrf)]

MAX_TITLE = 300
MAX_BODY = 60_000


def _platform(raw: str) -> ListingPlatform:
    try:
        return ListingPlatform(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Unbekannte Plattform.") from exc


def _current_text(
    db: Session, watch: Watch, platform: ListingPlatform, settings
) -> tuple[str, str, list[str], str | None, bool]:  # type: ignore[no-untyped-def]
    """(Titel, Text, fehlende Felder, Hinweis, gespeichert?)"""
    draft = db.scalar(
        select(ListingDraftRow).where(
            ListingDraftRow.watch_id == watch.id, ListingDraftRow.platform == platform
        )
    )
    generated = generate(watch, platform, settings)  # erzeugt Fehler, falls Titel nicht freigegeben
    if draft is not None:
        return draft.title, draft.body, generated.missing_fields, generated.hints, True
    return generated.title, generated.body, generated.missing_fields, generated.hints, False


def _export_images(
    db: Session, watch: Watch, settings, image_ids: list[int] | None
) -> list[WatchImage]:
    available = list(watch.images)
    approved = listing_approved(watch)
    usable = [
        img
        for img in available
        if img.source != ImageSource.WECATALOG or approved
    ]
    if image_ids is None:
        return usable
    wanted = set(image_ids)
    # Reihenfolge = gewählte Reihenfolge der Anfrage, nur Bilder dieser Uhr:
    by_id = {img.id: img for img in usable}
    ordered: list[WatchImage] = []
    for image_id in image_ids:
        image = by_id.get(image_id)
        if image is not None and image not in ordered:
            ordered.append(image)
    missing = wanted - {img.id for img in ordered}
    if missing:
        raise HTTPException(status_code=400, detail="Ungültige Bildauswahl.")
    return ordered


def _arcname(index: int, image: WatchImage) -> str:
    from pathlib import PurePosixPath

    fallback = PurePosixPath(image.original_name).stem
    label = image.label or image_ops.sanitize_label(fallback) or "bild"
    ext = image_ops.ALLOWED_FORMATS.get("JPEG", ".jpg")
    stored_ext = PurePosixPath(image.stored_name).suffix
    if stored_ext in image_ops.ALLOWED_FORMATS.values():
        ext = stored_ext
    return image_ops.export_filename(index, label, ext)


def _export_view(watch: Watch) -> tuple[list[dict], int]:
    """Bildliste inkl. Exportname (nummeriert wie der Default-Export) + Auszahl."""
    usable = [
        img
        for img in watch.images
        if img.source != ImageSource.WECATALOG or listing_approved(watch)
    ]
    excluded = len(watch.images) - len(usable)
    pairs = [
        {
            "id": img.id,
            "name": _arcname(i + 1, img),
            "label": img.label or "",
            "adjusted": bool(img.adjustments),
            "source": img.source.value,
        }
        for i, img in enumerate(usable)
    ]
    return pairs, excluded


@router.get("/watches/{watch_id}/listing")
def listing_page(
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
    return render(
        request,
        "listing.html",
        status_code=400 if error else 200,
        watch=watch,
        platform=plat,
        platforms=list(ListingPlatform),
        platform_labels=PLATFORM_LABELS,
        draft_title=title,
        draft_body=body,
        missing_fields=missing,
        hints=hints,
        is_saved=is_saved,
        error=error,
        export_pairs=export_pairs,
        excluded_count=excluded_count,
        approved=listing_approved(watch),
        errors={},
    )


@router.post("/watches/{watch_id}/listing/save", dependencies=_MUTATE)
async def listing_save(request: Request, watch_id: str, db=DbDep, settings=SettingsDep):
    watch = load_watch(db, watch_id)
    form = await request.form()
    plat = _platform(str(form.get("platform") or ""))
    title = str(form.get("title") or "").strip()
    body = str(form.get("body") or "")
    errors: dict[str, str] = {}
    if not title:
        errors["title"] = "Titel darf nicht leer sein."
    elif len(title) > MAX_TITLE:
        errors["title"] = f"Maximal {MAX_TITLE} Zeichen."
    if len(body) > MAX_BODY:
        errors["body"] = f"Maximal {MAX_BODY} Zeichen."
    if errors:
        export_pairs, excluded_count = _export_view(watch)
        return render(
            request,
            "listing.html",
            status_code=400,
            watch=watch,
            platform=plat,
            platforms=list(ListingPlatform),
            platform_labels=PLATFORM_LABELS,
            draft_title=title,
            draft_body=body,
            missing_fields=[],
            hints=None,
            is_saved=False,
            error=None,
            export_pairs=export_pairs,
            excluded_count=excluded_count,
            approved=listing_approved(watch),
            errors=errors,
        )

    draft = db.scalar(
        select(ListingDraftRow).where(
            ListingDraftRow.watch_id == watch.id, ListingDraftRow.platform == plat
        )
    )
    if draft is None:
        draft = ListingDraftRow(watch=watch, platform=plat, title=title, body=body)
        db.add(draft)
    else:
        draft.title = title
        draft.body = body
    db.commit()
    flash(request, f"Entwurf für {PLATFORM_LABELS[plat]} gespeichert.", "success")
    return Response(
        status_code=303,
        headers={"location": f"/watches/{watch.id}/listing?platform={plat.value}"},
    )


@router.post("/watches/{watch_id}/listing/regenerate", dependencies=_MUTATE)
async def listing_regenerate(request: Request, watch_id: str, db=DbDep):
    watch = load_watch(db, watch_id)
    form = await request.form()
    plat = _platform(str(form.get("platform") or ""))
    draft = db.scalar(
        select(ListingDraftRow).where(
            ListingDraftRow.watch_id == watch.id, ListingDraftRow.platform == plat
        )
    )
    if draft is not None:
        db.delete(draft)
        db.commit()
        flash(request, "Gespeicherten Entwurf verworfen – neu erzeugt.", "info")
    return Response(
        status_code=303,
        headers={"location": f"/watches/{watch.id}/listing?platform={plat.value}"},
    )


@router.get("/watches/{watch_id}/listing/export")
def listing_export(
    request: Request,
    watch_id: str,
    db=DbDep,
    settings=SettingsDep,
    platform: str = "reddit",
    format: str = "txt",
):
    watch = load_watch(db, watch_id)
    plat = _platform(platform)
    if format not in {"txt", "md"}:
        raise HTTPException(status_code=400, detail="Format muss txt oder md sein.")
    try:
        title, body, _, _, _ = _current_text(db, watch, plat, settings)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    content = f"{title}\n\n{body}".strip() + "\n"
    safe_sku = "".join(c for c in watch.sku if c.isalnum() or c in "-_") or "uhr"
    media = "text/markdown; charset=utf-8" if format == "md" else "text/plain; charset=utf-8"
    return Response(
        content=content,
        media_type=media,
        headers={
            "Content-Disposition": (
                f'attachment; filename="anzeige-{safe_sku}-{plat.value}.{format}"'
            )
        },
    )


@router.get("/watches/{watch_id}/listing/zip")
def listing_zip(
    request: Request,
    watch_id: str,
    db=DbDep,
    settings=SettingsDep,
    platform: str = "reddit",
    image_ids: Annotated[list[int] | None, Query()] = None,
):
    """ZIP mit ausgewählten (ggf. korrigierten) Bildern + passendem Anzeigentext."""
    watch = load_watch(db, watch_id)
    plat = _platform(platform)

    try:
        title, body, _, _, _ = _current_text(db, watch, plat, settings)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Ohne Auswahl: Defaultreihenfolge = alle freigegebenen Bilder.
    images = _export_images(db, watch, settings, image_ids or None)
    if not images:
        raise HTTPException(status_code=400, detail="Keine Bilder ausgewählt/freigegeben.")

    entries: list[tuple[str, bytes]] = []
    for index, image in enumerate(images, start=1):
        path = settings.images_dir / image.stored_name
        if not path.is_file():
            continue
        data: bytes | None = None
        if image.adjustments and (
            image.source != ImageSource.WECATALOG or settings.wecatalog_allow_image_edit
        ):
            try:
                data = image_ops.render_adjusted(
                    path, image.adjustments, max_pixels=settings.max_image_pixels
                )
            except image_ops.ImageValidationError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        if data is None:
            data = path.read_bytes()
        entries.append((_arcname(index, image), data))

    if not entries:
        raise HTTPException(status_code=400, detail="Keine exportierbaren Bilddateien gefunden.")

    text_name = f"anzeige-{plat.value}.txt"
    entries.insert(0, (text_name, f"{title}\n\n{body}".strip().encode("utf-8")))
    archive = zip_export.build_zip(entries)

    safe_sku = "".join(c for c in watch.sku if c.isalnum() or c in "-_") or "uhr"
    return Response(
        content=archive,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="anzeige-{safe_sku}.zip"',
            "Cache-Control": "private, no-store",
        },
    )


__all__ = ["router"]
