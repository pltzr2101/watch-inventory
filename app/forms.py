"""Serverseitige Validierung und Aufbereitung der Watch-Eingabeformulare."""
from __future__ import annotations

import re
import secrets

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import BOX_OPTIONS, ExtraCostCategory, Watch, WatchExtraCost, WatchStatus
from .money import MoneyError, RateError, parse_money, parse_rate, usd_to_eur
from .services.wecatalog import parse_specs  # noqa: F401 – weiterhin Import-Preview/Specs-Text

SKU_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,31}$")
MAX_TEXT = {
    "title": 200,
    "brand": 120,
    "model": 160,
    "condition": 80,
    "factory": 80,
    "version": 40,
    "band": 80,
    "highlight": 160,
}
MAX_LONG = 20_000
MAX_NOTES = 40_000
MAX_EXTRA_COST_ROWS = 30

# Technische Daten: feste, anhakbare Checkliste (Haken ⇒ Wert ist Pflicht).
SPEC_FIELDS: list[tuple[str, str]] = [
    ("kaliber", "Kaliber"),
    ("gehaeusedurchmesser", "Gehäusedurchmesser"),
    ("werk", "Werk"),
]
SPEC_LABELS = {label for _, label in SPEC_FIELDS}
SPEC_SUGGESTIONS = {
    "werk": ["Automatik", "Handaufzug", "Quarz"],
}

STATUS_VALUES = {status.value for status in WatchStatus}


def compose_title(
    brand: str | None,
    factory: str | None,
    version: str | None,
    model: str | None,
    band: str | None,
    highlight: str | None,
) -> str:
    """Setzt den Titel aus den Bausteinen zusammen.

    Muster: „Marke Factory – Version – Modell, Band, Farbe/Besonderheit“.
    Leere Bausteine und damit überflüssige Trenner entfallen.
    """
    head = " ".join(p for p in (brand, factory) if p)
    headline = [part for part in (head, version, model) if part]
    suffix = [part for part in (band, highlight) if part]
    title = " – ".join(headline)
    if suffix:
        tail = ", ".join(suffix)
        title = f"{title}, {tail}" if title else tail
    return title


def parse_watch_form(form) -> tuple[dict, dict[str, str]]:  # type: ignore[no-untyped-def]
    """Validiert ein Watch-Formular → (Werte, Feldfehler)."""
    values: dict = {}
    errors: dict[str, str] = {}

    def text(key: str, max_len: int, required: bool = False) -> None:
        raw = str(form.get(key) or "").strip()
        if required and not raw:
            errors[key] = "Pflichtfeld."
        elif len(raw) > max_len:
            errors[key] = f"Maximal {max_len} Zeichen."
        else:
            values[key] = raw or None

    text("title", MAX_TEXT["title"])
    text("brand", MAX_TEXT["brand"])
    text("model", MAX_TEXT["model"])
    text("factory", MAX_TEXT["factory"])
    text("version", MAX_TEXT["version"])
    text("band", MAX_TEXT["band"])
    text("highlight", MAX_TEXT["highlight"])
    text("condition", MAX_TEXT["condition"])
    text("shipping_info", MAX_LONG)
    text("internal_notes", MAX_NOTES)

    # Titel setzt sich aus den Bausteinen zusammen; eine manuelle Eingabe
    # gewinnt. Fehlt beides, ist der Titel Pflicht.
    values["title"] = (values.get("title") or "") or compose_title(
        values.get("brand"),
        values.get("factory"),
        values.get("version"),
        values.get("model"),
        values.get("band"),
        values.get("highlight"),
    )
    if not values["title"]:
        errors["title"] = "Titel fehlt – mindestens Marke, Factory oder Modell angeben."

    status_raw = str(form.get("status") or "").strip()
    if status_raw not in STATUS_VALUES:
        errors["status"] = "Unbekannter Status."
    else:
        values["status"] = status_raw

    sku_raw = str(form.get("sku") or "").strip()
    if sku_raw and not SKU_RE.match(sku_raw):
        errors["sku"] = (
            "Artikelnummer: 2–32 Zeichen aus A–Z, a–z, 0–9, Punkt, Bindestrich, Unterstrich."
        )
    else:
        values["sku"] = sku_raw or None

    box_raw = str(form.get("box") or "").strip()
    if box_raw and box_raw not in BOX_OPTIONS:
        errors["box"] = "Unbekannte Box-Angabe."
    else:
        values["box_option"] = box_raw or None

    # Einkauf: USD-Betrag ist die Grundlage; der Kurs (EUR pro USD) muss
    # angegeben sein, sobald USD gepflegt ist (oder knopfgesteuert ermittelt).
    try:
        values["purchase_price_usd"] = parse_money(str(form.get("purchase_price") or ""))
    except MoneyError as exc:
        errors["purchase_price"] = str(exc)
    try:
        values["usd_eur_rate"] = parse_rate(str(form.get("usd_eur_rate") or ""))
    except RateError as exc:
        errors["usd_eur_rate"] = str(exc)
    if values.get("purchase_price_usd") is not None and not values.get("usd_eur_rate"):
        errors.setdefault("usd_eur_rate", "Kurs zum Einkaufspreis erforderlich.")
    if values.get("purchase_price_usd") is None:
        values["usd_eur_rate"] = None  # Kurs nur bei USD-Eingabe relevant

    for key in ("target_price", "sale_price"):
        try:
            values[key] = parse_money(str(form.get(key) or ""))
        except MoneyError as exc:
            errors[key] = str(exc)

    # Zusätzliche Einkaufskosten als Einzelpositionen (Betrags- + Kategoriefeld).
    costs: list[tuple[int, str]] = []
    try:
        rows = int(str(form.get("extra_cost_rows") or "0"))
    except ValueError:
        rows = 0
    if rows < 0 or rows > MAX_EXTRA_COST_ROWS:
        errors["extra_costs"] = "Ungültige Anzahl Kostenpositionen."
        rows = 0
    for i in range(rows):
        amount_raw = str(form.get(f"extra_cost_amount_{i}") or "").strip()
        category_raw = str(form.get(f"extra_cost_category_{i}") or "").strip()
        if not amount_raw and not category_raw:
            continue  # leere Zeile ignorieren
        try:
            amount = parse_money(amount_raw)
        except MoneyError as exc:
            errors[f"extra_cost_{i}"] = str(exc)
            continue
        if amount is None:
            errors[f"extra_cost_{i}"] = "Betrag fehlt."
            continue
        if category_raw not in {c.value for c in ExtraCostCategory}:
            errors[f"extra_cost_{i}"] = "Bitte eine Kostenart wählen."
            continue
        costs.append((amount, category_raw))
    values["extra_costs"] = costs

    # Technische Daten: Checkliste – angehaktes Feld ist Pflicht.
    specs: list[dict] = []
    for key, label in SPEC_FIELDS:
        checked = str(form.get(f"spec_on_{key}") or "") in {"on", "1", "true"}
        value = str(form.get(f"spec_{key}") or "").strip()
        if checked and not value:
            errors[f"spec_{key}"] = "Angehaktes Feld muss befüllt werden."
        elif checked and value:
            specs.append({"key": label, "value": value[:200]})
    values["specs"] = specs

    values["public_approved"] = str(form.get("public_approved") or "") in {"on", "1", "true"}
    return values, errors


