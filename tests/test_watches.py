"""Bestandsverwaltung: Anlegen, Validierung, Geld, Filter, Status, Löschen."""
from __future__ import annotations

from sqlalchemy import select

from app.models import Watch, WatchStatus
from tests.conftest import create_watch, csrf_for, post_form


def test_create_watch_and_money_persisted(auth_client, env) -> None:
    watch_id = create_watch(auth_client)
    r = auth_client.get(f"/watches/{watch_id}")
    assert r.status_code == 200
    assert "Omega Speedmaster Professional" in r.text
    # USD-Einkauf 1.000,00 × Kurs 1,1000 = EUR 1.100,00 (exakt in Cents)
    assert "1.000,00 USD" in r.text
    assert "1.100,00" in r.text
    assert "45,10" in r.text
    assert "2.100,00" in r.text

    from app.db import make_session_factory

    app = auth_client.app
    Session = make_session_factory(app.state.engine)
    with Session() as db:
        watch = db.get(Watch, watch_id)
        assert watch is not None
        assert watch.purchase_price_usd_cents == 100_000
        assert watch.usd_eur_rate_milli == 1100
        assert watch.purchase_price_cents == 110_000
        assert len(watch.extra_costs) == 2
        assert watch.extra_costs[0].amount_cents == 4510
        assert watch.extra_costs[0].category.label == "Versand DHL (DE)"
        assert watch.extra_costs[1].amount_cents == 1800
        assert watch.target_price_cents == 210000
        assert watch.status == WatchStatus.IN_GERMANY
        assert watch.provenance.value == "manual"


def test_validation_errors_are_server_side(auth_client) -> None:
    csrf = csrf_for(auth_client, "/watches/new")
    r = post_form(
        auth_client,
        "/watches/new",
        {"title": "", "status": "in_germany", "purchase_price": "kein-geld"},
        csrf=csrf,
    )
    assert r.status_code == 400
    assert "Titel" in r.text or "title" in r.text


def test_status_change_logged_with_event(auth_client) -> None:
    watch_id = create_watch(auth_client)
    csrf = csrf_for(auth_client, f"/watches/{watch_id}")
    r = post_form(
        auth_client,
        f"/watches/{watch_id}/status",
        {"status": "in_transit", "note": "DHL 1234"},
        csrf=csrf,
        follow_redirects=False,
    )
    assert r.status_code == 303
    page = auth_client.get(f"/watches/{watch_id}")
    assert "Unterwegs" in page.text
    assert "DHL 1234" in page.text
    # Verlauf zeigt Vorher/Nachher:
    assert "Beim Lieferant" in page.text


def test_list_filter_and_search(auth_client) -> None:
    a = create_watch(
        auth_client,
        title="Rolex Submariner 126610LN",
        brand="Rolex",
        internal_notes="NOTIZ-SPEZIAL-ROLEX",
    )
    b = create_watch(
        auth_client,
        title="Tudor Black Bay 58",
        brand="Tudor",
        status="in_china",
        internal_notes="andere Notiz",
    )
    r = auth_client.get("/watches?q=Tudor")
    assert "Tudor Black Bay 58" in r.text
    assert "Rolex Submariner" not in r.text

    r = auth_client.get("/watches?status=in_china")
    assert "Tudor Black Bay 58" in r.text
    assert "Rolex Submariner" not in r.text

    # Interne Suche darf interne Notizen treffen (nur intern sichtbar):
    r = auth_client.get("/watches?q=NOTIZ-SPEZIAL-ROLEX")
    assert "Rolex Submariner" in r.text
    assert "Tudor Black Bay" not in r.text

    # Statusseiten der Übersicht:
    r = auth_client.get("/")
    assert "Übersicht" in r.text
    assert a and b


def test_archive_keeps_data_and_hides_from_default(auth_client) -> None:
    watch_id = create_watch(auth_client)
    csrf = csrf_for(auth_client, f"/watches/{watch_id}")
    r = post_form(
        auth_client, f"/watches/{watch_id}/archive", {}, csrf=csrf, follow_redirects=False
    )
    assert r.status_code == 303
    listing = auth_client.get("/watches")
    assert "Omega Speedmaster" not in listing.text
    listing_archived = auth_client.get("/watches?archived=1")
    assert "Omega Speedmaster" in listing_archived.text
    detail = auth_client.get(f"/watches/{watch_id}")
    assert detail.status_code == 200
    assert "archiviert" in detail.text


