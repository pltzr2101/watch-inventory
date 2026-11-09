"""Box-Inventar (Paket B): Durchschnitts-EK, abgeleiteter Bestand,
Verkaufs-Snapshots, geteilte Marge Uhr/Box und Ausschluss aus Anzeigen."""

from __future__ import annotations

from app.db import make_session_factory
from app.models import BoxType, Watch
from app.services.boxes import avg_unit_cost, overview, unit_cost
from tests.conftest import create_watch, csrf_for, post_form


def _make_type(client, label: str) -> str:
    csrf = csrf_for(client, "/boxes")
    r = post_form(client, "/boxes/types", {"label": label}, csrf=csrf, follow_redirects=True)
    assert r.status_code == 200
    Session = make_session_factory(client.app.state.engine)
    with Session() as db:
        row = db.query(BoxType).filter(BoxType.label == label).one()
        return row.id


def _buy(client, type_id: str, quantity: str, total: str):  # type: ignore[no-untyped-def]
    csrf = csrf_for(client, "/boxes")
    r = post_form(
        client,
        "/boxes/purchases",
        {"box_type_id": type_id, "quantity": quantity, "total_cost": total},
        csrf=csrf,
        follow_redirects=True,
    )
    assert r.status_code == 200, r.text[:300]
    return r


def test_avg_cost_and_stock_math(auth_client) -> None:
    type_id = _make_type(auth_client, "Box A")
    _buy(auth_client, type_id, "10", "80,00")
    r_last = _buy(auth_client, type_id, "5", "50,00")
    # Flash lebt nur auf der Redirect-Antwort (Once-Semantik):
    assert "Ø 10,00 €/Box" in r_last.text

    # 130,00 € / 15 = 8,67 € gewichteter Durchschnitt (kaufmännisch)
    assert unit_cost(15, 13000) == 867
    Session = make_session_factory(auth_client.app.state.engine)
    with Session() as db:
        assert avg_unit_cost(db, type_id) == 867
        rows = overview(db)
        a = next(r for r in rows if r["id"] == type_id)
        assert a["stock"] == 15
        assert a["avg"] == 867
        assert a["total_spend"] == 13000

    page = auth_client.get("/boxes")
    assert page.status_code == 200
    assert "8,67" in page.text  # Ø-EK sichtbar
    assert "130,00" in page.text  # Gesamt-Einkauf


def test_sold_with_box_flow_margins_and_derived_stock(auth_client) -> None:
    type_id = _make_type(auth_client, "Box Flow")
    _buy(auth_client, type_id, "10", "80,00")  # Ø 8,00 €
    watch_id = create_watch(auth_client)

    csrf = csrf_for(auth_client, f"/watches/{watch_id}")
    r = post_form(
        auth_client,
        f"/watches/{watch_id}/status",
        {"status": "sold", "sale_price": "600,00", "sale_scope": "with_box",
         "box_type_id": type_id, "box_vk_share": "50,00"},
        csrf=csrf,
    )
    assert r.status_code == 303, r.text[:300]

    Session = make_session_factory(auth_client.app.state.engine)
    with Session() as db:
        watch = db.get(Watch, watch_id)
        assert watch is not None
        assert watch.box_type_id == type_id
        assert watch.box_unit_cost_cents == 800  # Snapshot Ø
        assert watch.box_vk_share_cents == 5000
        # Bestand abgeleitet: 10 − 1 verkauft = 9
        a = next(r for r in overview(db) if r["id"] == type_id)
        assert a["stock"] == 9
        # Erwartete Margen exakt aus den DB-Werten
        total = (watch.purchase_price_cents or 0) + sum(
            c.amount_cents for c in watch.extra_costs
        )
        exp_uhr = 60000 - 5000 - total
        exp_box = 5000 - 800
        exp_gesamt = 60000 - (total + 800)

    detail = auth_client.get(f"/watches/{watch_id}")
    assert detail.status_code == 200
    assert "Uhr + Box (Box Flow)" in detail.text
    assert "Box-EK (Ø, Snapshot)" in detail.text
    assert "davon Box-Anteil (VK)" in detail.text
    assert "Marge Uhr" in detail.text and "Marge Box" in detail.text
    assert "Marge (gesamt)" in detail.text
    from app.money import format_money

    assert format_money(exp_uhr) in detail.text
    assert format_money(exp_box) in detail.text
    assert format_money(exp_gesamt) in detail.text

    # Verkauf rückgängig → Bestand frei, Snapshots leer
    csrf = csrf_for(auth_client, f"/watches/{watch_id}")
    r = post_form(
        auth_client, f"/watches/{watch_id}/status",
        {"status": "in_germany"}, csrf=csrf,
    )
    assert r.status_code == 303
    with Session() as db:
        watch = db.get(Watch, watch_id)
        assert watch is not None
        assert watch.box_type_id is None
        a = next(r for r in overview(db) if r["id"] == type_id)
        assert a["stock"] == 10