def gen_sku(session: Session) -> str:
    """Lesbare, kollisionsfreie Artikelnummer (U-0001, U-0002, …)."""
    highest = session.scalar(select(func.max(Watch.sku)).where(Watch.sku.like("U-%")))
    start = 1
    if highest and len(highest) > 2 and highest[2:].isdigit():
        start = int(highest[2:]) + 1
    for number in range(start, start + 100_000):
        candidate = f"U-{number:04d}"
        exists = session.scalar(select(Watch.id).where(Watch.sku == candidate))
        if not exists:
            return candidate
    return f"U-{secrets.token_hex(3).upper()}"


def apply_watch_values(watch: Watch, values: dict) -> None:
    """Schreibt validierte Formularwerte auf das Watch-Modell (kein Status-Hook)."""
    watch.title = values["title"]
    watch.brand = values.get("brand")
    watch.model = values.get("model")
    watch.factory = values.get("factory")
    watch.version = values.get("version")
    watch.band = values.get("band")
    watch.highlight = values.get("highlight")
    watch.condition = values.get("condition")
    watch.box_option = values.get("box_option")
    watch.shipping_info = values.get("shipping_info")
    watch.internal_notes = values.get("internal_notes")

    # Einkauf: EUR abgeleitet aus USD × historischem Kurs; ohne USD-Eingabe
    # bleibt kein Einkaufspreis (Alt-Daten wurden vor der USD-Ära gepflegt).
    usd = values.get("purchase_price_usd")
    rate = values.get("usd_eur_rate")
    watch.purchase_price_usd_cents = usd
    watch.usd_eur_rate_milli = rate
    watch.purchase_price_cents = usd_to_eur(usd, rate)

    watch.target_price_cents = values.get("target_price")
    watch.sale_price_cents = values.get("sale_price")

    # Zusatzkosten komplett neu aufsetzen (Einzelpositionen).
    watch.extra_costs.clear()
    for amount, category in values.get("extra_costs") or []:
        watch.extra_costs.append(
            WatchExtraCost(category=ExtraCostCategory(category), amount_cents=amount)
        )

    # Specs: Checklisten-Werte ersetzen ihre Labels, fremde Alt-Einträge
    # (z. B. aus Katalogimporten) bleiben erhalten.
    incoming = values.get("specs") or []
    incoming_labels = {item["key"] for item in incoming}
    kept = [
        item
        for item in (watch.specs or [])
        if isinstance(item, dict) and item.get("key") not in incoming_labels | SPEC_LABELS
    ]
    watch.specs = kept + incoming
    watch.public_approved = bool(values.get("public_approved"))
