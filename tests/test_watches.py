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
        assert watch.extra_costs[0].category.value == "de_dhl"
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
