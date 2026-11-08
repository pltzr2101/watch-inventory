"""Heuristische Bildrollen-Erkennung für eine sinnvolle Exportreihenfolge.

Reine Filename-Keywords (DE/EN) – bewusst kein ML. Ziel: für Käufer logische
Reihenfolge (Dial → Gesamtansicht → Gehäuse → Seite → Rücken → Schließe →
Armband → Papiere → Box → Handgelenk) und automatische Dateinamen wie
`01-dial.jpg`. Manuelle Eingriffe haben immer Vorrang: Labels werden nur
gefüllt, wenn sie leer sind; die Sortierung kann in der UI korrigiert werden.
"""
from __future__ import annotations

import unicodedata

from ..models import WatchImage

# (Rolle, Priorität, Keywords) – Reihenfolge = Exportreihenfolge (klein = vorne).
_ROLES: tuple[tuple[str, int, tuple[str, ...]], ...] = (
    ("dial", 10, ("dial", "zifferblatt", "face", "macro", "closeup", "nahaufnahme")),
    ("front", 20, ("front", "frontal", "vorne", "gesamt", "whole", "komplett")),
    ("case", 30, ("case", "gehaeuse", "gehaeus", "midcase", "rehaut")),
    ("side", 40, ("side", "profil", "profile", "seite", "flank")),
    ("back", 50, ("back", "rueck", "caseback", "rear", "deckel")),
    ("clasp", 60, ("clasp", "schlie", "buckle", "deployant", "falt")),
    ("bracelet", 70, ("bracelet", "armband", "glied", "link")),
    ("papers", 80, ("paper", "papiere", "karte", "card", "zertifikat", "cert")),
    ("box", 90, ("box", "kiste", "fullset", "etui")),
    ("wrist", 95, ("wrist", "traeger", "am-hand")),
)

_UNKNOWN_PRIORITY = 100


def _normalize(name: str) -> str:
    text = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode("ascii")
    return text.lower()


def classify(original_name: str | None) -> tuple[str, int]:
    """→ (Rolle, Priorität); unbekannt = ("bild", 100)."""
    haystack = _normalize(original_name or "")
    for role, priority, keywords in _ROLES:
        if any(keyword in haystack for keyword in keywords):
            return role, priority
    return "bild", _UNKNOWN_PRIORITY


def auto_order(images: list[WatchImage]) -> None:
    """Sortiert nach Rolle/Priorität (stabil) und füllt ausschließlich leere Labels."""
    ranked = []
    for position, image in enumerate(images):
        role, priority = classify(image.original_name)
        ranked.append((priority, int(image.sort_order), position, role, image))
    ranked.sort(key=lambda item: (item[0], item[1], item[2]))
    for new_order, (_, _, _, role, image) in enumerate(ranked):
        image.sort_order = new_order
        if not image.label and role != "bild":
            image.label = role
