"""ZIP-Erzeugung mit Schutz vor Pfad-Traversal und ZIP-Slip.

Archivnamen werden strikt bereinigt: nur Basisknamen aus einer eigenen
Zeichenliste, kein Verzeichnisanteil, keine „..“-Komponenten, Duplikate
erhalten ein Suffix. Es werden ausschließlich selbst erzeugte Einträge
geschrieben (keine hochgeladenen Pfade).
"""
from __future__ import annotations

import io
import re
import zipfile

SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_arcname(name: str) -> str:
    """Reduziert einen beliebigen Namen auf einen sicheren Basisknamen."""
    base = str(name).replace("\\", "/").split("/")[-1]
    base = SAFE_NAME_RE.sub("-", base)
    base = base.lstrip(".")  # versteckte Dateien & führende ".." entfernen
    base = base.strip("-.")
    if not base:
        return "datei"
    return base[:120]


def build_zip(entries: list[tuple[str, bytes]]) -> bytes:
    """Erzeugt ein ZIP aus (Wunschname, Inhalt) mit bereinigten, eindeutigen Namen."""
    buffer = io.BytesIO()
    seen: set[str] = set()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries:
            candidate = safe_arcname(name)
            if candidate in seen:
                stem, dot, ext = candidate.rpartition(".")
                if not dot:
                    stem, ext = candidate, ""
                counter = 2
                while f"{stem}-{counter}{dot}{ext}" in seen:
                    counter += 1
                candidate = f"{stem}-{counter}{dot}{ext}"
            seen.add(candidate)
            info = zipfile.ZipInfo(candidate, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, data)
    return buffer.getvalue()
