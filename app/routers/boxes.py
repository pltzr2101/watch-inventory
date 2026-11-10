"""Box-Inventar-Seite: Einkäufe erfassen, Typen pflegen, Bestände sehen.

Alles authentifiziert (nicht in PUBLIC_PATHS); Box-EK ist interne Einkaufs-
information und fließt nie in Anzeigen- oder ZIP-Kontexte (dort stehen nur
Verkaufsangaben).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select

from ..csrf import verify_csrf
from ..deps import DbDep, SettingsDep
from ..flash import flash
from ..forms import MoneyError, parse_money
from ..models import BoxPurchase, BoxSale, BoxType, new_id, utcnow
from ..money import RateError, format_money, format_rate, parse_rate, usd_to_eur
from ..services.boxes import overview, unit_cost
from ..templating import render

router = APIRouter(tags=["boxes"])
_MUTATE = [Depends(verify_csrf)]


def _redirect() -> RedirectResponse:
    return RedirectResponse(url="/boxes", status_code=303)


def _de(value_cents: int) -> str:
    """Cent → deutsche Eingabeform „28,35“ (ohne Währungszeichen)."""
    return f"{value_cents / 100:.2f}".replace(".", ",")


def _recent_purchases(db) -> list[dict]:  # type: ignore[no-untyped-def]
    rows = db.execute(
        select(BoxPurchase, BoxType.label)
        .join(BoxType, BoxPurchase.box_type_id == BoxType.id)
        .order_by(BoxPurchase.bought_at.desc(), BoxPurchase.id.desc())
        .limit(25)
    ).all()
    out = []
    for p, label in rows:
        cur = (p.currency or "EUR").upper()
        # Edit-Felder zeigen die QUELLWÄHRUNG; gespeichert wird immer EUR.
        if cur == "CNY" and p.rate_milli:
            factor = p.rate_milli / 1000
            goods_edit = _de(round((p.total_cost_cents or 0) / factor))
            ship_edit = _de(round((p.shipping_cents or 0) / factor))
            rate_edit = str(factor).replace(".", ",")
        else:
            cur = "EUR"  # CNY ohne Kurs (Legacy) → Felder zeigen den EUR-Stand
            goods_edit = _de(p.total_cost_cents or 0)
            ship_edit = _de(p.shipping_cents or 0)
            rate_edit = format_rate(p.rate_milli).replace(",", ",") if p.rate_milli else ""
        out.append(
            {
                "id": p.id,
                "bought_at": p.bought_at,
                "type_label": label,
                "quantity": p.quantity,
                "goods_cents": p.total_cost_cents,
                "shipping_cents": p.shipping_cents or 0,
                "unit": unit_cost(p.quantity, p.total_cost_cents, p.shipping_cents or 0),
                "currency": cur,
                "rate": format_rate(p.rate_milli) if p.rate_milli else None,
                "rate_edit": rate_edit,
                "goods_edit": goods_edit,
                "ship_edit": ship_edit,
                "note": p.note,
            }
        )
    return out


@router.get("/boxes")
def boxes_page(request: Request, db=DbDep):
    return render(
        request,
        "boxes.html",
        rows=overview(db),
        purchases=_recent_purchases(db),
        sales=_recent_sales(db),
    )


@router.post("/boxes/purchases", dependencies=_MUTATE)
async def box_purchase_new(request: Request, db=DbDep):
    form = await request.form()
    type_id = str(form.get("box_type_id") or "").strip()
    box_type = db.get(BoxType, type_id) if type_id else None
    if box_type is None:
        flash(request, "Bitte einen Box-Typ wählen.", "error")
        return _redirect()
    if box_type.archived_at is not None:
        flash(request, f"Typ archiviert: {box_type.label} – zuerst aktivieren.", "error")
        return _redirect()
    currency = str(form.get("currency") or "EUR").upper().strip()
    if currency not in ("EUR", "CNY"):
        flash(request, "Währung nur EUR oder CNY.", "error")
        return _redirect()
    try:
        quantity = int(str(form.get("quantity") or "0"))
    except ValueError:
        quantity = 0
    if quantity < 1 or quantity > 9999:
        flash(request, "Menge muss zwischen 1 und 9999 liegen.", "error")
        return _redirect()
    try:
        goods = parse_money(str(form.get("total_cost") or ""))
    except MoneyError as exc:
        flash(request, f"Warenwert: {exc}", "error")
        return _redirect()
    if goods is None or goods <= 0:
        flash(request, "Warenwert fehlt oder ist <= 0.", "error")
        return _redirect()
    try:
        shipping = parse_money(str(form.get("shipping_cost") or "") or "0")
    except MoneyError as exc:
        flash(request, f"Versand der Bestellung: {exc}", "error")
        return _redirect()
    shipping = shipping or 0
    if shipping < 0:
        flash(request, "Versand darf nicht negativ sein.", "error")
        return _redirect()

    rate = None
    if currency == "CNY":
        try:
            # CNY→EUR liegt typisch bei ~0,12: eigener plausibler Bereich,
            # nicht der USD-Standard (0,5–5,0).
            rate = parse_rate(str(form.get("rate") or ""), min_milli=10, max_milli=5000)
        except RateError as exc:
            flash(request, f"Kurs: {exc}", "error")
            return _redirect()
        if rate is None:
            flash(
                request,
                "Kurs erforderlich – bei CNY „Kurs holen“ setzen oder EUR wählen.",
                "error",
            )
            return _redirect()
        goods_eur = usd_to_eur(goods, rate) or 0
        shipping_eur = usd_to_eur(shipping, rate) or 0
    else:
        goods_eur, shipping_eur = goods, shipping

    purchase = BoxPurchase(
        box_type_id=box_type.id,
        quantity=quantity,
        total_cost_cents=goods_eur,
        shipping_cents=shipping_eur,
        currency=currency,
        rate_milli=rate,
        note=(str(form.get("note") or "").strip()[:80] or None),
        bought_at=utcnow(),
    )
    db.add(purchase)
    db.commit()
    msg = (
        f"Einkauf: {quantity}× {box_type.label} – Ware {format_money(goods_eur)}, "
        f"Versand {format_money(shipping_eur)} → Ø "
        f"{format_money(unit_cost(quantity, goods_eur, shipping_eur))}/Box"
    )
    if currency == "CNY":
        msg += f" (CNY, Kurs {format_rate(rate)})"
    flash(request, msg + ".", "success")
    return _redirect()


@router.post("/boxes/types", dependencies=_MUTATE)
async def box_type_new(request: Request, db=DbDep):
    form = await request.form()
    label = str(form.get("label") or "").strip()[:80]
    if not label:
        flash(request, "Name des Box-Typs fehlt.", "error")
        return _redirect()
    exists = db.scalars(select(BoxType)).all()
    if any(c.label.casefold() == label.casefold() for c in exists):
        flash(request, f"Diesen Box-Typ gibt es bereits: {label}", "error")
        return _redirect()
    max_sort = db.scalar(select(func.max(BoxType.sort_order))) or 0
    db.add(BoxType(id=new_id(), label=label, sort_order=max_sort + 1))
    db.commit()
    flash(request, f"Box-Typ angelegt: {label}", "success")
    return _redirect()


@router.post("/boxes/types/{box_type_id}/archive", dependencies=_MUTATE)
async def box_type_archive(
    box_type_id: str,
    request: Request,
    db=DbDep,
    unarchive: str = Form(default=""),
):
    box_type = db.get(BoxType, box_type_id)
    if box_type is None:
        flash(request, "Box-Typ fehlt.", "error")
        return _redirect()
    if unarchive:
        box_type.archived_at = None
        flash(request, f"Box-Typ wieder aktiv: {box_type.label}", "success")
    else:
        box_type.archived_at = utcnow()
        flash(
            request,
            f"Box-Typ archiviert: {box_type.label} (Bestände bleiben erhalten).",
            "success",
        )
    db.commit()
    return _redirect()


def fx_url_for(base_url: str, cur: str) -> str | None:
    """FX_RATE_URL-Vorlage → URL für die Währung.

    USD geht direkt; andere Währungen ersetzen `from=USD` in der Vorlage
    (die Standardfrankfurter-URL enthält sie). Kein Ersatz möglich → None.
    """
    if not base_url:
        return None
    if cur == "USD":
        return base_url
    if "from=USD" in base_url:
        return base_url.replace("from=USD", f"from={cur}")
    return None


@router.get("/api/fx-rate")
def fx_rate(cur: str = "USD", settings=SettingsDep):
    """Aktuellen Kurs (USD/CNY→EUR) als Vorschlag – nur auf Knopfdruck.

    Gleiche Infrastruktur wie der USD-Kurs der Uhren (`FX_RATE_URL`,
    Standard frankfurter.app); `FX_RATE_URL=""` deaktiviert; Fehler → 502.
    """
    import json
    import urllib.error
    import urllib.request

    cur = (cur or "USD").upper().strip()
    if cur not in ("USD", "CNY"):
        raise HTTPException(status_code=400, detail="Nur USD oder CNY unterstützt.")
    if not settings.fx_rate_url:
        raise HTTPException(
            status_code=404, detail="Kursdienst ist deaktiviert (FX_RATE_URL leer)."
        )
    url = fx_url_for(settings.fx_rate_url, cur)
    if url is None:
        raise HTTPException(
            status_code=404,
            detail=f"FX_RATE_URL enthält keine from=USD-Vorlage – {cur} nicht möglich.",
        )
    request_obj = urllib.request.Request(  # noqa: S310 – feste konfigurierte URL
        url, headers={"Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(request_obj, timeout=5) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise HTTPException(
            status_code=502, detail=f"Kursdienst nicht erreichbar: {exc}"
        ) from exc
    try:
        value = float(payload["rates"]["EUR"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=502, detail="Kursdienst lieferte unerwartete Daten."
        ) from exc
    return {"cur": cur, "rate": format_rate(int(round(value * 1000)))}


@router.post("/boxes/purchases/{purchase_id}", dependencies=_MUTATE)
async def box_purchase_edit(request: Request, purchase_id: int, db=DbDep):
    """Einkauf nachträglich bearbeiten (z. B. Versandkosten später ergänzen).

    Währung/Kurs bleiben unveränderlich (Snapshot-Charakter); bei CNY werden
    die Eingaben wie beim Anlegen in EUR umgerechnet. Verkaufs-Snapshots
    bereits verkaufter Uhren bleiben unangetastet.
    """
    purchase = db.get(BoxPurchase, purchase_id)
    if purchase is None:
        flash(request, "Einkauf fehlt.", "error")
        return _redirect()
    form = await request.form()
    try:
        quantity = int(str(form.get("quantity") or "0"))
    except ValueError:
        quantity = 0
    if quantity < 1 or quantity > 9999:
        flash(request, "Menge muss zwischen 1 und 9999 liegen.", "error")
        return _redirect()
    try:
        goods = parse_money(str(form.get("total_cost") or ""))
    except MoneyError as exc:
        flash(request, f"Warenwert: {exc}", "error")
        return _redirect()
    if goods is None or goods <= 0:
        flash(request, "Warenwert fehlt oder ist <= 0.", "error")
        return _redirect()
    try:
        shipping = parse_money(str(form.get("shipping_cost") or "") or "0")
    except MoneyError as exc:
        flash(request, f"Versand: {exc}", "error")
        return _redirect()
    shipping = shipping or 0
    if shipping < 0:
        flash(request, "Versand darf nicht negativ sein.", "error")
        return _redirect()

    currency = str(form.get("currency") or purchase.currency or "EUR").upper().strip()
    if currency not in ("EUR", "CNY"):
        flash(request, "Währung nur EUR oder CNY.", "error")
        return _redirect()

    rate: int | None = None
    if currency == "CNY":
        # Ein Kurs gilt für Ware UND Versand dieses Einkaufs; bleibt das Feld
        # leer, wird der bereits gespeicherte Kurs-Snapshot übernommen.
        try:
            rate = parse_rate(str(form.get("rate") or ""), min_milli=10, max_milli=5000)
        except RateError as exc:
            flash(request, f"Kurs: {exc}", "error")
            return _redirect()
        if rate is None:
            rate = purchase.rate_milli
        if rate is None:
            flash(request, "Kurs erforderlich („Kurs holen“) – bei CNY-Änderung.", "error")
            return _redirect()
        goods_eur = usd_to_eur(goods, rate) or 0
        shipping_eur = usd_to_eur(shipping, rate) or 0
    else:
        goods_eur, shipping_eur = goods, shipping

    purchase.quantity = quantity
    purchase.total_cost_cents = goods_eur
    purchase.shipping_cents = shipping_eur
    purchase.currency = currency
    purchase.rate_milli = rate
    purchase.note = str(form.get("note") or "").strip()[:80] or None
    db.commit()
    flash(
        request,
        f"Einkauf aktualisiert: {quantity}× ({currency}) – Ware {format_money(goods_eur)}, "
        f"Versand {format_money(shipping_eur)} → Ø "
        f"{format_money(unit_cost(quantity, goods_eur, shipping_eur))}/Box"
        + (f" (Kurs {format_rate(rate)})" if rate else "")
        + ".",
        "success",
    )
    return RedirectResponse(
        url=f"/boxes?edit={purchase.id}#kauf-{purchase.id}", status_code=303
    )


@router.post("/boxes/purchases/{purchase_id}/delete", dependencies=_MUTATE)
async def box_purchase_delete(request: Request, purchase_id: int, db=DbDep):
    purchase = db.get(BoxPurchase, purchase_id)
    if purchase is None:
        flash(request, "Einkauf fehlt.", "error")
        return _redirect()
    label = purchase.box_type.label if purchase.box_type else "?"
    summary = f"{purchase.quantity}× {label}"
    db.delete(purchase)
    db.commit()
    flash(
        request,
        f"Einkauf gelöscht: {summary} – Ø-EK und Bestand des Typs neu berechnet. "
        "Snapshots bereits verkaufter Uhren bleiben unverändert.",
        "success",
    )
    return _redirect()


def _recent_sales(db) -> list[dict]:  # type: ignore[no-untyped-def]
    rows = db.execute(
        select(BoxSale, BoxType.label)
        .join(BoxType, BoxSale.box_type_id == BoxType.id)
        .order_by(BoxSale.sold_at.desc(), BoxSale.id.desc())
        .limit(25)
    ).all()
    return [
        {
            "id": s.id,
            "sold_at": s.sold_at,
            "type_label": label,
            "quantity": s.quantity,
            "price_cents": s.sale_price_cents,
            "note": s.note,
        }
        for s, label in rows
    ]


@router.post("/boxes/sales", dependencies=_MUTATE)
async def box_sale_new(request: Request, db=DbDep):
    """Einzelverkauf von Boxen; prüft den verfügbaren Bestand des Typs."""
    form = await request.form()
    type_id = str(form.get("box_type_id") or "").strip()
    box_type = db.get(BoxType, type_id) if type_id else None
    if box_type is None:
        flash(request, "Bitte einen Box-Typ wählen.", "error")
        return _redirect()
    try:
        quantity = int(str(form.get("quantity") or "0"))
    except ValueError:
        quantity = 0
    if quantity < 1 or quantity > 9999:
        flash(request, "Menge muss zwischen 1 und 9999 liegen.", "error")
        return _redirect()
    stock = next((r["stock"] for r in overview(db) if r["id"] == box_type.id), 0)
    if quantity > stock:
        flash(
            request,
            f"Nur {stock}× {box_type.label} auf Lager (Verkauf nicht erfasst).",
            "error",
        )
        return _redirect()
    try:
        price = parse_money(str(form.get("sale_price") or "") or "")
    except MoneyError as exc:
        flash(request, f"Erlös: {exc}", "error")
        return _redirect()
    price = price or 0
    if price < 0:
        flash(request, "Erlös darf nicht negativ sein.", "error")
        return _redirect()
    sale = BoxSale(
        box_type_id=box_type.id,
        quantity=quantity,
        sale_price_cents=price if price > 0 else None,
        note=str(form.get("note") or "").strip()[:80] or None,
        sold_at=utcnow(),
    )
    db.add(sale)
    db.commit()
    msg = f"Einzelverkauf: {quantity}× {box_type.label}"
    if sale.sale_price_cents:
        msg += f" – Erlös {format_money(sale.sale_price_cents)}"
    flash(request, msg + ".", "success")
    return _redirect()


@router.post("/boxes/sales/{sale_id}/delete", dependencies=_MUTATE)
async def box_sale_delete(request: Request, sale_id: int, db=DbDep):
    sale = db.get(BoxSale, sale_id)
    if sale is None:
        flash(request, "Verkaufseintrag fehlt.", "error")
        return _redirect()
    label = sale.box_type.label if sale.box_type else "?"
    summary = f"{sale.quantity}× {label}"
    db.delete(sale)
    db.commit()
    flash(request, f"Einzelverkauf gelöscht: {summary} – Bestand wieder hochgezählt.", "success")
    return _redirect()
