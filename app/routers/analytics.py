"""Auswertungs-Dashboard (interne Verkaufs-/Bestands-Statistik, Phase 1)."""

from __future__ import annotations

from fastapi import APIRouter, Request

from ..deps import DbDep
from ..services import analytics
from ..templating import render

router = APIRouter(tags=["analytics"])


def _svg_geometry(data: dict) -> None:
    """Pixelwerte für Zeitreihe (Balken + Marge-Linie) und Breiten-Bars."""
    series = data["series"]
    max_u = max((s["umsatz"] for s in series), default=0) or 1
    left, plot_w, base_y, plot_h = 40.0, 560.0, 205.0, 175.0
    step = plot_w / max(1, len(series))
    pts: list[str] = []
    for i, s in enumerate(series):
        s["bar_h"] = round(s["umsatz"] / max_u * plot_h)
        s["bar_y"] = int(base_y - s["bar_h"])
        s["x"] = round(left + i * step + 6)
        s["mx"] = int(s["x"] + 14)
        s["my"] = int(base_y - round(s["marge"] / max_u * plot_h))
        pts.append(f"{s['mx']},{s['my']}")
    data["chart"] = {"max": max_u, "line": " ".join(pts)}

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


@router.get("/auswertung")
def auswertung(request: Request, db=DbDep, p: str = analytics.DEFAULT_PERIOD):
    data = analytics.dashboard(db, p)
    _svg_geometry(data)
    return render(request, "auswertung.html", d=data)
