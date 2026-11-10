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


# ---------- Aufgeteilter Einkauf (Ware + Versand) & CNY ----------


def test_split_purchase_includes_shipping_in_avg(auth_client) -> None:
    type_id = _make_type(auth_client, "Box D")
    csrf = csrf_for(auth_client, "/boxes")
    r = post_form(
        auth_client,
        "/boxes/purchases",
        {"box_type_id": type_id, "quantity": "10",
         "total_cost": "80,00", "shipping_cost": "40,00", "currency": "EUR"},
        csrf=csrf, follow_redirects=True,
    )
    assert r.status_code == 200
    # (80 + 40) / 10 = 12,00 € pro Box
    assert "Ø 12,00 €/Box" in r.text
    Session = make_session_factory(auth_client.app.state.engine)
    with Session() as db:
        a = next(x for x in overview(db) if x["id"] == type_id)
        assert a["goods_spend"] == 8000
        assert a["shipping_spend"] == 4000
        assert a["total_spend"] == 12000
        assert a["avg"] == 1200
    page = auth_client.get("/boxes")
    assert "40,00" in page.text  # Versand-Spalte sichtbar


def test_cny_purchase_converts_and_snapshots_rate(auth_client) -> None:
    type_id = _make_type(auth_client, "Box CNY")
    csrf = csrf_for(auth_client, "/boxes")
    # 620,00 CNY Ware + 314,00 CNY Versand, Kurs 0,127 → 78,74 € + 39,87 €
    r = post_form(
        auth_client,
        "/boxes/purchases",
        {"box_type_id": type_id, "quantity": "10",
         "currency": "CNY", "total_cost": "620,00", "shipping_cost": "314,00",
         "rate": "0,1273"},
        csrf=csrf, follow_redirects=True,
    )
    assert r.status_code == 200, r.text[:300]
    assert "CNY, Kurs 0,127" in r.text
    Session = make_session_factory(auth_client.app.state.engine)
    with Session() as db:
        from app.models import BoxPurchase

        p = db.query(BoxPurchase).filter(BoxPurchase.box_type_id == type_id).one()
        assert p.currency == "CNY"
        assert p.rate_milli == 127
        assert p.total_cost_cents == 7874
        assert p.shipping_cents == 3988  # 314,00 × 0,127 = 39,878 € → 39,88 kaufmännisch
        a = next(x for x in overview(db) if x["id"] == type_id)
        # (78,74 + 39,87) / 10 = 11,86 €
        assert a["avg"] == 1186
    page = auth_client.get("/boxes")
    assert "CNY @ 0,127" in page.text


def test_cny_without_rate_is_rejected(auth_client) -> None:
    type_id = _make_type(auth_client, "Box CNY2")
    csrf = csrf_for(auth_client, "/boxes")
    r = post_form(
        auth_client,
        "/boxes/purchases",
        {"box_type_id": type_id, "quantity": "5",
         "currency": "CNY", "total_cost": "500,00"},
        csrf=csrf, follow_redirects=True,
    )
    assert r.status_code == 200
    assert "Kurs erforderlich" in r.text
    Session = make_session_factory(auth_client.app.state.engine)
    with Session() as db:
        from app.models import BoxPurchase

        assert db.query(BoxPurchase).filter(BoxPurchase.box_type_id == type_id).count() == 0


def test_fx_route_disabled_and_url_template(auth_client) -> None:
    from app.routers.boxes import fx_url_for

    settings = auth_client.app.state.settings

    # URL-Bauplan ohne Netz: from=USD-Vorlage → jede andere Währung
    assert fx_url_for("https://x/latest?from=USD&to=EUR", "CNY") == (
        "https://x/latest?from=CNY&to=EUR"
    )
    assert fx_url_for("https://x/latest?from=USD&to=EUR", "USD") == (
        "https://x/latest?from=USD&to=EUR"
    )
    assert fx_url_for("https://x/custom?base=EUR", "CNY") is None
    assert fx_url_for("", "CNY") is None

    # Deaktivierter Dienst → 404 (kein Netzzugriff im Test)
    old = settings.fx_rate_url
    object.__setattr__(settings, "fx_rate_url", "")
    try:
        r = auth_client.get("/api/fx-rate?cur=cny")
        assert r.status_code == 404
        assert "deaktiviert" in r.text
        r = auth_client.get("/api/fx-rate?cur=gbp")
        assert r.status_code == 400
    finally:
        object.__setattr__(settings, "fx_rate_url", old)


