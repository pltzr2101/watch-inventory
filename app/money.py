"""Geldbeträge: Speicherung als ganze Cents, Eingabe/Anzeige über Decimal.

Fließkomma-Arithmetik wird vermieden; Eingaben werden serverseitig
validiert und mit höchstens zwei Nachkommastellen akzeptiert.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation


class MoneyError(ValueError):
    """Geldbetrag konnte nicht gelesen werden."""


def parse_money(raw: str | None) -> int | None:
    """Parst einen Eingabewert zu Cents (None = leer/nicht gepflegt).

    Akzeptiert deutsche und englische Schreibweise: „1.234,56“, „1234.56“,
    „1,5“, „€ 9,99“. Negativbeträge und mehr als zwei Nachkommastellen sind
    ungültig.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None

    for token in ("€", "$", "£", "¥", "￥", "EUR", "eur", "CNY", "cny", "USD", "usd"):
        text = text.replace(token, "")
    text = text.replace(" ", "").replace(" ", "")
    if not text:
        return None
    if text.startswith("-"):
        raise MoneyError("Negative Beträge sind nicht erlaubt.")

    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(".", "").replace(",", ".")
    elif "." in text:
        head, _, tail = text.rpartition(".")
        if head and len(tail) == 3 and all(g.isdigit() for g in _groups(head)):
            # Deutsche Tausendertrennung: „1.234“
            text = text.replace(".", "")

    if not all(c.isdigit() or c == "." for c in text) or text.count(".") > 1:
        raise MoneyError(f"Ungültiger Geldbetrag: {raw!r}")

    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise MoneyError(f"Ungültiger Geldbetrag: {raw!r}") from exc

    cents = value * 100
    if cents != cents.to_integral_value():
        raise MoneyError("Höchstens zwei Nachkommastellen erlaubt.")
    if cents < 0:
        raise MoneyError("Negative Beträge sind nicht erlaubt.")
    return int(cents)


def _groups(text: str) -> list[str]:
    """Teilt einen Zahlenkopf in Tausendergruppen (für den Punkt-Check)."""
    groups: list[str] = []
    rest = text
    while rest:
        groups.append(rest[-3:])
        rest = rest[:-3]
    return list(reversed(groups))


def format_money(cents: int | None) -> str:
    """Formatiert Cents als deutschen Betrag (None → Platzhalter)."""
    if cents is None:
        return "—"
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    euros, rest = divmod(cents, 100)
    grouped = f"{euros:,}".replace(",", ".")
    return f"{sign}{grouped},{rest:02d} €"
