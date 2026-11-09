"""Box-Inventar: Bestand, Durchschnitts-EK, Einkäufe.

Bewusst simpel (Nutzerentscheid): der EK pro Box ist der **gewichtete
Durchschnitt aller Einkäufe** des Typs – kein FIFO. Bei Bulk-Bestellungen
(10 Stück für 80 €) weicht der Durchschnitt kaum ab; FIFO-Logik wäre erst
Teil einer erweiterten Lagerhaltung (spätere Ausbaustufe).

Bestand ist ABGELEITET: eingekaufte Mengen minus Uhren, die aktuell mit
Box-Typ auf „Verkauft“ stehen – Statusrücksetzen gibt den Bestand automatisch
wieder frei, es gibt keinen doppelten Buchungsstand.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import BoxPurchase, BoxType, Watch, WatchStatus


def avg_unit_cost(db: Session, box_type_id: str) -> int | None:
    """Gewichteter Ø-EK pro Box in Cents (kaufmännisch, ganzzahlig)."""
    total, qty = db.execute(
        select(
            func.coalesce(func.sum(BoxPurchase.total_cost_cents), 0),
            func.coalesce(func.sum(BoxPurchase.quantity), 0),
        ).where(BoxPurchase.box_type_id == box_type_id)
    ).one()
    if int(qty) <= 0:
        return None
    return (2 * int(total) + int(qty)) // (2 * int(qty))


def unit_cost(quantity: int, total_cents: int) -> int:
    """Ø eines einzelnen Einkaufs (für die Anzeige)."""
    if quantity <= 0:
        return 0
    return (2 * total_cents + quantity) // (2 * quantity)


def overview(db: Session) -> list[dict]:
    """Alle Typen mit Bestand, Ø-EK und Kaufstatistik (Reihenfolge wie Dropdown)."""
    types = db.scalars(
        select(BoxType).order_by(BoxType.sort_order, BoxType.label)
    ).all()
    rows: list[dict] = []
    for t in types:
        total, qty, last = db.execute(
            select(
                func.coalesce(func.sum(BoxPurchase.total_cost_cents), 0),
                func.coalesce(func.sum(BoxPurchase.quantity), 0),
                func.max(BoxPurchase.bought_at),
            ).where(BoxPurchase.box_type_id == t.id)
        ).one()
        sold = int(
            db.scalar(
                select(func.count(Watch.id)).where(
                    Watch.box_type_id == t.id, Watch.status == WatchStatus.SOLD
                )
            )
            or 0
        )
        purchased = int(qty)
        avg = None
        if purchased > 0:
            avg = (2 * int(total) + purchased) // (2 * purchased)
        rows.append(
            {
                "id": t.id,
                "label": t.label,
                "sort_order": t.sort_order,
                "archived_at": t.archived_at,
                "purchased": purchased,
                "sold": sold,
                "stock": purchased - sold,
                "avg": avg,
                "total_spend": int(total),
                "last_bought": last if isinstance(last, datetime) else None,
            }
        )
    return rows