def test_sold_validations_box_type_and_share(auth_client) -> None:
    type_id = _make_type(auth_client, "Box B")  # ohne Einkauf → Ø 0 + Warnung
    watch_id = create_watch(auth_client)
    csrf = csrf_for(auth_client, f"/watches/{watch_id}")

    # Mit Box, aber ohne Typ → 400
    r = post_form(
        auth_client, f"/watches/{watch_id}/status",
        {"status": "sold", "sale_price": "600,00", "sale_scope": "with_box"},
        csrf=csrf,
    )
    assert r.status_code == 400
    assert "Box-Typ wählen" in r.text

    # VK-Anteil > Gesamt-VK → 400
    r = post_form(
        auth_client, f"/watches/{watch_id}/status",
        {"status": "sold", "sale_price": "600,00", "sale_scope": "with_box",
         "box_type_id": type_id, "box_vk_share": "900,00"},
        csrf=csrf,
    )
    assert r.status_code == 400
    assert "überschreiten" in r.text

    # Ohne Einkauf: erlaubt, aber Snapshots 0 + klarer Hinweis
    r = post_form(
        auth_client, f"/watches/{watch_id}/status",
        {"status": "sold", "sale_price": "600,00", "sale_scope": "with_box",
         "box_type_id": type_id},
        csrf=csrf, follow_redirects=True,
    )
    assert r.status_code == 200
    assert "Kein Box-Einkauf erfasst" in r.text
    assert "0,00 €" in r.text  # Ø-EK (Snapshot) 0,00 € sichtbar


def test_box_data_never_in_listing(auth_client) -> None:
    type_id = _make_type(auth_client, "Box Geheim")
    _buy(auth_client, type_id, "10", "83,00")  # Ø 8,30 – Distinktivwert
    watch_id = create_watch(auth_client)
    csrf = csrf_for(auth_client, f"/watches/{watch_id}")
    post_form(
        auth_client, f"/watches/{watch_id}/status",
        {"status": "sold", "sale_price": "600,00", "sale_scope": "with_box",
         "box_type_id": type_id, "box_vk_share": "50,00"},
        csrf=csrf,
    )

    for platform in ("reddit", "facebook"):
        body = auth_client.get(f"/watches/{watch_id}/listing?platform={platform}").text
        assert "8,30" not in body, f"Box-EK im {platform}-Listing!"
        assert "Box-EK" not in body
        assert "Snapshot" not in body
        # interne Uhr-Einkaufsdaten weiterhin draußen (bestehende Regel)
        assert "STRENG-INTERN" not in body


def test_duplicate_and_archived_type_handling(auth_client) -> None:
    _make_type(auth_client, "Box C")
    csrf = csrf_for(auth_client, "/boxes")
    # Casegleiches Duplikat abgelehnt
    r = post_form(auth_client, "/boxes/types", {"label": "box c"}, csrf=csrf, follow_redirects=True)
    assert "bereits" in r.text
    # Archivieren → nicht mehr im Verkaufs-Dialog wählbar
    Session = make_session_factory(auth_client.app.state.engine)
    with Session() as db:
        type_id = db.query(BoxType).filter(BoxType.label == "Box C").one().id
    r = post_form(
        auth_client, f"/boxes/types/{type_id}/archive", {},
        csrf=csrf, follow_redirects=True,
    )
    assert r.status_code == 200
    watch_id = create_watch(auth_client)
    detail = auth_client.get(f"/watches/{watch_id}")
    assert "Box C (0 auf Lager)" not in detail.text
    # Wieder aktivieren
    r = post_form(
        auth_client, f"/boxes/types/{type_id}/archive",
        {"unarchive": "1"}, csrf=csrf, follow_redirects=True,
    )
    assert r.status_code == 200
