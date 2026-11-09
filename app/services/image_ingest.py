"""Foto-Ingest für die Uhrenanlage: Uploads prüfen VOR dem Datensatz.

Ausgelagerte Kernlogik aus dem früheren Pump&Dump-Flow:
- `prepare_uploads` liest und validiert Dateien vollständig (echter Typ,
  Größe, Pixelmaße), ohne schon etwas anzulegen.
- `store_prepared` legt Originale ab und schreibt WatchImage-Zeilen;
  bei Speicherfehlern werden bereits abgelegte Dateien entfernt.
"""
from __future__ import annotations

import contextlib

from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from ..models import ImageSource, Watch, WatchImage
from ..routers.images import MIME_BY_FMT, _original_filename
from ..services import image_classify, image_ops


async def prepare_uploads(
    form, settings  # type: ignore[no-untyped-def]
) -> tuple[list[tuple[str, bytes, image_ops.ImageProbe]], str | None]:
    """Validiert alle Uploads des Formulars → (vorbereitete Dateien, Fehler)."""
    prepared: list[tuple[str, bytes, image_ops.ImageProbe]] = []
    for upload in form.getlist("files"):
        if not isinstance(upload, UploadFile):
            continue
        filename = getattr(upload, "filename", None) or "bild"
        data = await upload.read()
        if not data:
            # Leerer Part (Browser-Sonderfall bei leerem/abgebrochenem
            # Datei-Feld) ist KEIN Upload – nicht blockieren, still überspringen.
            continue
        try:
            probe = image_ops.probe_image(
                data,
                max_bytes=settings.max_upload_bytes,
                max_pixels=settings.max_image_pixels,
            )
        except image_ops.ImageValidationError as exc:
            return [], f"{filename}: {exc}"
        prepared.append((filename, data, probe))
    return prepared, None


def store_prepared(
    db: Session,
    watch: Watch,
    prepared: list[tuple[str, bytes, image_ops.ImageProbe]],
    settings,  # type: ignore[no-untyped-def]
) -> None:
    """Speichert vorbereitete Uploads (Originale + Zeilen). Wirft OSError.

    Reihenfolge ist frisch (0..n); `auto_order` sortiert nach Bildrolle,
    sofern Labels es nahelegen.
    """
    stored_paths = []
    try:
        for position, (filename, data, probe) in enumerate(prepared):
            path = image_ops.store_original(data, probe, settings.images_dir)
            stored_paths.append(path)
            if probe.fmt in {"HEIC", "HEIF"}:
                with contextlib.suppress(OSError):
                    image_ops.write_preview(data, path)
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
    except OSError:
        db.rollback()
        for path in stored_paths:
            path.unlink(missing_ok=True)
            path.with_name(f"{path.stem}.preview.jpg").unlink(missing_ok=True)
        raise
