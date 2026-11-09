"""Kostenpositionen V3: Währung pro Position, Prozent-Modus, Kursregel für
alle USD-Anteile, gemischte Währungen in der Marge und selbst pflegbare
Kostenkategorien."""

from __future__ import annotations

import pytest

from app.db import make_session_factory
from app.models import CostCategory, Watch
from app.money import PercentError, format_percent, parse_percent, percent_amount
from tests.conftest import cost_category_id, create_watch, csrf_for, post_form

# ---------- Prozent-Helfer (Ganzzahlrundung) ----------


def test_percent_helpers() -> None:
    assert parse_percent("6") == 6000
    assert parse_percent("6,5") == 6500
    assert parse_percent("0,005") == 5
    assert parse_percent("100") == 100_000
    assert percent_amount(11_000, 6000) == 660  # 110,00 × 6 % = 6,60
    assert percent_amount(33_000, 6000) == 1980  # 330,00 × 6 % = 19,80
    assert percent_amount(299, 1000) == 3  # kaufmännisch gerundet
    assert format_percent(6000) == "6"
    assert format_percent(6500) == "6,5"
    for bad in ("0", "-3", "150", "1,23456", "abc"):
        with pytest.raises(PercentError):
            parse_percent(bad)


# ---------- Prozent-Position serverseitig gerechnet + transparent gezeigt ----------


def test_percent_row_computed_and_shown(auth_client) -> None:
    csrf = csrf_for(auth_client, "/watches/new")
    payload = {
        "title": "Vers-Test",
        "brand": "Test",
        "status": "in_china",
        "usd_eur_rate": "1,0923",
        "extra_cost_rows": "1",
        "extra_cost_category_0": cost_category_id(auth_client, "Versicherung"),
        "extra_cost_currency_0": "USD",
        "extra_cost_basis_0": "110,00",
        "extra_cost_pct_0": "6",
    }
    r = post_form(auth_client, "/watches/new", payload, csrf=csrf)
    assert r.status_code == 303, r.text[:400]
    watch_id = r.headers["location"].rsplit("/", 1)[-1]

    Session = make_session_factory(auth_client.app.state.engine)
    with Session() as db:
        watch = db.get(Watch, watch_id)
        assert watch is not None
        cost = watch.extra_costs[0]
        assert cost.amount_cents == 660
        assert cost.basis_cents == 11_000
        assert cost.percent_milli == 6000
        assert cost.currency == "USD"

    detail = auth_client.get(f"/watches/{watch_id}")
    assert detail.status_code == 200
    # Herleitung vollständig sichtbar: Prozent → Betrag → EUR-Umrechnung
    assert "6 % von 110,00 USD = 6,60 USD × 1,092 = 7,21 €" in detail.text


def test_fix_and_percent_are_mutually_exclusive(auth_client) -> None:
    csrf = csrf_for(auth_client, "/watches/new")
    payload = {
        "title": "Beides-Test",
        "brand": "Test",
        "status": "in_china",
        "extra_cost_rows": "1",
        "extra_cost_category_0": cost_category_id(auth_client, "Versicherung"),
        "extra_cost_currency_0": "EUR",
        "extra_cost_amount_0": "10,00",
        "extra_cost_basis_0": "100,00",
        "extra_cost_pct_0": "6",
    }
    r = post_form(auth_client, "/watches/new", payload, csrf=csrf)
    assert r.status_code == 400
    assert "nicht beides" in r.text


# ---------- Kurs ist für ALLE USD-Anteile Pflicht ----------


def test_usd_position_requires_rate(auth_client) -> None:
    csrf = csrf_for(auth_client, "/watches/new")
    payload = {
        "title": "Kurs-Pflicht",
        "brand": "Test",
        "status": "in_china",
        "extra_cost_rows": "1",
        "extra_cost_category_0": cost_category_id(auth_client, "Versand China"),
        "extra_cost_currency_0": "USD",
        "extra_cost_amount_0": "30,00",
        # bewusst KEIN usd_eur_rate
    }
    r = post_form(auth_client, "/watches/new", payload, csrf=csrf)
    assert r.status_code == 400
    assert "Kurs erforderlich" in r.text


# ---------- Gemischte Währungen: Umrechnung + Marge ----------


