"""Serverseitige Validierung und Aufbereitung der Watch-Eingabeformulare."""
from __future__ import annotations

import re
import secrets

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import BOX_OPTIONS, CostCategory, Watch, WatchExtraCost, WatchStatus
from .money import (
    MoneyError,
    PercentError,
    RateError,
    parse_money,
    parse_percent,
    parse_rate,
    percent_amount,
    usd_to_eur,
)
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


def category_choices(db) -> list[dict]:  # type: ignore[no-untyped-def]
    """Alle Kategorien für Dropdowns: [{id, label, archived}] in Anzeige-Reihenfolge."""
    from sqlalchemy import select

    rows = db.scalars(
        select(CostCategory).order_by(CostCategory.sort_order, CostCategory.label)
    ).all()
    return [
        {"id": c.id, "label": c.label, "archived": c.archived_at is not None}
        for c in rows
    ]


def category_ids(db) -> set[str]:  # type: ignore[no-untyped-def]
    """Alle Kategorie-IDs (inkl. archiviert) für die Formular-Validierung."""
    return {c["id"] for c in category_choices(db)}


def parse_watch_form(
    form, valid_category_ids: set[str] | None = None  # type: ignore[no-untyped-def]
) -> tuple[dict, dict[str, str]]:
    """Validiert ein Watch-Formular → (Werte, Feldfehler).

    `valid_category_ids`: alle (auch archivierten) Kategorie-IDs – None = keine
    Prüfung (nur für direkte Unit-Tests; Router übergeben echte IDs).
    """
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
    for key in ("target_price", "sale_price"):
        try:
            values[key] = parse_money(str(form.get(key) or ""))
        except MoneyError as exc:
            errors[key] = str(exc)

    # Zusätzliche Einkaufskosten als Einzelpositionen:
    # Kategorie (DB-ID) – Währung – entweder fixer Betrag ODER Basis × Prozent.
    costs: list[dict] = []
    try:
        rows = int(str(form.get("extra_cost_rows") or "0"))
    except ValueError:
        rows = 0
    if rows < 0 or rows > MAX_EXTRA_COST_ROWS:
        errors["extra_costs"] = "Ungültige Anzahl Kostenpositionen."
        rows = 0
    for i in range(rows):
        cat = str(form.get(f"extra_cost_category_{i}") or "").strip()
        currency = str(form.get(f"extra_cost_currency_{i}") or "").strip().upper()
        amount_raw = str(form.get(f"extra_cost_amount_{i}") or "").strip()
        basis_raw = str(form.get(f"extra_cost_basis_{i}") or "").strip()
        pct_raw = str(form.get(f"extra_cost_pct_{i}") or "").strip()
        if not any((cat, amount_raw, basis_raw, pct_raw)):
            continue  # leere Zeile ignorieren
        row_err: str | None = None
        if not cat:
            row_err = "Kategorie wählen."
        elif valid_category_ids is not None and cat not in valid_category_ids:
            row_err = "Unbekannte Kategorie."
        elif currency not in {"USD", "EUR"}:
            row_err = "Währung USD oder EUR wählen."

        pct: int | None = None
        basis: int | None = None
        amount: int | None = None
        if row_err is None and pct_raw:
            try:
                pct = parse_percent(pct_raw)
            except PercentError as exc:
                row_err = str(exc)
            if row_err is None and basis_raw:
                try:
                    basis = parse_money(basis_raw)
                except MoneyError as exc:
                    row_err = str(exc)
                if basis is None:
                    row_err = "Basiswert fehlt (für den Prozent-Modus)."
            elif row_err is None:
                row_err = "Basiswert fehlt (für den Prozent-Modus)."
            if row_err is None and amount_raw:
                row_err = "Bitte entweder Betrag ODER Basis × % angeben – nicht beides."
            if row_err is None and pct is not None and basis is not None:
                amount = percent_amount(basis, pct)
        elif row_err is None:
            if basis_raw:
                row_err = "Prozentwert fehlt (Basis nur im %-Modus)."
            else:
                try:
                    amount = parse_money(amount_raw)
                except MoneyError as exc:
                    row_err = str(exc)
                if row_err is None and amount is None:
                    row_err = "Betrag fehlt."
        if row_err:
            errors[f"extra_cost_{i}"] = row_err
            continue
        costs.append(
            {
                "category_id": cat,
                "currency": currency,
                "amount_cents": amount,
                "basis_cents": basis,
                "percent_milli": pct,
            }
        )
    values["extra_costs"] = costs

    # Ein Kurs pro Uhr: gilt für ALLE USD-Anteile (EK UND Positionen).
    usd_present = values.get("purchase_price_usd") is not None or any(
        c["currency"] == "USD" for c in costs
    )
    if usd_present and not values.get("usd_eur_rate"):
        errors.setdefault(
            "usd_eur_rate", "Kurs erforderlich – es sind USD-Beträge vorhanden."
        )
    if not usd_present:
        values["usd_eur_rate"] = None

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

    # Zusatzkosten komplett neu aufsetzen (Einzelpositionen inkl. Währung/Basis).
    watch.extra_costs.clear()
    for cost in values.get("extra_costs") or []:
        watch.extra_costs.append(
            WatchExtraCost(
                category_id=cost["category_id"],
                currency=cost["currency"],
                amount_cents=cost["amount_cents"],
                basis_cents=cost["basis_cents"],
                percent_milli=cost["percent_milli"],
            )
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