def test_delete_removes_watch_and_related_rows(auth_client) -> None:
    watch_id = create_watch(auth_client)
    csrf = csrf_for(auth_client, f"/watches/{watch_id}")
    r = post_form(
        auth_client, f"/watches/{watch_id}/delete", {}, csrf=csrf, follow_redirects=False
    )
    assert r.status_code == 303
    assert auth_client.get(f"/watches/{watch_id}").status_code == 404

    app = auth_client.app
    from app.db import make_session_factory
    from app.models import ListingDraft, StatusEvent, WatchImage

    Session = make_session_factory(app.state.engine)
    with Session() as db:
        assert db.get(Watch, watch_id) is None
        assert db.scalars(select(StatusEvent).where(StatusEvent.watch_id == watch_id)).all() == []
        assert db.scalars(select(ListingDraft).where(ListingDraft.watch_id == watch_id)).all() == []
        assert db.scalars(select(WatchImage).where(WatchImage.watch_id == watch_id)).all() == []


def test_edit_updates_fields(auth_client) -> None:
    watch_id = create_watch(auth_client)
    csrf = csrf_for(auth_client, f"/watches/{watch_id}/edit")
    r = post_form(
        auth_client,
        f"/watches/{watch_id}/edit",
        {
            "title": "Omega Speedmaster (Edit)",
            "status": "reserved",
            "purchase_price": "999,99",
            "usd_eur_rate": "1,0000",
            "internal_notes": "neue Notiz",
            "spec_on_kaliber": "on",
            "spec_kaliber": "3861",
        },
        csrf=csrf,
        follow_redirects=False,
    )
    assert r.status_code == 303
    page = auth_client.get(f"/watches/{watch_id}")
    assert "Omega Speedmaster (Edit)" in page.text
    assert "999,99" in page.text
    assert "Reserviert" in page.text


def test_public_approved_flag_stored_but_never_delivered(auth_client) -> None:
    watch_id = create_watch(auth_client, public_approved="1")
    page = auth_client.get(f"/watches/{watch_id}")
    assert "vormerken" in page.text or "zurücknehmen" in page.text
    app = auth_client.app
    from app.db import make_session_factory

    Session = make_session_factory(app.state.engine)
    with Session() as db:
        watch = db.get(Watch, watch_id)
        assert watch.public_approved is True
    # Es gibt KEINE öffentliche Auslieferungsroute (nur interne):
    for path in ("/public", "/inventory", "/api/public", f"/watches/{watch_id}/public"):
        assert auth_client.get(path).status_code in {401, 404}