def test_mixed_currencies_in_margin(auth_client) -> None:
    csrf = csrf_for(auth_client, "/watches/new")
    payload = {
        "title": "Mix-Test",
        "brand": "Test",
        "status": "in_china",
        "purchase_price": "1.000,00",  # USD-EK
        "usd_eur_rate": "1,1000",  # → 1,100
        "sale_price": "1.300,00",
        "extra_cost_rows": "2",
        "extra_cost_category_0": cost_category_id(auth_client, "Versand China"),
        "extra_cost_currency_0": "USD",
        "extra_cost_amount_0": "30,00",  # → 33,00 €
        "extra_cost_category_1": cost_category_id(auth_client, "Wise-Gebühren"),
        "extra_cost_currency_1": "EUR",
        "extra_cost_amount_1": "18,50",
    }
    r = post_form(auth_client, "/watches/new", payload, csrf=csrf)
    assert r.status_code == 303, r.text[:400]
    watch_id = r.headers["location"].rsplit("/", 1)[-1]

    Session = make_session_factory(auth_client.app.state.engine)
    with Session() as db:
        watch = db.get(Watch, watch_id)
        assert watch is not None
        assert watch.purchase_price_cents == 110_000  # 1.000 USD × 1,1
        assert watch.usd_eur_rate_milli == 1100

    detail = auth_client.get(f"/watches/{watch_id}")
    assert detail.status_code == 200
    assert "30,00 USD × 1,1 = 33,00 €" in detail.text
    # Übersichtliches dl-Layout: je Position ein dt/dd-Paar statt UL-Chaos
    assert "<dt>Versand China</dt>" in detail.text
    assert "<dt>Wise-Gebühren</dt>" in detail.text
    # Summe Zusatz = 33,00 + 18,50; Marge = 1.300 − (1.100 + 51,50)
    assert "Summe Zusatz" in detail.text and "51,50" in detail.text
    assert "148,50" in detail.text


# ---------- Kategorien selbst pflegen ----------


def test_cost_category_selfservice(auth_client) -> None:
    csrf = csrf_for(auth_client, "/cost-categories")
    page = auth_client.get("/cost-categories")
    assert page.status_code == 200
    assert "Kostenkategorien" in page.text
    assert "Versicherung" in page.text  # Startbestand gesät

    # Anlegen (casegleiches Duplikat wird abgelehnt)
    r = post_form(
        auth_client, "/cost-categories", {"label": "Zollgebühren"},
        csrf=csrf, follow_redirects=True,
    )
    assert r.status_code == 200
    assert "Zollgebühren" in r.text

    r = post_form(
        auth_client, "/cost-categories", {"label": "zollgebühren"},
        csrf=csrf, follow_redirects=True,
    )
    assert "bereits" in r.text

    Session = make_session_factory(auth_client.app.state.engine)
    with Session() as db:
        cat = db.query(CostCategory).filter(CostCategory.label == "Zollgebühren").one()
        cat_id = cat.id

    # Umbenennen wirkt global
    r = post_form(
        auth_client, f"/cost-categories/{cat_id}/rename", {"label": "Zollgebühren 2026"},
        csrf=csrf, follow_redirects=True,
    )
    assert "Zollgebühren 2026" in r.text

    # Archivieren → nicht mehr wählbar im Anlage-Formular
    r = post_form(
        auth_client, f"/cost-categories/{cat_id}/archive", {},
        csrf=csrf, follow_redirects=True,
    )
    assert "archiviert" in r.text
    form = auth_client.get("/watches/new")
    assert "Zollgebühren 2026" not in form.text

    # Wieder aktivieren → wieder wählbar
    r = post_form(
        auth_client, f"/cost-categories/{cat_id}/archive", {"unarchive": "1"},
        csrf=csrf, follow_redirects=True,
    )
    assert r.status_code == 200
    form = auth_client.get("/watches/new")
    assert "Zollgebühren 2026" in form.text


# ---------- Archivierte Kategorie bleibt in bestehender Position nutzbar ----------


def test_archived_category_still_valid_for_existing_rows(auth_client) -> None:
    watch_id = create_watch(auth_client)
    Session = make_session_factory(auth_client.app.state.engine)
    with Session() as db:
        watch = db.get(Watch, watch_id)
        assert watch is not None
        cat_id = watch.extra_costs[0].category_id

    csrf = csrf_for(auth_client, f"/watches/{watch_id}/edit")
    r = post_form(
        auth_client,
        f"/watches/{watch_id}/edit",
        {
            "title": "Keep-Edit",
            "brand": "Test",
            "status": "in_germany",
            "extra_cost_rows": "1",
            # archivierte Kategorie weiterhin zulässig:
            "extra_cost_category_0": cat_id,
            "extra_cost_currency_0": "EUR",
            "extra_cost_amount_0": "5,00",
        },
        csrf=csrf,
    )
    assert r.status_code == 303, r.text[:400]

    with Session() as db:
        watch = db.get(Watch, watch_id)
        assert watch is not None
        # archivierte Kategorie bleibt der Position erhalten
        assert watch.extra_costs[0].category_id == cat_id
