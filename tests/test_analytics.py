"""Auswertungs-Dashboard (Phase 1): KPIs, Haltedauer-Regel, Pipeline, Schutz."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.db import make_session_factory
from app.models import CostCategory, StatusEvent, Watch, WatchExtraCost, WatchStatus
from tests.conftest import create_watch, csrf_for, post_form


def _seed_sold_watch(auth_client) -> str:
    """Uhr mit Zusatzkosten verkaufen; DE-Ankunft auf −30 T, Verkauf auf −5 T."""
    watch_id = create_watch(
        auth_client, purchase_price="300,00", sale_price="", internal_notes=""
    )
    csrf = csrf_for(auth_client, f"/watches/{watch_id}")
    post_form(
        auth_client, f"/watches/{watch_id}/status",
        {"status": "in_germany"}, csrf=csrf,
    )
    r = post_form(
        auth_client, f"/watches/{watch_id}/status",
        {"status": "sold", "sale_price": "450,00"}, csrf=csrf,
    )
    assert r.status_code == 303, r.text[:200]

    now = datetime.now(UTC).replace(tzinfo=None)
    Session = make_session_factory(auth_client.app.state.engine)
    with Session() as db:
        # Standard-Extras des Test-Creators entfernen → exakte Zahlen
        db.query(WatchExtraCost).filter(
            WatchExtraCost.watch_id == watch_id
        ).delete(synchronize_session=False)
        cats = {}
        for label in ("Versicherung", "DHL-Versand", "Wise-Gebühr"):
            # Standard-Kategorien sind geseedet → vorhandene nutzen
            c = db.query(CostCategory).filter(CostCategory.label == label).one_or_none()
            if c is None:
                c = CostCategory(label=label)
                db.add(c)
                db.flush()
            cats[label] = c.id
        amounts = {"Versicherung": 650, "DHL-Versand": 1120, "Wise-Gebühr": 730}
        for label, cents in amounts.items():
            db.add(
                WatchExtraCost(
                    watch_id=watch_id, category_id=cats[label],
                    currency="EUR", amount_cents=cents,
                )
            )
        watch = db.get(Watch, watch_id)
        assert watch is not None
        watch.sold_at = now - timedelta(days=5)
        for ev in (
            db.query(StatusEvent)
            .filter(StatusEvent.watch_id == watch_id)
            .all()
        ):
            if ev.to_status == WatchStatus.IN_GERMANY:
                ev.changed_at = now - timedelta(days=30)
        db.commit()
    return watch_id


def test_dashboard_kpis_pipeline_and_top(auth_client) -> None:
    watch_id = _seed_sold_watch(auth_client)
    from app.money import format_money

    # EK kommt aus USD × Kurs → dynamisch aus der DB lesen
    Session = make_session_factory(auth_client.app.state.engine)
    with Session() as db:
        watch = db.get(Watch, watch_id)
        assert watch is not None
        eur_ek = watch.purchase_price_cents or 0
    # VK 450,00 − EK − Zusatz 25,00 = Marge
    margin = 45000 - eur_ek - 2500

    r = auth_client.get("/auswertung")
    assert r.status_code == 200
    body = r.text

    # Seine KPI-Liste: Ø-EK, Ø-Zusatz, Ø-Versicherung, Ø-Versand, Ø-Box, …
    assert "Ø EK / Uhr" in body and format_money(eur_ek) in body
    assert "Ø Zusatzkosten / Uhr" in body and "25,00 €" in body
    assert "Ø Versicherung / Uhr" in body and "6,50 €" in body
    assert "Ø Versand / Uhr" in body and "11,20 €" in body
    assert "Ø EK / Box" in body
    assert "Ø Marge / Stück" in body and format_money(margin) in body
    # Haltedauer: DE −30 T → Verkauf −5 T = 25 Tage
    assert "Ø Haltedauer" in body and "25 T" in body
    # Pipeline + Top-Margen
    assert "Verkauft (Zeitraum 90)" in body
    assert "+" + format_money(margin) in body
    # Kalkulator-Seeds als JSON (echte Durchschnisse)
    assert f'"ek": {eur_ek}' in body
    assert '"extra": 2500' in body


def test_dashboard_period_filter(auth_client) -> None:
    _seed_sold_watch(auth_client)
    r = auth_client.get("/auswertung?p=30")
    assert r.status_code == 200
    assert "Verkauft (Zeitraum 30)" in r.text
    r = auth_client.get("/auswertung?p=all")
    assert r.status_code == 200
    assert "Verkauft (Zeitraum all)" in r.text


def test_dashboard_requires_auth(auth_client) -> None:
    unauth = TestClient(auth_client.app)
    r = unauth.get("/auswertung", follow_redirects=False)
    # 401 (API-Stil) oder Login-Redirect – nie die Daten
    assert r.status_code in (302, 307, 401)
    if r.status_code != 401:
        assert "/login" in r.headers.get("location", "")
    assert "Ø EK / Uhr" not in r.text


def test_dashboard_fills_kpis_from_active_stock_without_sales(auth_client) -> None:
    """Sein Fehlerfall: nur aktive Uhren → Einstands-KPIs müssen trotzdem
    füllen; Verkaufs-KPIs zeigen „–“ statt geratenen Nullen."""
    from app.money import format_money

    watch_id = create_watch(auth_client, purchase_price="300,00")
    Session = make_session_factory(auth_client.app.state.engine)
    with Session() as db:
        w = db.get(Watch, watch_id)
        assert w is not None
        eur_ek = w.purchase_price_cents or 0
    assert eur_ek > 0

    r = auth_client.get("/auswertung")
    assert r.status_code == 200
    body = r.text
    assert "Verkauft im Zeitraum: 0" in body
    assert format_money(eur_ek) in body            # Ø-EK aus dem Bestand
    assert "Gesamtbestand, 1 Uhren mit EK-Wert" in body
    assert "keine Verkäufe im Zeitraum" in body    # Marge/Haltedauer ehrlich
    # Kalkulator-Seed liegt als data-Attribut vor (CSP-konform)
    assert f'"ek": {eur_ek}' in body


def test_nachgetragener_verkauf_zahlt_in_auswertung(auth_client) -> None:
    """Nachgetragener Verkauf: sold_at muss gesetzt werden (Anlege-Route);
    Legacy-Daten ohne sold_at zählen über den status_changed_at-Fallback.
    (User-Report: Marge im Detail sichtbar, Auswertung zeigte „0“.)"""
    from app.db import make_session_factory
    from app.models import Watch, WatchStatus

    # Fall A: direkt als VERKAUFT angelegt (Nachtrag)
    from tests.conftest import create_watch

    watch_id = create_watch(
        auth_client,
        sku="VK-NACH",
        title="Nachgetragener Verkauf",
        status="sold",
        purchase_price="449,94",
        sale_price="600,00",
    )

    Session = make_session_factory(auth_client.app.state.engine)
    with Session() as db:
        watch = db.get(Watch, watch_id)  # type: ignore[arg-type]
        assert watch is not None
        assert watch.status == WatchStatus.SOLD
        assert watch.sold_at is not None, "sold_at fehlt beim nachgetragenen Verkauf"

    page = auth_client.get("/auswertung").text
    assert "Verkauft im Zeitraum: 1" in page, "Auswertung zählt den Verkauf nicht"
    assert "keine Verkäufe im Zeitraum" not in page

    # Fall B: Legacy-Datum – Status verkauft, sold_at leer (bestehende Daten)
    with Session() as db:
        watch = db.query(Watch).filter(Watch.sku == "VK-NACH").one()
        watch.sold_at = None
        db.commit()

    page = auth_client.get("/auswertung").text
    assert "Verkauft im Zeitraum: 1" in page, (
        "Fallback status_changed_at greift nicht (Legacy ohne sold_at)"
    )
    assert "keine Verkäufe im Zeitraum" not in page


def test_chart_zeitraum_und_y_achse(auth_client) -> None:
    """Diagramm konfigurierbar: c=6|12|24|all, Y-Achsen-Ticks, Chip-Komposition
    mit dem Hauptzeitraum, unbekannte Werte fallen auf12 zurück."""
    html6 = auth_client.get("/auswertung?c=6").text
    assert "6 Monate" in html6
    from app.services.analytics import DEFAULT_PERIOD

    assert f'href="?p={DEFAULT_PERIOD}&amp;c=6"' in html6  # Chips erben den Hauptzeitraum
    html24 = auth_client.get("/auswertung?c=24").text
    assert "24 Monate" in html24
    html_all = auth_client.get("/auswertung?c=all").text
    assert "alle Monate" in html_all
    # Y-Achse: Tick-Labels mit text-anchor="end" und Gridlines
    assert 'text-anchor="end"' in html6
    assert "d.chart.ticks" not in html6  # kein ungerenderter Jinja-Rest
    # Hauptzeitraum-Chips behalten den Chart-Zeitraum
    assert 'href="?p=30&amp;c=6"' in html6
    # Unbekannter Wert →12 (Default)
    assert "12 Monate" in auth_client.get("/auswertung?c=quatsch").text


def test_chart_serie_laenge_nach_param(auth_client) -> None:
    """dashboard()-Serie folgt dem Chart-Parameter (6 vs.24 vs. alle)."""
    from app.db import make_session_factory
    from app.services import analytics

    Session = make_session_factory(auth_client.app.state.engine)
    with Session() as db:
        assert len(analytics.dashboard(db, "all", chart=6)["series"]) == 6
        assert len(analytics.dashboard(db, "all", chart=24)["series"]) == 24
        data_all = analytics.dashboard(db, "all", chart=None)
        assert data_all["chart_all"] is True
        assert data_all["chart_months"] >= 12
