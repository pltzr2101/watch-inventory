"""Serverseitige Validierung und Aufbereitung der Watch-Eingabeformulare."""
from __future__ import annotations

import re
import secrets

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Watch, WatchStatus
from .money import MoneyError, parse_money
from .services.wecatalog import parse_specs

SKU_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,31}$")
MAX_TEXT = {"title": 200, "brand": 120, "model": 160, "condition": 80}
MAX_LONG = 20_000
MAX_NOTES = 40_000

STATUS_VALUES = {status.value for status in WatchStatus}


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

    text("title", MAX_TEXT["title"], required=True)
    text("brand", MAX_TEXT["brand"])
    text("model", MAX_TEXT["model"])
    text("condition", MAX_TEXT["condition"])
    text("condition_description", MAX_LONG)
    text("included_accessories", MAX_LONG)
    text("shipping_info", MAX_LONG)
    text("description", MAX_LONG)
    text("extra_costs_note", 500)
    text("internal_notes", MAX_NOTES)

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

    for key in (
        "purchase_price",
        "extra_costs",
        "target_price",
        "sale_price",
    ):
        try:
            values[key] = parse_money(str(form.get(key) or ""))
        except MoneyError as exc:
            errors[key] = str(exc)

    specs_raw = str(form.get("specs_text") or "")
    try:
        values["specs"] = parse_specs(specs_raw)
        if len(values["specs"]) > 60:
            errors["specs_text"] = "Maximal 60 Datenzeilen."
    except ValueError as exc:
        errors["specs_text"] = str(exc)

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
    watch.condition = values.get("condition")
    watch.condition_description = values.get("condition_description")
    watch.included_accessories = values.get("included_accessories")
    watch.shipping_info = values.get("shipping_info")
    watch.description = values.get("description")
    watch.extra_costs_note = values.get("extra_costs_note")
    watch.internal_notes = values.get("internal_notes")
    watch.purchase_price_cents = values.get("purchase_price")
    watch.extra_costs_cents = values.get("extra_costs")
    watch.target_price_cents = values.get("target_price")
    watch.sale_price_cents = values.get("sale_price")
    watch.specs = values.get("specs") or []
    watch.public_approved = bool(values.get("public_approved"))
