"""Auswertungs-Dashboard (interne Verkaufs-/Bestands-Statistik, Phase 1)."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Request
from fastapi.responses import Response

from ..deps import DbDep
from ..money import format_money
from ..services import analytics
from ..templating import render

router = APIRouter(tags=["analytics"])


def _nice_max(cents: int) -> int:
    """Skalen-Obergrenze auf1/2/2,5/5/10×10^k aufrunden (immer ≥ Wert)."""
    if cents <= 0:
        return 100
    import math

    magnitude = 10 ** math.floor(math.log10(cents))
    for factor in (1, 2, 2.5, 5, 10):
        nice = int(factor * magnitude)
        if nice >= cents:
            return nice
    return int(10 * magnitude)


def _svg_geometry(data: dict) -> None:
    """Pixelwerte für Zeitreihe inkl. Y-Achse (Ticks + Grid) und Balkenbreiten."""
    series = data["series"]
    raw_max = max(
        (max(s["umsatz"], s["marge"]) for s in series), default=0
    ) or 1
    max_u = _nice_max(raw_max)
    left, plot_w, base_y, plot_h = 44.0, 566.0, 205.0, 175.0
    n = max(1, len(series))
    step = plot_w / n
    bw = max(4, min(28, int(step) - 8))
    pts: list[str] = []
    for i, s in enumerate(series):
        s["bar_h"] = round(s["umsatz"] / max_u * plot_h)
        s["bar_y"] = int(base_y - s["bar_h"])
        s["x"] = round(left + i * step + (step - bw) / 2)
        s["bw"] = bw
        s["mx"] = int(s["x"] + bw / 2)
        s["my"] = int(base_y - round(s["marge"] / max_u * plot_h))
        pts.append(f"{s['mx']},{s['my']}")
    ticks = []
    for i in range(5):
        value = round(max_u * i / 4)
        ticks.append(
            {
                "y": int(base_y - value / max_u * plot_h),
                "label": format_money(value).replace(",00 €", " €"),
                "major": i == 0,
            }
        )
    ticks.reverse()
    data["chart"] = {
        "max": max_u,
        "line": " ".join(pts),
        "ticks": ticks,
        "label_every": 1 if n <= 13 else (2 if n <= 26 else 4),
    }

    kpi = data["kpi"]
    base = kpi["avg_sale"] or (kpi["avg_ek"] + kpi["avg_extra"] + kpi["avg_box"]) or 1
    parts = [
        ("Ø EK", kpi["avg_ek"]),
        ("Ø Zusatzkosten", kpi["avg_extra"]),
        ("Ø Box-EK", kpi["avg_box"]),
        ("Ø Marge", max(0, kpi["avg_margin"] or 0)),
    ]
    data["waterfall"] = [
        {"label": label, "cents": cents, "pct": max(1, round(100 * cents / base))}
        for label, cents in parts
    ]
    data["pipe_max"] = max((r["count"] for r in data["pipeline"]), default=1) or 1


_CHART_PERIODS: dict[str, int | None] = {"6": 6, "12": 12, "24": 24, "all": None}


def _parse_date(value: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


@router.get("/auswertung")
def auswertung(
    request: Request,
    db=DbDep,
    p: str = analytics.DEFAULT_PERIOD,
    c: str = "12",
    von: str = "",
    bis: str = "",
):
    chart_key = c if c in _CHART_PERIODS else "12"
    d_von, d_bis = _parse_date(von), _parse_date(bis)
    custom_error = ""
    if (von and d_von is None) or (bis and d_bis is None):
        custom_error = "Ungültiges Datum – bitte JJJJ-MM-TT verwenden."
    elif d_von is not None and d_bis is not None and d_von > d_bis:
        custom_error = "„Von“ liegt nach „Bis“."
    if d_von is not None and d_bis is not None and not custom_error:
        data = analytics.dashboard(
            db, p, chart=_CHART_PERIODS[chart_key], von=d_von, bis=d_bis
        )
    else:
        data = analytics.dashboard(db, p, chart=_CHART_PERIODS[chart_key])
    data["chart_period"] = chart_key
    data["custom_error"] = custom_error
    _svg_geometry(data)
    return render(request, "auswertung.html", d=data)


def _sales_csv(db, p: str, von: str, bis: str) -> Response:  # type: ignore[valid-type]
    import csv
    import io

    d_von, d_bis = _parse_date(von), _parse_date(bis)
    if d_von is not None and d_bis is not None and d_von <= d_bis:
        data = analytics.dashboard(db, p, von=d_von, bis=d_bis, chart=None)
    else:
        data = analytics.dashboard(db, p, chart=None)
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(["Datum", "Uhr", "VK (EUR)", "EK (EUR)", "DB (EUR)",
                     "DB (%)", "Haltedauer (Tage)"])

    def esc(value: object) -> object:
        # CSV-Injection-Schutz: Excel-Auswertungszeichen am Feldanfang neutralisieren
        if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
            return "'" + value
        return value

    for r in data["sales_rows"]:
        writer.writerow(
            [esc(r["date"]), esc(r["title"]), _e(r["vk"]), _e(r["ek"]),
             _e(r["db"]), r["pct"], r["held"]]
        )
    return Response(
        buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": "attachment; filename=verkaeufe.csv",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _e(cents: int) -> str:
    return f"{cents / 100:.2f}".replace(".", ",")


@router.get("/auswertung/verkaeufe.csv")
def sales_csv(
    request: Request,
    db=DbDep,
    p: str = analytics.DEFAULT_PERIOD,
    von: str = "",
    bis: str = "",
):
    return _sales_csv(db, p, von, bis)