def test_purchase_edit_shipping_and_delete(auth_client) -> None:
    """Versand nachträglich eintragen, Einkauf danach per Button löschen."""
    from tests.conftest import extract_csrf

    r = auth_client.post(
        "/boxes/types",
        data={"label": "T-Edit", "csrf_token": extract_csrf(auth_client.get("/boxes").text)},
        follow_redirects=False,
    )
    assert r.status_code == 303
    r = auth_client.post(
        "/boxes/purchases",
        data={
            "box_type_id": _type_id(auth_client, "T-Edit"),
            "quantity": "10",
            "currency": "EUR",
            "total_cost": "80,00",
            "shipping_cost": "0",
            "csrf_token": extract_csrf(auth_client.get("/boxes").text),
        },
        follow_redirects=False,
    )
    assert r.status_code == 303

    from app.db import make_session_factory
    from app.models import BoxPurchase

    Session = make_session_factory(auth_client.app.state.engine)
    with Session() as db:
        purchase = db.query(BoxPurchase).order_by(BoxPurchase.id.desc()).first()
        assert purchase is not None
        pid = purchase.id

    # Bearbeiten: Versand später hinzufügen
    r = auth_client.post(
        f"/boxes/purchases/{pid}",
        data={
            "quantity": "10",
            "total_cost": "80,00",
            "shipping_cost": "12,50",
            "note": "nachgetragen",
            "csrf_token": extract_csrf(auth_client.get("/boxes").text),
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert f"edit={pid}" in r.headers["location"]
    with Session() as db:
        purchase = db.get(BoxPurchase, pid)
        assert purchase is not None
        assert purchase.shipping_cents == 1250
        assert purchase.note == "nachgetragen"
        assert purchase.quantity == 10

    # Löschen (Bestätigungs-Button im UI) – AUCH die aus der Page generierte
    # Action-URL muss numerisch sein (Regression: p.id fehlte im Dict →
    # "/boxes/purchases//delete" → 404 im Browser)
    page = auth_client.get("/boxes").text
    assert "wirklich löschen" in page
    import re as _re

    ids = _re.findall(r'action="/boxes/purchases/(\d+)"', page)
    assert ids, "keine numerische purchase-id in den Action-URLs"
    assert f'action="/boxes/purchases/{ids[0]}/delete"' in page
    r = auth_client.post(
        f"/boxes/purchases/{pid}/delete",
        data={"csrf_token": extract_csrf(auth_client.get("/boxes").text)},
        follow_redirects=False,
    )
    assert r.status_code == 303
    with Session() as db:
        assert db.get(BoxPurchase, pid) is None


def _type_id(auth_client, label: str) -> str:
    from app.db import make_session_factory
    from app.models import BoxType

    Session = make_session_factory(auth_client.app.state.engine)
    with Session() as db:
        row = db.query(BoxType).filter(BoxType.label == label).one()
        return str(row.id)


def test_box_single_sale_lifecycle(auth_client) -> None:
    """Einzelverkauf: Bestand mindert sich, Erlös fließt ins Dashboard,
    Über-Bestand wird abgelehnt, Löschen zählt zurück."""
    import re as _re

    from app.db import make_session_factory
    from app.models import BoxSale
    from app.services.boxes import overview
    from tests.conftest import extract_csrf

    csrf = extract_csrf(auth_client.get("/boxes").text)
    r = auth_client.post(
        "/boxes/types",
        data={"label": "T-Sale", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert r.status_code == 303
    csrf = extract_csrf(auth_client.get("/boxes").text)
    r = auth_client.post(
        "/boxes/purchases",
        data={
            "box_type_id": _type_id(auth_client, "T-Sale"),
            "quantity": "10",
            "currency": "EUR",
            "total_cost": "80,00",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert r.status_code == 303

    Session = make_session_factory(auth_client.app.state.engine)
    with Session() as db:
        row = next(r for r in overview(db) if r["label"] == "T-Sale")
        assert row["stock"] == 10

    # 1) Verkauf 5× mit Erlös 90,00
    csrf = extract_csrf(auth_client.get("/boxes").text)
    r = auth_client.post(
        "/boxes/sales",
        data={
            "box_type_id": _type_id(auth_client, "T-Sale"),
            "quantity": "5",
            "sale_price": "90,00",
            "note": "einzeln",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    with Session() as db:
        row = next(r for r in overview(db) if r["label"] == "T-Sale")
        assert row["stock"] == 5
        sale = db.query(BoxSale).one()
        assert sale.sale_price_cents == 9000

    page = auth_client.get("/boxes").text
    assert "Verkaufte Einzelboxen" in page
    assert "90,00 €" in page
    sale_ids = _re.findall(r'action="/boxes/sales/(\d+)/delete"', page)
    assert sale_ids, "keine numerische Sale-ID in der Page"

    # 2) Dashboard: Stückzahl + Erlös im Zeitraum
    dash = auth_client.get("/auswertung").text
    assert "Boxen verkauft (Zeitraum)" in dash
    assert "5 Stk" in dash
    assert "Erlös 90,00 €" in dash

    # 3) Über-Bestand wird abgelehnt (kein neuer Eintrag)
    csrf = extract_csrf(auth_client.get("/boxes").text)
    r = auth_client.post(
        "/boxes/sales",
        data={
            "box_type_id": _type_id(auth_client, "T-Sale"),
            "quantity": "99",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "Nur 5×" in auth_client.get("/boxes").text
    with Session() as db:
        assert db.query(BoxSale).count() == 1

    # 4) Löschen zählt den Bestand zurück
    csrf = extract_csrf(auth_client.get("/boxes").text)
    r = auth_client.post(
        f"/boxes/sales/{sale_ids[0]}/delete",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert r.status_code == 303
    with Session() as db:
        assert db.query(BoxSale).count() == 0
        row = next(r for r in overview(db) if r["label"] == "T-Sale")
        assert row["stock"] == 10
