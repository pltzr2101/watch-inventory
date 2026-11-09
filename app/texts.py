"""Feste Zustands- und Versandoptionen mit DE/EN-Übersetzungen.

Anlegen/Verkaufsformulare speichern NUR die Schlüssel; der Listing-Generator
liefert je Plattformsprache den fertigen Text (siehe PLATFORM_LANG). So
landet nie wieder englischer Freitext in einem deutschen Template.

Plattformsprachen-Anpassung: PLATFORM_LANG (de/en) – hier ändern.
"""

from __future__ import annotations

CONDITION_OPTIONS: list[tuple[str, str, str]] = [
    # (schlüssel, de, en)
    ("neu_foliert", "Neu, vollständig foliert", "New, fully stickered"),
    ("neu", "Neu", "New"),
    ("sehr_gut", "Sehr gut", "Very good"),
    ("gut", "Gut", "Good"),
    ("gebraucht", "Gebraucht", "Pre-owned"),
]

SHIPPING_OPTIONS: list[tuple[str, str, str]] = [
    ("dhl_de_incl", "Versand innerhalb Deutschlands per DHL inklusive",
     "Free DHL shipping within Germany"),
    ("eu_by_arrangement", "EU-Versand nach Absprache", "EU shipping by arrangement"),
    ("worldwide_by_arrangement", "Weltweiter Versand nach Absprache",
     "Worldwide shipping by arrangement"),
]

# Plattformsprache der eingebauten Templates (Englisch-Reddits, deutsche FB/TG).
PLATFORM_LANG: dict[str, str] = {
    "facebook": "de",
    "telegram": "de",
    "reddit": "en",
    "reddit_code": "en",
}

_CONDITION_KEYS = {k for k, _, _ in CONDITION_OPTIONS}
_SHIPPING_KEYS = {k for k, _, _ in SHIPPING_OPTIONS}


def is_condition_key(value: str | None) -> bool:
    return value in _CONDITION_KEYS


def is_shipping_key(value: str | None) -> bool:
    return value in _SHIPPING_KEYS


def _lookup(options: list[tuple[str, str, str]], key: str | None, lang: str) -> str | None:
    if not key:
        return None
    for k, de, en in options:
        if k == key:
            return de if lang == "de" else en
    return None


def condition_text(key: str | None, lang: str = "de") -> str | None:
    return _lookup(CONDITION_OPTIONS, key, lang)


def shipping_text(key: str | None, lang: str = "de") -> str | None:
    return _lookup(SHIPPING_OPTIONS, key, lang)


def translated(options: list[tuple[str, str, str]], value: str | None, lang: str) -> str | None:
    """Schlüssel → übersetzter Text; unbekannte Alt-/Quellwerte roh durchreichen."""
    if not value:
        return None
    return _lookup(options, value, lang) or value


def normalize_condition(raw: str | None) -> str | None:
    """Ordnet alte Freitexte aus der Migration eindeutigen Schlüsseln zu."""
    if not raw:
        return None
    text = raw.casefold()
    if "sticker" in text or "foliert" in text:
        return "neu_foliert"
    if "neu" in text or text.startswith("new") or "mint" in text:
        return "neu"
    if "sehr gut" in text or "very good" in text or "exzellent" in text:
        return "sehr_gut"
    if text in {"gut", "good", "ok", "okay"}:
        return "gut"
    if any(w in text for w in ("gebraucht", "used", "pre-owned", "pre owned", "getragen")):
        return "gebraucht"
    return None


def normalize_shipping(raw: str | None) -> str | None:
    """Ordnet alte Versandfreitexte den Schlüsseln zu (sonst None)."""
    if not raw:
        return None
    text = raw.casefold()
    if "dhl" in text and ("eu" not in text and "welt" not in text and "world" not in text):
        return "dhl_de_incl"
    if "welt" in text or "world" in text or "international" in text:
        return "worldwide_by_arrangement"
    if "eu" in text or "europä" in text:
        return "eu_by_arrangement"
    generisch = "versand" in text or "shipping" in text
    inklusive = "inkl" in text or "free" in text or "gratis" in text or "kostenlos" in text
    if generisch and inklusive:
        return "dhl_de_incl"
    return None
