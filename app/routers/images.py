"""Fotos pro Uhr: Upload, Sortierung, Labels, Korrekturen, Originalzugriff.

- Uploads werden serverseitig auf echten Typ, Größe und Pixelmaße geprüft.
- Originaldateien liegen unverändert unter DATA_DIR/images; Korrekturen sind
  Parameter und werden nur für Vorschau/Export gerendert.
- Katalogbilder (Wecatalog) sind standardmäßig unverändert (kein Edit).
- Alle Routen laufen hinter Auth-Gate + CSRF; kein statischer, offener Media-Pfad.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from ..csrf import verify_csrf
from ..deps import DbDep, SettingsDep, load_watch, safe_next
from ..flash import flash
from ..models import ImageSource, WatchImage
from ..services import image_classify, image_ops
from ..templating import render

router = APIRouter(tags=["images"])

_MUTATE = [Depends(verify_csrf)]

MIME_BY_FMT = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


def load_image(db: Session, image_id: int) -> WatchImage:
    image = db.get(WatchImage, image_id)
    if image is None:
        raise HTTPException(status_code=404, detail="Bild nicht gefunden.")
    return image


def _original_filename(raw: str | None) -> str:
    name = (raw or "bild").replace("\\", "/").split("/")[-1]
    name = "".join(c for c in name if c.isprintable())[:180].strip()
    return name or "bild"


def _editable(image: WatchImage, settings) -> bool:  # type: ignore[no-untyped-def]
    if image.source != ImageSource.WECATALOG:
        return True
    return bool(settings.wecatalog_allow_image_edit)


@router.post("/watches/{watch_id}/images/auto-order", dependencies=_MUTATE)
async def images_auto_order(request: Request, watch_id: str, db=DbDep):
    """Reihenfolge nach Bildrolle (Dial → … → Box) + leere Labels auffüllen."""
    watch = load_watch(db, watch_id)
    form = await request.form()
    image_classify.auto_order(list(watch.images))
    db.commit()
    flash(
        request,
        "Bilder automatisch sortiert und Rollen ergänzt (manuelle Labels bleiben).",
        "success",
    )
    location = safe_next(form.get("next"), f"/watches/{watch.id}")
    return Response(status_code=303, headers={"location": location})


@router.post("/watches/{watch_id}/images", dependencies=_MUTATE)
async def upload_images(request: Request, watch_id: str, db=DbDep, settings=SettingsDep):
    watch = load_watch(db, watch_id)
    form = await request.form()
    uploads = form.getlist("files")
    if not uploads:
        flash(request, "Keine Datei ausgewählt.", "warning")
        return Response(status_code=303, headers={"location": f"/watches/{watch.id}"})

    saved = 0
    next_order = max((img.sort_order for img in watch.images), default=-1) + 1
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
        path = image_ops.store_original(data, probe, settings.images_dir)
        db.add(
            WatchImage(
                watch=watch,
                stored_name=path.name,
                original_name=_original_filename(filename),
                source=ImageSource.OWNER,
                sort_order=next_order,
                mime=MIME_BY_FMT.get(probe.fmt, "application/octet-stream"),
                width=probe.width,
                height=probe.height,
                byte_size=probe.byte_size,
            )
        )
        next_order += 1
        saved += 1
    db.commit()
    if saved:
        flash(request, f"{saved} Foto(s) hochgeladen.", "success")
    return Response(status_code=303, headers={"location": f"/watches/{watch.id}"})


@router.post("/images/{image_id}/label", dependencies=_MUTATE)
async def image_label(request: Request, image_id: str, db=DbDep, settings=SettingsDep):
    form = await request.form()
    image = load_image(db, _image_id(image_id))
    if not _editable(image, settings):
        raise HTTPException(status_code=400, detail="Katalogbilder werden unverändert behandelt.")
    label = image_ops.sanitize_label(str(form.get("label") or "")) or ""
    image.label = label or None
    db.commit()
    flash(request, "Bezeichnung gespeichert.", "success")
    return Response(
        status_code=303, headers={"location": f"/watches/{image.watch_id}"}
    )


@router.post("/images/{image_id}/move", dependencies=_MUTATE)
async def image_move(request: Request, image_id: str, db=DbDep, direction: str = Form(...)):
    image = load_image(db, _image_id(image_id))
    siblings = list(
        db.scalars(
            select(WatchImage)
            .where(WatchImage.watch_id == image.watch_id)
            .order_by(WatchImage.sort_order, WatchImage.id)
        ).all()
    )
    index = next((i for i, item in enumerate(siblings) if item.id == image.id), None)
    if index is None:
        raise HTTPException(status_code=404, detail="Bild nicht gefunden.")
    swap_with = index - 1 if direction == "up" else index + 1
    if direction not in {"up", "down"} or not 0 <= swap_with < len(siblings):
        raise HTTPException(status_code=400, detail="Ungültige Richtung.")
    other = siblings[swap_with]
    image.sort_order, other.sort_order = other.sort_order, image.sort_order
    db.commit()
    return Response(
        status_code=303, headers={"location": f"/watches/{image.watch_id}"}
    )


@router.post("/images/{image_id}/delete", dependencies=_MUTATE)
async def image_delete(request: Request, image_id: str, db=DbDep, settings=SettingsDep):
    image = load_image(db, _image_id(image_id))
    watch_id = image.watch_id
    path = settings.images_dir / image.stored_name
    db.delete(image)
    db.commit()
    try:
        path.unlink()
        flash(request, "Bild entfernt.", "success")
    except FileNotFoundError:
        flash(request, "Bild entfernt (Datei war bereits nicht mehr vorhanden).", "info")
    except OSError:
        flash(request, "Datenbankeintrag entfernt, Datei blieb aber liegen.", "warning")
    return Response(status_code=303, headers={"location": f"/watches/{watch_id}"})


@router.get("/images/{image_id}/adjust")
def image_adjust_page(request: Request, image_id: str, db=DbDep, settings=SettingsDep):
    image = load_image(db, _image_id(image_id))
    return render(
        request,
        "image_adjust.html",
        image=image,
        editable=_editable(image, settings),
        adjustments=image.adjustments or {},
    )


@router.post("/images/{image_id}/adjust", dependencies=_MUTATE)
async def image_adjust_save(request: Request, image_id: str, db=DbDep, settings=SettingsDep):
    image = load_image(db, _image_id(image_id))
    if not _editable(image, settings):
        raise HTTPException(status_code=400, detail="Katalogbilder werden unverändert behandelt.")
    form = await request.form()
    raw_crop = [
        str(form.get("crop_left") or ""),
        str(form.get("crop_top") or ""),
        str(form.get("crop_right") or ""),
        str(form.get("crop_bottom") or ""),
    ]
    crop = None
    if any(raw_crop):
        try:
            crop = [float(v) for v in raw_crop]
        except ValueError:
            crop = None
    raw = {
        "brightness": form.get("brightness") or 1.0,
        "contrast": form.get("contrast") or 1.0,
        "rotation": form.get("rotation") or 0,
        "crop": crop,
    }
    image.adjustments = image_ops.sanitize_adjustments(raw)
    db.commit()
    flash(request, "Korrekturen gespeichert (Original bleibt unverändert).", "success")
    return Response(
        status_code=303, headers={"location": f"/images/{image.id}/adjust"}
    )


@router.post("/images/{image_id}/reset", dependencies=_MUTATE)
async def image_reset(request: Request, image_id: str, db=DbDep, settings=SettingsDep):
    image = load_image(db, _image_id(image_id))
    if not _editable(image, settings):
        raise HTTPException(status_code=400, detail="Katalogbilder werden unverändert behandelt.")
    image.adjustments = None
    db.commit()
    flash(request, "Korrekturen zurückgesetzt.", "success")
    return Response(
        status_code=303, headers={"location": f"/images/{image.id}/adjust"}
    )


@router.get("/images/{image_id}/file")
def image_file(
    request: Request,
    image_id: str,
    db=DbDep,
    settings=SettingsDep,
    adjusted: str = "",
    brightness: float = 0,
    contrast: float = 0,
    rotation: int = 0,
    crop: str = "",
):
    """Auth-geschützter Original-/Vorschauzugriff (kein öffentlicher Media-Pfad)."""
    image = load_image(db, _image_id(image_id))
    path = settings.images_dir / image.stored_name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Datei fehlt.")

    adjustments = image.adjustments if adjusted == "1" else None
    if adjusted == "preview" and _editable(image, settings):
        raw_crop = None
        if crop:
            try:
                parts = [float(v) for v in crop.split(",")]
                raw_crop = parts if len(parts) == 4 else None
            except ValueError:
                raw_crop = None
        adjustments = image_ops.sanitize_adjustments(
            {
                "brightness": brightness or 1.0,
                "contrast": contrast or 1.0,
                "rotation": rotation,
                "crop": raw_crop,
            }
        )

    data: bytes | None = None
    if adjustments:
        try:
            data = image_ops.render_adjusted(
                path, adjustments, max_pixels=settings.max_image_pixels
            )
        except image_ops.ImageValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    if data is None:
        data = path.read_bytes()

    return Response(
        content=data,
        media_type=image.mime,
        headers={"Cache-Control": "private, no-store", "Content-Disposition": "inline"},
    )


def _image_id(raw: str) -> int:
    if not raw.isdigit():
        raise HTTPException(status_code=404, detail="Bild nicht gefunden.")
    return int(raw)


__all__ = ["router", "load_image", "_editable", "MIME_BY_FMT", "_original_filename"]
