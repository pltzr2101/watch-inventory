"""Anzeigengenerator: platziertspezifische Textentwürfe aus Bestandsdaten.

Regeln:
- Nur gespeicherte bzw. ausdrücklich bestätigte Daten; nichts erfinden.
- Nicht gepflegte Felder fehlen im Text (und werden in `missing_fields`
  als Hinweis an die UI gemeldet), statt geraten zu werden.
- Vertrauliche Daten (Einkaufspreise, Notizen, Quell-Snapshots) landen nie
  im Kontext und damit nie im Anzeigentext.
- Importierte Kataloginhalte nur nach ausdrücklicher Freigabe
  (`import_approved_for_listing`).
- Die Vorlagen sind austauschbare Dateien (siehe listing_templates/).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from jinja2 import ChoiceLoader, Environment, FileSystemLoader, StrictUndefined

from ..models import ListingPlatform, Provenance, Watch
from ..money import format_money

DEFAULT_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "listing_templates"
PLATFORM_LABELS = {
    ListingPlatform.REDDIT: "Reddit",
    ListingPlatform.FACEBOOK: "Facebook-Gruppe",
}

# Felder, die überhaupt erst in Anzeigenkontext gelangen dürfen.
_PUBLIC_SAFE_FIELDS = (
    "title",
    "brand",
    "model",
    "condition",
    "condition_description",
    "included_accessories",
    "shipping_info",
    "description",
    "specs",
)


@dataclass
class GeneratedListing:
    platform: ListingPlatform
    title: str
    body: str
    missing_fields: list[str]
    hints: str | None = None


def source_backed(watch: Watch, field: str) -> bool:
    """True, wenn der Wert aus dem Katalog-Import stammt."""
    return field in (watch.source_fields or [])


def listing_approved(watch: Watch) -> bool:
    """Importierte Inhalte erst nach ausdrücklicher Bestätigung nutzbar."""
    if watch.provenance != Provenance.WECATALOG:
        return True
    return bool(watch.import_approved_for_listing)


def build_context(watch: Watch) -> dict:
    """Baut den ausschließlich freigegebenen Template-Kontext inkl. Fehlliste."""
    missing: list[str] = []
    approved = listing_approved(watch)

    def take(field: str, label: str, value):  # type: ignore[no-untyped-def]
        if source_backed(watch, field) and not approved:
            missing.append(f"{label} (Katalog-Inhalt – noch nicht für Anzeigen freigegeben)")
            return None
        if value in (None, "", [], {}):
            missing.append(label)
            return None
        return value

    asking_price_cents = watch.target_price_cents
    if asking_price_cents is None:
        asking_price_cents = watch.sale_price_cents
    price_str = None
    if asking_price_cents is None:
        missing.append("Verkaufspreis")
    else:
        price_str = format_money(asking_price_cents)

    specs = take("specs", "Technische Daten", watch.specs)
    specs_list = []
    if specs:
        specs_list = [
            {"key": str(item.get("key", "")).strip(), "value": str(item.get("value", "")).strip()}
            for item in specs
            if isinstance(item, dict) and str(item.get("key", "")).strip()
        ]
        if not specs_list:
            specs_list = []
            missing.append("Technische Daten")

    title = take("title", "Titel", watch.title)

    context = {
        "title": title,
        "sku": watch.sku,
        "brand": take("brand", "Marke", watch.brand),
        "model": take("model", "Modell", watch.model),
        "price": price_str,
        "condition": take("condition", "Zustand", watch.condition),
        "condition_description": take(
            "condition_description", "Zustandsbeschreibung", watch.condition_description
        ),
        "accessories": take(
            "included_accessories", "Lieferumfang", watch.included_accessories
        ),
        "shipping_info": take("shipping_info", "Versandinformationen", watch.shipping_info),
        "description": take("description", "Beschreibung", watch.description),
        "specs": specs_list,
        "missing_fields": missing,
    }
    # Defense in depth: nur die Template-Schlüssel durchreichen – vertrauliche
    # Felder (Einkauf, Notizen, Quell-Snapshot) sind gar nicht Teil des Kontexts.
    allowed = {
        "title",
        "brand",
        "model",
        "price",
        "condition",
        "condition_description",
        "accessories",
        "shipping_info",
        "description",
        "specs",
        "sku",
        "missing_fields",
    }
    return {key: value for key, value in context.items() if key in allowed}


def _environment(settings) -> Environment:  # type: ignore[no-untyped-def]
    loaders = []
    if settings.listing_template_dir:
        loaders.append(FileSystemLoader(str(settings.listing_template_dir)))
    loaders.append(FileSystemLoader(str(DEFAULT_TEMPLATE_DIR)))
    return Environment(
        loader=ChoiceLoader(loaders),
        autoescape=False,
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )


def load_hints(settings) -> dict[str, str]:  # type: ignore[no-untyped-def]
    """Plattformspezifische Hinweise aus hints.json (konfigurierbar)."""
    for directory in filter(None, [settings.listing_template_dir, DEFAULT_TEMPLATE_DIR]):
        path = Path(directory) / "hints.json"
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return {str(k): str(v) for k, v in data.items()}
            except (OSError, json.JSONDecodeError):
                continue
    return {}


def generate(watch: Watch, platform: ListingPlatform, settings) -> GeneratedListing:
    """Erzeugt Titel und Textentwurf aus den tatsächlich gepflegten Daten."""
    if platform not in (ListingPlatform.REDDIT, ListingPlatform.FACEBOOK):
        raise ValueError(f"Unbekannte Plattform: {platform}")
    context = build_context(watch)
    if context["title"] is None:
        raise ValueError(
            "Kein freigegebener Titel vorhanden – Katalog-Inhalte zuerst bestätigen "
            "oder einen eigenen Titel hinterlegen."
        )

    env = _environment(settings)
    body = env.get_template(f"{platform.value}.txt.j2").render(**context)

    parts = [str(context["title"]).strip()]
    if context["price"]:
        parts.append(str(context["price"]))
    title = " | ".join(parts)

    return GeneratedListing(
        platform=platform,
        title=title,
        body=body.strip() + "\n",
        missing_fields=context["missing_fields"],
        hints=load_hints(settings).get(platform.value),
    )