def test_charge_anlegen_teilt_kosten_anteilig(auth_client) -> None:
    """Charge mit3 Uhren: Versand/„Wise“ werden anteilig, die Versicherung
    EK-basiert je Uhr berechnet; individuelle Kosten bleiben an ihrer Uhr;
    alle Uhren werden eigene Bestandseinträge mit purchase_batch."""
    from app.db import make_session_factory
    from app.forms import category_choices
    from app.models import Watch, WatchStatus
    from app.money import percent_amount
    from app.routers.watches import _cat_default, _split_even

    Session = make_session_factory(auth_client.app.state.engine)
    with Session() as db:
        cats = category_choices(db)
    ship_cat = _cat_default(cats, "versand")
    wise_cat = _cat_default(cats, "wise", "weise", "gebühr")
    ins_cat = _cat_default(cats, "versicherung")

    csrf = csrf_for(auth_client, "/watches/charge")
    data: dict = {
        "csrf_token": csrf,
        "charge_count": "3",
        "charge_rate": "0,893",
        "charge_shipping": "100,00",
        "charge_wise": "9,65",
        "charge_ins_on": "on",
        "charge_ins_pct": "6",
        "charge_ins_cur": "USD",
        "charge_cat_ship": ship_cat,
        "charge_cat_wise": wise_cat,
        "charge_cat_ins": ins_cat,
        "charge_label": "Testcharge Nov",
    }
    eks = ["400,00", "500,00", "400,00"]
    for i, ek in enumerate(eks):
        data.update(
            {
                f"w{i}_status": "in_transit",
                f"w{i}_usd_eur_rate": "0,893",
                f"w{i}_brand": "Omega",
                f"w{i}_model": f"Test {i + 1}",
                f"w{i}_purchase_price": ek,
                f"w{i}_extra_cost_rows": "1",
            }
        )
    # Individuelle Kosten nur an Uhr2 (z. B. DHL):6,19 EUR
    data.update(
        {
            "w1_extra_cost_rows": "1",
            "w1_extra_cost_category_0": wise_cat,
            "w1_extra_cost_currency_0": "EUR",
            "w1_extra_cost_amount_0": "6,19",
        }
    )

    r = auth_client.post("/watches/charge", data=data, follow_redirects=False)
    assert r.status_code == 303, r.text[:600]

    Session = make_session_factory(auth_client.app.state.engine)
    with Session() as db:
        watches = (
            db.query(Watch)
            .filter(Watch.purchase_batch == "Testcharge Nov")
            .order_by(Watch.created_at)
            .all()
        )
        assert len(watches) == 3
        assert all(w.status == WatchStatus.IN_TRANSIT for w in watches)
        assert all(w.purchase_batch == "Testcharge Nov" for w in watches)

        # Versand100 USD /3 = exakte Summe, Rest auf die letzten Zeilen
        ship_parts = _split_even(10000, 3)
        assert sum(ship_parts) == 10000
        for w, part in zip(watches, ship_parts, strict=True):
            ship_rows = [c for c in w.extra_costs if c.category_id == ship_cat]
            assert len(ship_rows) == 1
            assert ship_rows[0].amount_cents == part
            assert ship_rows[0].currency == "USD"

        # Wise9,65 EUR /3 → exakte Summe in EUR-Positionen
        wise_parts = _split_even(965, 3)
        assert sum(wise_parts) == 965
        for w, part in zip(watches, wise_parts, strict=True):
            wise_rows = [c for c in w.extra_costs if c.category_id == wise_cat]
            # Anteil der Charge UND (nur an Uhr2) die individuelle Position coexistieren
            assert any(r.amount_cents == part for r in wise_rows)

        # Versicherung EK-basiert: % × (EK + Versandanteil) je Uhr
        for w, ek_str, part in zip(watches, eks, ship_parts, strict=True):
            ins_rows = [c for c in w.extra_costs if c.category_id == ins_cat]
            assert len(ins_rows) == 1
            row = ins_rows[0]
            ek_cents = int(round(float(ek_str.replace(",", ".")) * 100))
            assert row.basis_cents == ek_cents + part
            assert row.amount_cents == percent_amount(ek_cents + part, 6000)
            assert row.percent_milli == 6000

        # Individuelle Kosten nur an Uhr2
        ind = [
            c
            for w in watches
            for c in w.extra_costs
            if c.category_id == wise_cat and c.currency == "EUR" and c.amount_cents == 619
        ]
        assert len(ind) == 1

        # Kein Rest-Bestand: die Charge hat nur diese3 Datensätze
        assert all(w.created_at is not None for w in watches)


def test_charge_ohne_kurs_legt_nichts_an(auth_client) -> None:
    """Pflichtfeld Kurs fehlt →400, und es werden KEINE Uhren angelegt."""
    from app.db import make_session_factory
    from app.models import Watch

    before = auth_client.get("/watches/charge").status_code
    assert before == 200
    csrf = csrf_for(auth_client, "/watches/charge")
    r = auth_client.post(
        "/watches/charge",
        data={
            "csrf_token": csrf,
            "charge_count": "1",
            "charge_rate": "",  # Pflichtfeld fehlt
            "w0_status": "in_transit",
            "w0_brand": "Rolex",
            "w0_model": "Sub",
            "w0_purchase_price": "100,00",
            "w0_extra_cost_rows": "1",
        },
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert "Kurs erforderlich" in r.text

    Session = make_session_factory(auth_client.app.state.engine)
    with Session() as db:
        assert db.query(Watch).filter(Watch.purchase_batch.isnot(None)).count() == 0
