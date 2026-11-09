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


class RateError(ValueError):
    """Wechselkurs konnte nicht gelesen werden."""


# Plausibilitätsgrenze EUR pro USD (Milli-Einheiten): 0.50 … 5.00.
RATE_MIN_MILLI = 500
RATE_MAX_MILLI = 5_000


def parse_rate(raw: str | None) -> int | None:
    """Parst einen USD→EUR-Kurs (EUR pro 1 USD) zu Milli-Einheiten.

    Beispiel: „1,0923“ → 1092. Bis zu vier Nachkommastellen.
    """
    if raw is None:
        return None
    text = str(raw).strip().replace(" ", "")
    if not text:
        return None
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    if not all(c.isdigit() or c == "." for c in text) or text.count(".") > 1:
        raise RateError(f"Ungültiger Kurs: {raw!r}")
    head, _, tail = text.partition(".")
    if len(tail) > 4:
        raise RateError("Höchstens vier Nachkommastellen beim Kurs.")
    if not head:
        raise RateError(f"Ungültiger Kurs: {raw!r}")
    try:
        milli = int(head) * 1000 + int((tail + "000")[:3])
        extra_digit = int(tail[3]) if len(tail) == 4 else 0
    except ValueError as exc:
        raise RateError(f"Ungültiger Kurs: {raw!r}") from exc
    if extra_digit >= 5:
        milli += 1  # vierte Nachkommastelle aufrunden
    if not RATE_MIN_MILLI <= milli <= RATE_MAX_MILLI:
        raise RateError("Kurs außerhalb des plausiblen Bereichs (0,5000–5,0000).")
    return milli


def format_rate(milli: int | None) -> str:
    """Milli-Kurs → deutsche Schreibweise (1092 → „1,092“)."""
    if milli is None:
        return "—"
    euros, frac = divmod(milli, 1000)
    frac_str = f"{frac:03d}".rstrip("0") or "0"
    return f"{euros},{frac_str}"


def usd_to_eur(usd_cents: int | None, rate_milli: int | None) -> int | None:
    """USD-Cents × Kurs → EUR-Cents (kaufmännische Ganzzahlrundung)."""
    if usd_cents is None or rate_milli is None:
        return None
    return (usd_cents * rate_milli + 500) // 1000


class PercentError(ValueError):
    """Prozentwert konnte nicht gelesen werden."""


def parse_percent(raw: str | None) -> int | None:
    """Parst Prozent zu Millionstel (6,5 → 6500). Bis zu vier Nachkommastellen.

    Gültig: 0 < Wert ≤ 100. Leer → None.
    """
    if raw is None:
        return None
    text = str(raw).strip().replace(" ", "").replace("%", "")
    if not text:
        return None
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    if not all(c.isdigit() or c == "." for c in text) or text.count(".") > 1:
        raise PercentError(f"Ungültiger Prozentwert: {raw!r}")
    head, _, tail = text.partition(".")
    if len(tail) > 4:
        raise PercentError("Höchstens vier Nachkommastellen beim Prozentwert.")
    if not head and not tail:
        raise PercentError(f"Ungültiger Prozentwert: {raw!r}")
    milli = int(head or "0") * 1000 + int((tail + "000")[:3])
    if len(tail) == 4 and int(tail[3]) >= 5:
        milli += 1
    if milli <= 0:
        raise PercentError("Prozentwert muss größer als 0 sein.")
    if milli > 100_000:
        raise PercentError("Prozentwert maximal 100 %.")
    return milli


def format_percent(milli: int | None) -> str:
    """Millionstel-Prozent → deutsche Schreibweise (6000 → „6“, 6500 → „6,5“)."""
    if milli is None:
        return "—"
    whole, frac = divmod(milli, 1000)
    frac_str = f"{frac:03d}".rstrip("0")
    return f"{whole},{frac_str}" if frac_str else str(whole)


def percent_amount(basis_cents: int, percent_milli: int) -> int:
    """Basis × Prozent → Cents (Ganzzahl, kaufmännisch gerundet).

    percent_milli: 6 % = 6000 → Faktor / 100 000.
    """
    return (basis_cents * percent_milli + 50_000) // 100_000
