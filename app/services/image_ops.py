"""Bildvalidierung, nicht-destruktive Korrekturen und sichere Export-Namen.

Originaldateien werden unverändert gespeichert. Korrekturen (Helligkeit,
Kontrast, 90°-Rotation, Zuschnitt) werden als Parameter in der Datenbank
gehalten und nur für Export-/Vorschaukopien gerendert.
"""
from __future__ import annotations

import io
import re
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageEnhance, UnidentifiedImageError

ALLOWED_FORMATS = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}
ADJUST_MIN = 0.1
ADJUST_MAX = 2.0
JPEG_QUALITY = 92


class ImageValidationError(ValueError):
    """Upload erfüllt die Bildanforderungen nicht."""


@dataclass(frozen=True)
class ImageProbe:
    fmt: str
    ext: str
    width: int
    height: int
    byte_size: int


def probe_image(data: bytes, *, max_bytes: int, max_pixels: int) -> ImageProbe:
    """Prüft echten Dateityp, Größe und Pixelmaße (nicht nur die Endung)."""
    if not data:
        raise ImageValidationError("Datei ist leer.")
    if len(data) > max_bytes:
        raise ImageValidationError(
            f"Datei zu groß (max. {max_bytes // (1024 * 1024)} MB)."
        )

    try:
        with Image.open(io.BytesIO(data)) as img:
            img.verify()
    except Image.DecompressionBombError as exc:  # type: ignore[attr-defined]
        raise ImageValidationError("Bild ist zu groß (Pixelzahl überschreitet Limit).") from exc
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise ImageValidationError(
            "Kein gültiges Bildformat (erkannt werden JPEG, PNG und WebP)."
        ) from exc

    try:
        with Image.open(io.BytesIO(data)) as img:
            fmt = (img.format or "").upper()
            width, height = img.size
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:  # type: ignore[attr-defined]
        raise ImageValidationError("Bild konnte nicht gelesen werden.") from exc

    if fmt not in ALLOWED_FORMATS:
        raise ImageValidationError(
            f"Dateityp {fmt or 'unbekannt'} wird nicht unterstützt (JPEG, PNG, WebP)."
        )
    if width * height > max_pixels:
        raise ImageValidationError(
            f"Bild zu groß: {width}x{height} Pixel (Limit {max_pixels} Pixel)."
        )
    return ImageProbe(
        fmt=fmt, ext=ALLOWED_FORMATS[fmt], width=width, height=height, byte_size=len(data)
    )


def new_stored_name(probe: ImageProbe) -> str:
    """Zufälliger, kontrollierter Zieldateiname (Client-Name fließt nie ein)."""
    return f"{uuid.uuid4().hex}{probe.ext}"


def store_original(data: bytes, probe: ImageProbe, directory: Path) -> Path:
    """Speichert die hochgeladene Datei byte-identisch als Original."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / new_stored_name(probe)
    path.write_bytes(data)
    return path


def sanitize_label(raw: str | None) -> str | None:
    """Bildbezeichnung → sicheres, aussagekräftiges Kürzel für Dateinamen."""
    if not raw:
        return None
    text = unicodedata.normalize("NFKD", str(raw)).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return text[:40] or None


def export_filename(index: int, label: str | None, ext: str) -> str:
    """„01-front.jpg“ – Reihenfolge zweistellig, Label bereinigt, Dedup später."""
    safe_label = sanitize_label(label) or "bild"
    if ext not in ALLOWED_FORMATS.values():
        ext = ".jpg"
    return f"{index:02d}-{safe_label}{ext}"


def sanitize_adjustments(raw: dict | None) -> dict | None:
    """Validiert und normalisiert Korrekturparameter (Werte begrenzen)."""
    if not raw:
        return None

    def _factor(key: str) -> float:
        try:
            value = float(raw.get(key, 1.0))
        except (TypeError, ValueError):
            value = 1.0
        return min(ADJUST_MAX, max(ADJUST_MIN, value))

    try:
        rotation = int(raw.get("rotation", 0) or 0)
    except (TypeError, ValueError):
        rotation = 0
    rotation = (round(rotation / 90) * 90) % 360

    crop = raw.get("crop")
    crop_list: list[float] | None = None
    if crop:
        try:
            values = [float(v) for v in crop]
        except (TypeError, ValueError):
            values = []
        if len(values) == 4:
            left, top, right, bottom = values
            if (
                0 <= left < right <= 100
                and 0 <= top < bottom <= 100
                and right - left >= 5
                and bottom - top >= 5
            ):
                crop_list = [round(v, 2) for v in values]

    result = {
        "brightness": round(_factor("brightness"), 3),
        "contrast": round(_factor("contrast"), 3),
        "rotation": rotation,
        "crop": crop_list,
    }
    if not has_adjustments(result):
        return None
    return result


def has_adjustments(adj: dict | None) -> bool:
    if not adj:
        return False
    return bool(
        adj.get("crop")
        or adj.get("rotation", 0) % 360 != 0
        or abs(float(adj.get("brightness", 1.0)) - 1.0) > 1e-6
        or abs(float(adj.get("contrast", 1.0)) - 1.0) > 1e-6
    )


def render_adjusted(path: Path, adjustments: dict | None, *, max_pixels: int) -> bytes | None:
    """Rendert die Exportversion. None = keine Korrekturen, Original verwenden."""
    if not has_adjustments(adjustments):
        return None
    adj = sanitize_adjustments(adjustments) or {}
    with Image.open(path) as _opened:
        img: Image.Image = _opened
        img.load()
        original_format = (img.format or "JPEG").upper()
        width, height = img.size

        crop = adj.get("crop")
        if crop:
            left, top, right, bottom = crop
            box = (
                int(width * left / 100),
                int(height * top / 100),
                int(width * right / 100),
                int(height * bottom / 100),
            )
            img = img.crop(box)

        rotation = int(adj.get("rotation", 0)) % 360
        if rotation:
            img = img.rotate(rotation, expand=True)

        brightness = float(adj.get("brightness", 1.0))
        if brightness != 1.0:
            img = ImageEnhance.Brightness(img).enhance(brightness)
        contrast = float(adj.get("contrast", 1.0))
        if contrast != 1.0:
            img = ImageEnhance.Contrast(img).enhance(contrast)

        if img.width * img.height > max_pixels:
            raise ImageValidationError("Korrigiertes Bild überschreitet das Pixel-Limit.")

        fmt = original_format
        out = io.BytesIO()
        if fmt == "JPEG":
            if img.mode in {"RGBA", "P", "LA"}:
                img = img.convert("RGB")
            img.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        elif fmt == "PNG":
            img.save(out, format="PNG", optimize=True)
        elif fmt == "WEBP":
            img.save(out, format="WEBP", quality=90)
        else:
            img.save(out, format="JPEG", quality=JPEG_QUALITY)
        return out.getvalue()
