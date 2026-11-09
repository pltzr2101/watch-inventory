"""Read-only Verkaufs-/Bestands-Aggregatik für das Auswertungs-Dashboard.

Liefert ausschließlich berechnete Summen und Durchschnitte (Cents als int)
über bereits bestehende Tabellen – keine Schema-Änderung, keine
Materialisierung, kein Hintergrundlauf. Die Zeilenmengen sind O(Uhren) und
damit bewusst in Python aggregiert (Volumen überschaubar); SQL wird nur für
Gruppierungen mit vielen Zeilen genutzt (Zusatzkosten, Status-Events).

Grenze zum späteren öffentlichen Lesen: Diese Daten sind intern (Einkauf,
Notizen, Kategorien). Für die öffentliche Bestandsansicht (Ausbaustufe 4)
wird ein eigenes read-only-Modul mit ausschließlich freigegebenen Feldern
nötig – diese Module dürfen nicht wiederverwendet werden.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import CostCategory, Watch, WatchStatus
from .boxes import overview as box_overview

#: Zeitraumfilter (Tage) für KPIs/Listen; die Zeitreihe zeigt immer 12 Monate.
PERIODS: dict[str, int | None] = {"30": 30, "90": 90, "365": 365, "all": None}
DEFAULT_PERIOD = "90"


def _since(period: str, now: datetime) -> datetime | None:
    days = PERIODS.get(period, PERIODS[DEFAULT_PERIOD])
    if days is None:
        return None
    return now - timedelta(days=days)


def _purchase_eur(w: Watch) -> int:
    return w.purchase_price_cents or 0


def _extra_total(w: Watch) -> int:
    return sum(c.amount_cents for c in w.extra_costs)


def margin_eur(w: Watch) -> int | None:
    """Marge wie auf der Detailseite: VK − (EK + Zusatz + Box-Snapshot)."""
    if w.sale_price_cents is None:
        return None
    return w.sale_price_cents - (_purchase_eur(w) + _extra_total(w) + (w.box_unit_cost_cents or 0))


def _extra_buckets(w: Watch, by_category: dict[str, str]) -> dict[str, int]:
    """Zusatzkosten eines Uhren-Elements nach Grobkategorie (Label-Heuristik)."""
    out = {"versicherung": 0, "versand": 0, "uebrige": 0}
    for c in w.extra_costs:
        label = (by_category.get(c.category_id, "") or "").lower()
        if "versicher" in label:
            out["versicherung"] += c.amount_cents
        elif any(k in label for k in ("versand", "porto", "dhl", "shipping")):
            out["versand"] += c.amount_cents
        else:
            out["uebrige"] += c.amount_cents
    return out


def _held_days(w: Watch, now: datetime) -> int:
    """Haltedauer: Ankunft DE → Verkauf (Entscheidung des Betreibers).

    Fallback ohne DE-Status-Event: Anlage → Verkauf bzw. → heute.
    """
    start = w.created_at
    if w.status_events:
        de_events = [e.changed_at for e in w.status_events if e.to_status == WatchStatus.IN_GERMANY]
        if de_events:
            start = min(de_events)
    end = w.sold_at or now
    return max(0, (end - start).days)


def dashboard(db: Session, period: str, now: datetime | None = None) -> dict:
    """Alle Daten für /auswertung als reines Ergebnis-Dict (ints in Cents)."""
    # Naive UTC wie die DB (DateTime-Spalten speichern ohne Timezone)
    now = now or datetime.now(UTC).replace(tzinfo=None)
    since = _since(period, now)

    all_watches = list(db.execute(select(Watch)).scalars().all())
    sold_all = [w for w in all_watches if w.status == WatchStatus.SOLD and w.sold_at]
    sold = [
        w
        for w in sold_all
        if since is None or (w.sold_at is not None and w.sold_at >= since)
    ]
    active = [w for w in all_watches if w.status != WatchStatus.SOLD]

    labels = dict(
        db.execute(select(CostCategory.id, CostCategory.label)).all()
    )

    n = len(sold)
    with_ek = [w for w in sold if w.purchase_price_cents is not None]
    avg_ek = round(sum(_purchase_eur(w) for w in with_ek) / len(with_ek)) if with_ek else 0

    buckets = [_extra_buckets(w, labels) for w in sold]
    sum_extra = sum(b["versicherung"] + b["versand"] + b["uebrige"] for b in buckets)
    sum_ins = sum(b["versicherung"] for b in buckets)
    sum_ship = sum(b["versand"] for b in buckets)
    avg_extra = round(sum_extra / n) if n else 0
    avg_ins = round(sum_ins / n) if n else 0
    avg_ship = round(sum_ship / n) if n else 0

    margins = [(w, margin_eur(w)) for w in sold]
    margins_ok = [(w, m) for w, m in margins if m is not None]
    avg_margin = round(sum(m for _, m in margins_ok) / len(margins_ok)) if margins_ok else 0
    sales = [w.sale_price_cents for w, _ in margins_ok if w.sale_price_cents]
    avg_pct = (
        round(100 * sum(m for _, m in margins_ok) / sum(sales)) if sales else 0
    )
    avg_sale = round(sum(sales) / len(sales)) if sales else 0

    # Boxen: gepoolter Ø inkl. Bestellversand + Bestandswert je Typ
    box_rows = box_overview(db)
    box_qty = sum(r["purchased"] for r in box_rows)
    box_spend = sum(r["total_spend"] for r in box_rows)
    avg_box = round(box_spend / box_qty) if box_qty else 0
    box_value = sum((r["avg"] or 0) * max(0, r["stock"]) for r in box_rows)

    active_with_val = [w for w in active if w.purchase_price_cents is not None]
    stock_value = sum(_purchase_eur(w) + _extra_total(w) for w in active_with_val)

    # Verkäufe/Monat im Zeitraum
    if period == "all":
        first = min((w.sold_at for w in sold_all if w.sold_at), default=None)
        months = ((now - first).days / 30.44 + 1) if first else 1
    else:
        months = max(1.0, (PERIODS.get(period) or 90) / 30.44)
    per_month = round(len(sold) / months, 1) if months else 0.0

    # Ø Haltedauer der verkauften Uhren im Zeitraum (Ankunft DE → Verkauf)
    held = [_held_days(w, now) for w in sold if w.sold_at]
    avg_held = round(sum(held) / len(held)) if held else 0

    # Zeitreihe: letzte 12 Monate, Umsatz + Marge je Monat
    months_series: list[dict] = []
    y, m = now.year, now.month
    for _ in range(12):
        label = f"{m:02d}/{str(y)[2:]}"
        ms = [
            w for w in sold_all if w.sold_at and w.sold_at.year == y and w.sold_at.month == m
        ]
        um = sum(w.sale_price_cents or 0 for w in ms)
        mg = sum(m_ for w in ms if (m_ := margin_eur(w)) is not None)
        months_series.append({"label": label, "umsatz": um, "marge": mg})
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    months_series.reverse()

    # Bestands-Pipeline: aktive Status + Verkäufe im Zeitraum
    pipeline = [
        ("Beim Lieferanten / CN", WatchStatus.IN_CHINA),
        ("Unterwegs", WatchStatus.IN_TRANSIT),
        ("In Deutschland", WatchStatus.IN_GERMANY),
        ("Reserviert", WatchStatus.RESERVED),
    ]
    pipe_rows = []
    for title, st in pipeline:
        ws = [w for w in active if w.status == st]
        pipe_rows.append(
            {
                "title": title,
                "count": len(ws),
                "eur": sum(_purchase_eur(w) + _extra_total(w) for w in ws),
                "eur_label": "EK",
            }
        )
    pipe_rows.append(
        {
            "title": f"Verkauft (Zeitraum {period})",
            "count": len(sold),
            "eur": sum(w.sale_price_cents or 0 for w in sold),
            "eur_label": "VK",
        }
    )

    top = sorted(margins_ok, key=lambda t: t[1], reverse=True)[:5]
    top_rows = [
        {
            "title": w.title or "Ohne Titel",
            "margin": m,
            "pct": round(100 * m / w.sale_price_cents) if w.sale_price_cents else 0,
        }
        for w, m in top
    ]

    return {
        "period": period if period in PERIODS else DEFAULT_PERIOD,
        "kpi": {
            "avg_sale": avg_sale,
            "avg_ek": avg_ek,
            "avg_extra": avg_extra,
            "avg_insurance": avg_ins,
            "avg_shipping": avg_ship,
            "avg_box": avg_box,
            "box_value": box_value,
            "avg_margin": avg_margin,
            "avg_pct": avg_pct,
            "avg_held": avg_held,
            "stock_value": stock_value,
            "stock_count": len(active),
            "sold_count": n,
            "per_month": per_month,
        },
        "series": months_series,
        "pipeline": pipe_rows,
        "top": top_rows,
        "calc_seed": {"ek": avg_ek, "extra": avg_extra, "box": avg_box},
    }
