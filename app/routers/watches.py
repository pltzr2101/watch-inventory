"""CRUD, Filter/Suche, Statushistorik, Archiv/Löschen für den Bestand."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import or_, select

from ..csrf import verify_csrf
from ..deps import DbDep, SettingsDep, load_watch
from ..flash import flash
from ..forms import (
    SPEC_FIELDS,
    apply_watch_values,
    category_choices,
    category_ids,
    gen_sku,
    parse_watch_form,
)
from ..models import (
    BOX_OPTIONS,
    BoxType,
    StatusEvent,
    Watch,
    WatchStatus,
    utcnow,
)
from ..money import MoneyError, format_money, format_percent, format_rate, parse_money, usd_to_eur
from ..services import image_ingest
from ..services.boxes import avg_unit_cost
from ..services.boxes import overview as box_overview
from ..templating import render
from ..texts import CONDITION_OPTIONS, SHIPPING_OPTIONS, condition_text, shipping_text

router = APIRouter(tags=["watches"])

_MUTATE = [Depends(verify_csrf)]


def _redirect(location: str) -> RedirectResponse:
    return RedirectResponse(location, status_code=303)


def set_status(watch: Watch, to_status: WatchStatus, note: str | None = None) -> None:
    """Statuswechsel inkl. nachvollziehbarem History-Eintrag und Verkaufszeit."""
    if watch.status == to_status:
        return
    watch.status_events.append(
        StatusEvent(from_status=watch.status, to_status=to_status, note=note or None)
    )
    watch.status = to_status
    watch.status_changed_at = utcnow()
    if to_status == WatchStatus.SOLD:
        watch.sold_at = utcnow()
    elif watch.sold_at is not None:
        watch.sold_at = None


def _num(cents: int | None) -> str:
    """Cents → „12,34“ (ohne Währungssuffix)."""
    if cents is None:
        return ""
    whole, frac = divmod(cents, 100)
    return f"{whole}.{frac:02d}".replace(".", ",")


def _watch_form_context(
    watch: Watch | None,
    values: dict | None,
    errors: dict,
    db,  # type: ignore[no-untyped-def]
) -> dict:
    from ..money import format_rate

    categories = category_choices(db)

    if watch is not None and values is None:
        spec_values: dict[str, str] = {}
        for item in watch.specs or []:
            for key, label in SPEC_FIELDS:
                if str(item.get("key", "")) == label:
                    spec_values[key] = str(item.get("value", ""))
        cost_rows = [
            {
                "category_id": c.category_id,
                "currency": c.currency,
                "amount": _num(c.amount_cents),
                "basis": _num(c.basis_cents),
                "pct": format_percent(c.percent_milli)
                if c.percent_milli is not None
                else "",
            }
            for c in watch.extra_costs
        ]
        values = {
            "sku": watch.sku,
            "title": watch.title,
            "brand": watch.brand or "",
            "model": watch.model or "",
            "factory": watch.factory or "",
            "version": watch.version or "",
            "band": watch.band or "",
            "highlight": watch.highlight or "",
            "status": watch.status.value,
            "purchase_price": (
                format_money(watch.purchase_price_usd_cents).replace(" €", "")
                if watch.purchase_price_usd_cents is not None
                else ""
            ),
            "usd_eur_rate": (
                format_rate(watch.usd_eur_rate_milli)
                if watch.usd_eur_rate_milli is not None
                else ""
            ),
            "target_price": (
                format_money(watch.target_price_cents).replace(" €", "")
                if watch.target_price_cents is not None
                else ""
            ),
            "sale_price": (
                format_money(watch.sale_price_cents).replace(" €", "")
                if watch.sale_price_cents is not None
                else ""
            ),
            "condition": watch.condition or "",
            "condition_note": watch.condition_note or "",
            "box_option": watch.box_option or "",
            "shipping_info": watch.shipping_info or "",
            "internal_notes": watch.internal_notes or "",
            "public_approved": watch.public_approved,
            "spec_values": spec_values,
            "cost_rows": cost_rows,
            "extra_cost_rows": str(max(len(cost_rows), 1)),
        }
    else:
        # Neu-Anlage oder Fehler-Rückgabe: parse-Werte renderbar aufbereiten.
        v = dict(values or {})
        # Zerstörungsfreier Roundtrip: parse speichert ints (Cents/Milli),
        # das Formular erwartet aber Eingabe-Strings – sonst sind EK/Kurs/VK
        # nach Fehlern leer oder der Kurs landet als „893" statt „0,893" drin.
        if v.get("purchase_price_usd") is not None:
            v["purchase_price"] = format_money(v["purchase_price_usd"]).replace(" €", "")
        if v.get("usd_eur_rate") is not None:
            v["usd_eur_rate"] = format_rate(v["usd_eur_rate"])
        for _price_key in ("target_price", "sale_price"):
            if v.get(_price_key) is not None:
                v[_price_key] = format_money(v[_price_key]).replace(" €", "")
        if "extra_costs" in v:
            cost_rows = [
                {
                    "category_id": c["category_id"],
                    "currency": c["currency"],
                    "amount": _num(c["amount_cents"]),
                    "basis": _num(c.get("basis_cents")),
                    "pct": format_percent(c["percent_milli"])
                    if c.get("percent_milli") is not None
                    else "",
                }
                for c in v["extra_costs"]
            ]
        else:
            cost_rows = [
                {"category_id": "", "currency": "EUR", "amount": "", "basis": "", "pct": ""}
                for _ in range(max(int(v.get("extra_cost_rows") or 1), 1))
            ]
        spec_values = {}
        for item in v.get("specs") or []:
            for key, label in SPEC_FIELDS:
                if item.get("key") == label:
                    spec_values[key] = item.get("value", "")
        v["cost_rows"] = cost_rows
        v["extra_cost_rows"] = str(max(len(cost_rows), 1))
        v["spec_values"] = spec_values
        values = v
    return {
        "values": values or {},
        "errors": errors or {},
        "statuses": list(WatchStatus),
        "box_options": BOX_OPTIONS,
        "spec_fields": SPEC_FIELDS,
        "categories": categories,
        "condition_options": [(k, de) for k, de, _ in CONDITION_OPTIONS],
        "shipping_options": [(k, de) for k, de, _ in SHIPPING_OPTIONS],
        "watch": watch,
    }


@router.get("/watches")
def watches_list(
    request: Request,
    db=DbDep,
    q: str = "",
    status: str = "",
    archived: str = "",
):
    query = select(Watch).order_by(Watch.updated_at.desc())
    q = q.strip()
    if q:
        pattern = f"%{q}%"
        query = query.where(
            or_(
                Watch.title.ilike(pattern),
                Watch.sku.ilike(pattern),
                Watch.brand.ilike(pattern),
                Watch.model.ilike(pattern),
                Watch.factory.ilike(pattern),
                Watch.internal_notes.ilike(pattern),
            )
        )
    if status and status in {s.value for s in WatchStatus}:
        query = query.where(Watch.status == status)
    if archived == "1":
        query = query.where(Watch.archived_at.is_not(None))
    else:
        query = query.where(Watch.archived_at.is_(None))

    watches = db.scalars(query.limit(500)).all()
    return render(
        request,
        "watches_list.html",
        watches=watches,
        q=q,
        status=status,
        archived=archived == "1",
        statuses=list(WatchStatus),
    )


@router.get("/watches/new")
def watch_new_form(request: Request, db=DbDep):
    return render(request, "watch_form.html", **_watch_form_context(None, None, {}, db))


@router.post("/watches/new", dependencies=_MUTATE)
async def watch_new(request: Request, db=DbDep, settings=SettingsDep):
    form = await request.form()
    values, errors = parse_watch_form(form, category_ids(db))
    # Uploads vollständig VOR dem Datensatz prüfen (kein inkonsistenter Rest):
    prepared, upload_error = await image_ingest.prepare_uploads(form, settings)
    if upload_error:
        errors["files"] = upload_error
    if errors:
        return render(
            request,
            "watch_form.html",
            status_code=400,
            **_watch_form_context(None, values, errors, db),
        )

    watch = Watch()
    apply_watch_values(watch, values)
    watch.sku = values["sku"] or gen_sku(db)
    watch.status = WatchStatus(values["status"])
    watch.status_changed_at = utcnow()
    if watch.status == WatchStatus.SOLD:
        watch.sold_at = utcnow()
    db.add(watch)
    db.flush()
    watch.status_events.append(
        StatusEvent(from_status=None, to_status=watch.status, note="Angelegt")
    )
    try:
        image_ingest.store_prepared(db, watch, prepared, settings)
    except OSError:
        flash(request, "Speichern fehlgeschlagen – es wurden keine Daten übernommen.", "error")
        return render(
            request,
            "watch_form.html",
            status_code=400,
            **_watch_form_context(
                None, values, {**errors, "files": "Dateien konnten nicht gespeichert werden."},
            db,
            ),
        )
    db.commit()
    flash(request, f"Uhr {watch.sku} angelegt.", "success")
    return _redirect(f"/watches/{watch.id}")


@router.get("/api/usd-rate")
def usd_rate(settings=SettingsDep):
    """Aktuellen USD→EUR-Kurs als Vorschlag (manuell überschreibbar).

    Nur auf Knopfdruck abgefragt (kein Hintergrundlauf); `FX_RATE_URL=""`
    deaktiviert den Dienst. Fehler führen zu einem klaren 502-Text.
    """
    import json
    import urllib.error
    import urllib.request

    from ..money import RateError, parse_rate

    if not settings.fx_rate_url:
        raise HTTPException(
            status_code=404, detail="Kursdienst ist deaktiviert (FX_RATE_URL leer)."
        )
    request_obj = urllib.request.Request(  # noqa: S310 – feste konfigurierte URL
        settings.fx_rate_url, headers={"Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(request_obj, timeout=5) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise HTTPException(
            status_code=502, detail=f"Kursdienst nicht erreichbar: {exc}"
        ) from exc
    try:
        rate = float(payload["rates"]["EUR"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=502, detail="Kursdienst lieferte unerwartete Daten."
        ) from exc
    try:
        rate_milli = parse_rate(f"{rate:.4f}")
    except RateError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    from ..money import format_rate

    return {"rate": format_rate(rate_milli), "rate_milli": rate_milli}


# ---------- Charge-Erfassung (Baustein B: Mehrfach-Einkauf) ----------


def _split_even(total_cents: int, n: int) -> list[int]:
    """Verteilt Cent gleichmäßig; der Rest geht auf die letzten Positionen."""
    base, rest = divmod(total_cents, n)
    return [base + (1 if i >= n - rest else 0) for i in range(n)]


class _PrefixedForm:
    """Sicht auf form-Felder mit Präfix („w0_“) – parse_watch_form bleibt unverändert."""

    def __init__(self, form, prefix: str) -> None:
        self._form = form
        self._prefix = prefix

    def get(self, key, default=None):  # type: ignore[no-untyped-def]
        value = self._form.get(self._prefix + key, default)
        if key == "title" and not str(value or "").strip():
            brand = str(self._form.get(self._prefix + "brand") or "").strip()
            model = str(self._form.get(self._prefix + "model") or "").strip()
            combined = f"{brand} {model}".strip()
            return combined or default
        return value


def _cat_default(choices: list, *needles: str) -> str:
    for row in choices:
        text = str(row["label"] or "").lower()
        if any(n in text for n in needles):
            return row["id"]
    return choices[0]["id"] if choices else ""


def _charge_ctx(db, form=None, errors: dict | None = None, charge_count: int = 2) -> dict:
    # SPEC_FIELDS liegt modulweit vor (wie im normalen Formular-Kontext).
    cats = category_choices(db)
    valid = {c["id"] for c in cats}

    def fv(key: str) -> str:
        return str(form.get(key) or "") if form is not None else ""

    def posted(key: str, default: str) -> str:
        return fv(key) or default

    return {
        "categories": cats,
        "spec_fields": SPEC_FIELDS,
        "box_options": BOX_OPTIONS,
        "condition_options": [(k, de) for k, de, _ in CONDITION_OPTIONS],
        "shipping_options": [(k, de) for k, de, _ in SHIPPING_OPTIONS],
        "errors": errors or {},
        "fv": fv,
        "charge_count": max(1, min(20, charge_count)),
        "cat_ship": posted("charge_cat_ship", _cat_default(cats, "versand")),
        "cat_wise": posted("charge_cat_wise", _cat_default(cats, "wise", "weise", "gebühr")),
        "cat_ins": posted("charge_cat_ins", _cat_default(cats, "versicherung")),
        "valid_cat_ids": valid,
        "charge_rate": posted("charge_rate", ""),
        "charge_shipping": posted("charge_shipping", ""),
        "charge_wise": posted("charge_wise", ""),
        "charge_ins_on": form is None
        or str(form.get("charge_ins_on") or "") in {"on", "1", "true"},
        "charge_ins_pct": posted("charge_ins_pct", "6"),
        "charge_ins_cur": posted("charge_ins_cur", "USD"),
        "charge_label": posted("charge_label", ""),
    }


@router.get("/watches/charge")
def charge_form(request: Request, db=DbDep, n: int = 2):  # type: ignore[no-untyped-def]
    return render(request, "watch_charge.html", **_charge_ctx(db, charge_count=n))


@router.post("/watches/charge", dependencies=_MUTATE)
async def charge_create(request: Request, db=DbDep):  # type: ignore[no-untyped-def]
    from datetime import date as _date

    from ..forms import parse_percent
    from ..money import parse_money, parse_rate, percent_amount, usd_to_eur

    form = await request.form()
    try:
        count = int(str(form.get("charge_count") or "0"))
    except ValueError:
        count = 0
    count = max(1, min(20, count))
    ctx = _charge_ctx(db, form=form, errors={}, charge_count=count)
    errors: dict[str, str] = ctx["errors"]
    valid_cat_ids = ctx["valid_cat_ids"]

    try:
        rate = parse_rate(str(form.get("charge_rate") or ""), min_milli=500, max_milli=5000)
    except Exception as exc:  # noqa: BLE001 – RateError-Text sauber ans Formular
        errors["charge_rate"] = str(exc)
        rate = None
    if rate is None and "charge_rate" not in errors:
        errors["charge_rate"] = "Kurs erforderlich (ein Kurs pro Charge)."

    ship_usd = parse_money(str(form.get("charge_shipping") or "")) or 0
    wise_eur = parse_money(str(form.get("charge_wise") or "")) or 0
    ins_on = str(form.get("charge_ins_on") or "") in {"on", "1", "true"}
    ins_pct: int | None = None
    if ins_on:
        try:
            ins_pct = parse_percent(str(form.get("charge_ins_pct") or ""))  # type: ignore[arg-type]
        except Exception as exc:  # noqa: BLE001
            errors["charge_ins_pct"] = str(exc)
        if ins_pct is None and "charge_ins_pct" not in errors:
            errors["charge_ins_pct"] = "Prozentwert fehlt."
    ins_cur = "EUR" if str(form.get("charge_ins_cur") or "USD") == "EUR" else "USD"

    cat_ship = str(form.get("charge_cat_ship") or "")
    cat_wise = str(form.get("charge_cat_wise") or "")
    cat_ins = str(form.get("charge_cat_ins") or "")
    for key, val in (("Versand", cat_ship), ("Gebühren", cat_wise), ("Versicherung", cat_ins)):
        if val not in valid_cat_ids:
            errors["charge_cats"] = f"Unbekannte Kostenkategorie ({key})."
            break

    label = str(form.get("charge_label") or "").strip()[:80] or f"Einkauf {_date.today():%Y-%m-%d}"

    # Erst alle Cluster validieren, dann anlegen (Transaktion bleibt leer bei Fehlern)
    parsed: list[dict] = []
    for i in range(count):
        pf = _PrefixedForm(form, f"w{i}_")
        values, werr = parse_watch_form(pf, valid_cat_ids)
        if werr:
            errors.update({f"uhr{i + 1}_{k}": str(v) for k, v in werr.items()})
            continue
        parsed.append(values)

    if errors:
        return render(
            request,
            "watch_charge.html",
            status_code=400,
            **_charge_ctx(db, form=form, errors=errors, charge_count=count),
        )

    from ..models import StatusEvent, Watch, WatchStatus

    ship_parts = _split_even(ship_usd, count)
    wise_parts = _split_even(wise_eur, count)
    created: list[Watch] = []
    for i, values in enumerate(parsed):
        ek_usd = values.get("purchase_price_usd") or 0
        costs = list(values.get("extra_costs") or [])
        if ship_usd:
            costs.append(
                {"category_id": cat_ship, "currency": "USD",
                 "amount_cents": ship_parts[i], "basis_cents": None, "percent_milli": None}
            )
        if wise_eur:
            costs.append(
                {"category_id": cat_wise, "currency": "EUR",
                 "amount_cents": wise_parts[i], "basis_cents": None, "percent_milli": None}
            )
        if ins_on and ins_pct:
            basis_usd = ek_usd + ship_parts[i]
            basis_i = (usd_to_eur(basis_usd, rate) or 0) if ins_cur == "EUR" else basis_usd
            costs.append(
                {"category_id": cat_ins, "currency": ins_cur,
                 "amount_cents": percent_amount(basis_i, ins_pct),
                 "basis_cents": basis_i, "percent_milli": ins_pct}
            )
        values["extra_costs"] = costs

        watch = Watch()
        apply_watch_values(watch, values)
        watch.sku = values["sku"] or gen_sku(db)
        watch.status = WatchStatus(values.get("status") or "in_transit")
        watch.status_changed_at = utcnow()
        watch.purchase_batch = label
        watch.status_events.append(
            StatusEvent(from_status=None, to_status=watch.status, note=f"Charge: {label}")
        )
        db.add(watch)
        db.flush()
        created.append(watch)
    db.commit()

    def _eur(cents: int, cur: str) -> str:
        eur = cents if cur == "EUR" else (usd_to_eur(cents, rate) or 0)
        from ..money import format_money as _fm

        return _fm(eur)

    parts = []
    if ship_usd:
        parts.append(f"Versand {_eur(ship_usd, 'USD')}")
    if wise_eur:
        parts.append(f"Gebühren {_eur(wise_eur, 'EUR')}")
    if ins_on and ins_pct:
        from ..money import format_percent as _fp

        total_ins = sum(
            c.amount_cents for c in created[0].extra_costs if c.category_id == cat_ins
        )
        parts.append(f"Versicherung {_eur(total_ins, ins_cur)} ({_fp(ins_pct)})")
    flash(
        request,
        f"Charge „{label}“: {len(created)} Uhren angelegt"
        + (" – " + ", ".join(parts) + " anteilig" if parts else "")
        + ". Fotos bitte an den Uhren nachreichen.",
        "success",
    )
    return _redirect(f"/watches/{created[0].id}")


@router.get("/watches/{watch_id}")
def watch_detail(request: Request, watch_id: str, db=DbDep, settings=SettingsDep):
    watch = load_watch(db, watch_id)
    rate = watch.usd_eur_rate_milli
    cost_lines: list[dict] = []
    extra_total = 0
    for c in watch.extra_costs:
        eur = usd_to_eur(c.amount_cents, rate) if c.currency == "USD" else c.amount_cents
        if eur is not None:
            extra_total += eur
        if c.percent_milli is not None and c.basis_cents is not None:
            head = (
                f"{format_percent(c.percent_milli)} % von {_num(c.basis_cents)} "
                f"{c.currency} = {_num(c.amount_cents)} {c.currency}"
            )
        else:
            head = f"{_num(c.amount_cents)} {c.currency}"
        if c.currency == "USD":
            if eur is not None and rate is not None:
                head += f" × {format_rate(rate)} = {format_money(eur)}"
            else:
                head += " – Kurs fehlt, nicht in Summe enthalten"
        cost_lines.append({"label": c.category_label, "detail": head, "eur_cents": eur})
    total_cost = (watch.purchase_price_cents or 0) + extra_total
    # Box-Verkauf: Snapshots gelten nur beim tatsächlichen Verkauf.
    box_ek: int | None = None
    box_share: int | None = None
    if watch.status == WatchStatus.SOLD and watch.box_type_id is not None:
        box_ek = watch.box_unit_cost_cents or 0
        box_share = watch.box_vk_share_cents
    margin = None
    margin_uhr: int | None = None
    margin_box: int | None = None
    margin_note: str | None = None
    if watch.sale_price_cents is not None and watch.purchase_price_cents is not None:
        if box_ek is None:
            margin = watch.sale_price_cents - total_cost
        else:
            margin = watch.sale_price_cents - (total_cost + box_ek)
            if box_share is not None:
                margin_uhr = (watch.sale_price_cents - box_share) - total_cost
                margin_box = box_share - box_ek
            else:
                margin_note = (
                    f"inkl. Box-EK (Ø): {format_money(box_ek)} – kein "
                    "VK-Anteil hinterlegt, daher nur Gesamtmarge"
                )
    box_dialog = [
        row for row in box_overview(db) if row["archived_at"] is None
    ]
    return render(
        request,
        "watch_detail.html",
        watch=watch,
        statuses=list(WatchStatus),
        wecatalog_enabled=settings.wecatalog_enabled,
        cost_lines=cost_lines,
        extra_total_cents=extra_total,
        total_cost_cents=total_cost if watch.purchase_price_cents is not None else None,
        margin_cents=margin,
        margin_uhr_cents=margin_uhr,
        margin_box_cents=margin_box,
        margin_note=margin_note,
        condition_display=condition_text(watch.condition) or watch.condition,
        shipping_display=shipping_text(watch.shipping_info) or watch.shipping_info,
        box_types=box_dialog,
    )


@router.get("/watches/{watch_id}/edit")
def watch_edit_form(request: Request, watch_id: str, db=DbDep):
    watch = load_watch(db, watch_id)
    return render(request, "watch_form.html", **_watch_form_context(watch, None, {}, db))


@router.post("/watches/{watch_id}/edit", dependencies=_MUTATE)
async def watch_edit(request: Request, watch_id: str, db=DbDep):
    watch = load_watch(db, watch_id)
    form = await request.form()
    values, errors = parse_watch_form(form, category_ids(db))
    if errors:
        return render(
            request,
            "watch_form.html",
            status_code=400,
            **_watch_form_context(watch, values, errors, db),
        )

    apply_watch_values(watch, values)
    new_status = WatchStatus(values["status"])
    if new_status == WatchStatus.SOLD and values.get("sale_price") is None:
        return render(
            request,
            "watch_form.html",
            status_code=400,
            **_watch_form_context(
                watch,
                values,
                {**errors, "sale_price": "Endgültiger Verkaufspreis ist für „Verkauft“ Pflicht."},
            db,
            ),
        )
    if new_status != watch.status:
        set_status(watch, new_status, note="Über Bearbeitungsformular")
    # SKU bleibt erhalten, sofern keine gültige neue angegeben wurde:
    if values.get("sku") and values["sku"] != watch.sku:
        taken = db.scalar(select(Watch.id).where(Watch.sku == values["sku"]))
        if taken:
            return render(
                request,
                "watch_form.html",
                status_code=400,
                **_watch_form_context(
                    watch, values, {**errors, "sku": "Artikelnummer bereits vergeben."},
                db,
                ),
            )
        watch.sku = values["sku"]
    db.commit()
    flash(request, "Gespeichert.", "success")
    return _redirect(f"/watches/{watch.id}")


@router.post("/watches/{watch_id}/status", dependencies=_MUTATE)
async def watch_status(
    request: Request,
    watch_id: str,
    db=DbDep,
    status: str = Form(...),
    note: str = Form(default=""),
    sale_price: str = Form(default=""),
    sale_scope: str = Form(default=""),
    box_type_id: str = Form(default=""),
    box_vk_share: str = Form(default=""),
):
    watch = load_watch(db, watch_id)
    try:
        new_status = WatchStatus(status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Unbekannter Status.") from exc
    box_low_cost = False
    if new_status == WatchStatus.SOLD:
        try:
            sale = parse_money(sale_price)
        except MoneyError as exc:
            raise HTTPException(status_code=400, detail=f"Verkaufspreis: {exc}") from exc
        if sale is None:
            sale = watch.sale_price_cents
        if sale is None:
            raise HTTPException(
                status_code=400,
                detail="Endgültiger Verkaufspreis ist beim Setzen auf „Verkauft“ Pflicht.",
            )
        watch.sale_price_cents = sale
        # Verkaufsumfang: Box kommt aus dem Inventar (Ø-EK als Snapshot).
        if sale_scope == "with_box":
            btype = db.get(BoxType, box_type_id.strip()) if box_type_id.strip() else None
            if btype is None:
                raise HTTPException(
                    status_code=400,
                    detail="Verkauf mit Box: bitte einen Box-Typ wählen (Inventar: Boxen).",
                )
            avg = avg_unit_cost(db, btype.id)
            watch.box_type_id = btype.id
            watch.box_unit_cost_cents = avg if avg is not None else 0
            box_low_cost = avg is None
        else:
            watch.box_type_id = None
            watch.box_unit_cost_cents = None
            watch.box_vk_share_cents = None
        if watch.box_type_id is not None and box_vk_share.strip():
            try:
                share = parse_money(box_vk_share)
            except MoneyError as exc:
                raise HTTPException(status_code=400, detail=f"Box-Anteil VK: {exc}") from exc
            if share is None:
                share = None
            elif share > sale:
                raise HTTPException(
                    status_code=400,
                    detail="VK-Anteil Box darf den Gesamt-Verkaufspreis nicht überschreiten.",
                )
            watch.box_vk_share_cents = share
        elif watch.box_type_id is None:
            watch.box_vk_share_cents = None
    else:
        # Kein Verkauf mehr: Box-Snapshots zurücksetzen (Bestand leitet sich
        # ohnehin aus status=sold ab – hier bleibt die Anzeige konsistent).
        watch.box_type_id = None
        watch.box_unit_cost_cents = None
        watch.box_vk_share_cents = None
    set_status(watch, new_status, note=note.strip()[:300] or None)
    db.commit()
    flash(request, f"Status → {new_status.label}.", "success")
    if box_low_cost:
        flash(
            request,
            "Kein Box-Einkauf erfasst – Ø-EK wurde 0 € gesetzt. Bitte Einkauf "
            "unter „Boxen“ nachtragen.",
            "warning",
        )
    return _redirect(f"/watches/{watch.id}")


@router.post("/watches/{watch_id}/archive", dependencies=_MUTATE)
async def watch_archive(
    request: Request, watch_id: str, db=DbDep, unarchive: str = Form(default="")
):
    watch = load_watch(db, watch_id)
    if unarchive:
        watch.archived_at = None
        flash(request, "Archivierung aufgehoben.", "success")
    else:
        watch.archived_at = utcnow()
        flash(request, "Uhr archiviert (Daten bleiben vollständig erhalten).", "success")
    db.commit()
    return _redirect(f"/watches/{watch.id}")


@router.post("/watches/{watch_id}/delete", dependencies=_MUTATE)
async def watch_delete(request: Request, watch_id: str, db=DbDep, settings=SettingsDep):
    watch = load_watch(db, watch_id)
    image_paths = [settings.images_dir / img.stored_name for img in watch.images]

    db.delete(watch)
    db.commit()

    removed = 0
    for path in image_paths:
        try:
            path.unlink()
            removed += 1
        except FileNotFoundError:
            continue
        except OSError:
            flash(request, f"Datei konnte nicht entfernt werden: {path.name}", "warning")
    flash(
        request,
        f"Uhr und {removed} zugehörige Datei(en) gelöscht.",
        "success",
    )
    return _redirect("/watches")


@router.post("/watches/{watch_id}/public-approve", dependencies=_MUTATE)
async def watch_public_approve(request: Request, watch_id: str, db=DbDep):
    watch = load_watch(db, watch_id)
    form = await request.form()
    watch.public_approved = str(form.get("approve") or "1") in {"1", "on", "true"}
    db.commit()
    state = "gesetzt" if watch.public_approved else "zurückgenommen"
    flash(request, f"Öffentliche Freigabe {state} (wirkt erst mit späterer Funktion).", "success")
    return _redirect(f"/watches/{watch.id}")


@router.post("/watches/{watch_id}/import-approve", dependencies=_MUTATE)
async def watch_import_approve(request: Request, watch_id: str, db=DbDep):
    watch = load_watch(db, watch_id)
    form = await request.form()
    watch.import_approved_for_listing = str(form.get("approve") or "1") in {"1", "on", "true"}
    db.commit()
    if watch.import_approved_for_listing:
        flash(request, "Importierte Inhalte sind jetzt für Anzeigen freigegeben.", "success")
    else:
        flash(request, "Freigabe für importierte Inhalte zurückgenommen.", "info")
    return _redirect(f"/watches/{watch.id}")


__all__ = ["router", "set_status", "_redirect"]


