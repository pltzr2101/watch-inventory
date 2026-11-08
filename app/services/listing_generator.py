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

from jinja2 import ChoiceLoader, Environment, FileSystemLoader, StrictUndefined, TemplateNotFound

from ..models import ListingPlatform, Provenance, Watch
from ..money import format_money

DEFAULT_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "listing_templates"
PLATFORM_LABELS = {
    ListingPlatform.REDDIT: "Reddit",
    ListingPlatform.REDDIT_CODE: "Reddit (Seller-Code)",
    ListingPlatform.FACEBOOK: "Facebook-Gruppe",
    ListingPlatform.TELEGRAM: "Telegram",
}

# Felder, die überhaupt erst in Anzeigenkontext gelangen dürfen.
TEMPLATE_CONTEXT_KEYS = frozenset(
    {
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
        "factory",
        "movement",
        "box",
        "sku",
        "missing_fields",
    }
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

    def spec_value(*wanted: str) -> str | None:
        lowered = {w.lower() for w in wanted}
        for item in specs_list:
            if item["key"].lower() in lowered:
                return item["value"]
        return None

    # Factory/Movement als bequeme Zugriffe auf die technischen Daten;
    # nur aus freigegebenen/gepflegten Specs ableitbar (nie raten).
    factory = spec_value("factory", "hersteller", "manufacturer") if specs else None
    movement = spec_value("movement", "werk", "kaliber", "caliber", "calibre") if specs else None
    if factory is None and specs:
        missing.append("Factory (Hersteller)")
    if movement is None and specs:
        missing.append("Werk (Movement)")

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
        "factory": factory,
        "movement": movement,
        # Box-Option: None = nicht angegeben → Templates lassen die Angabe weg.
        "box": watch.box_option,
        "missing_fields": missing,
    }
    # Defense in depth: nur die Template-Schlüssel durchreichen – vertrauliche
    # Felder (Einkauf, Notizen, Quell-Snapshot) sind gar nicht Teil des Kontexts.
    return {key: value for key, value in context.items() if key in TEMPLATE_CONTEXT_KEYS}


def sample_context() -> dict:
    """Vollständiger Beispielkontext für Vorlagenvalidierung und -vorschau.

    Enthält ausschließlich unverbindliche Beispielwerte und niemals echte
    Bestands- oder vertrauliche Daten (Credentials/Preise etc. ersinnen).
    """
    base: dict = dict.fromkeys(TEMPLATE_CONTEXT_KEYS)
    base.update(
        {
            "title": "Clean Factory Submariner 126610LN",
            "sku": "U-0001",
            "brand": "Clean Factory",
            "model": "Submariner 126610LN",
            "price": "1.234,56 €",
            "condition": "Neu",
            "condition_description": "Ungetragen, Etiketten vorhanden.",
            "accessories": "Box, Papiere, volle Armbandlänge",
            "shipping_info": "DHL versichert, 1–2 Tage nach Zahlung",
            "description": "Beispielhafte Kurzbeschreibung.",
            "specs": [{"key": "Kaliber", "value": "Clone VS352"}],
            "factory": "Clean Factory",
            "movement": "VS352",
            "box": "optional",
            "missing_fields": [],
        }
    )
    return base


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
    if platform not in PLATFORM_LABELS:
        raise ValueError(f"Unbekannte Plattform: {platform}")
    context = build_context(watch)
    if context["title"] is None:
        raise ValueError(
            "Kein freigegebener Titel vorhanden – Katalog-Inhalte zuerst bestätigen "
            "oder einen eigenen Titel hinterlegen."
        )

    env = _environment(settings)
    body = env.get_template(f"{platform.value}.txt.j2").render(**context)

    # Titelvorlage <plattform>.title.txt.j2 (optional); sonst klassische
    # Zusammenstellung Titel | Preis (wird durch Uploads/Editor überschreibbar).
    try:
        rendered_title = env.get_template(f"{platform.value}.title.txt.j2").render(**context)
        title = rendered_title.strip()
    except TemplateNotFound:
        parts = [str(context["title"]).strip()]
        if context["price"]:
            parts.append(str(context["price"]))
        title = " | ".join(parts)
    if not title:
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
