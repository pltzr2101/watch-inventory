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
