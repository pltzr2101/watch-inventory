"""Titel-Bausteine, USD/Kurs, Kostenpositionen, Specs-Checkliste,
Verkaufs-Pflicht und Bildreihenfolge (reorder) – Kernlogik der V2-Formulare."""

from __future__ import annotations

import pytest

from app.forms import compose_title
from app.money import RateError, format_rate, parse_rate, usd_to_eur
from tests.conftest import create_watch, csrf_for, post_form

# ---------- Titel aus Bausteinen ----------


def test_compose_title_full_and_partial() -> None:
    assert compose_title("Rolex", "VSF", "V2", "Submariner", "Jubilee", "green dial") == (
        "Rolex VSF – V2 – Submariner, Jubilee, green dial"
    )
    assert compose_title(None, "Clean", None, "Submariner", None, None) == (
        "Clean – Submariner"
    )
    assert compose_title("Omega", None, None, None, None, "Snoopy") == "Omega, Snoopy"
    assert compose_title(None, None, None, None, None, None) == ""


def test_title_composed_server_side_when_missing(auth_client) -> None:
    csrf = csrf_for(auth_client, "/watches/new")
    r = post_form(
        auth_client,
        "/watches/new",
        {
            "title": "",
            "brand": "Rolex",
            "factory": "VSF",
            "model": "Submariner",
            "status": "in_china",
        },
        csrf=csrf,
        follow_redirects=False,
    )
    assert r.status_code == 303
    from app.db import make_session_factory
    from app.models import Watch

    Session = make_session_factory(auth_client.app.state.engine)
    with Session() as db:
        watch = db.get(Watch, r.headers["location"].rsplit("/", 1)[-1])
        assert watch is not None
        assert watch.title == "Rolex VSF – Submariner"
        assert watch.factory == "VSF"


def test_title_required_without_any_building_block(auth_client) -> None:
    csrf = csrf_for(auth_client, "/watches/new")
    r = post_form(
        auth_client,
        "/watches/new",
        {"title": "", "status": "in_china"},
        csrf=csrf,
    )
    assert r.status_code == 400
    assert "Titel" in r.text


# ---------- USD + Kurs ----------


def test_rate_parse_roundtrip() -> None:
    assert parse_rate("1,0923") == 1092
    assert parse_rate("1.1") == 1100
    assert format_rate(1100) == "1,1"
    assert format_rate(1092) == "1,092"
    for bad in ("abc", "0,1", "9", "1,12345", ""):
        if bad == "":
            assert parse_rate(bad) is None
        else:
            with pytest.raises(RateError):
                parse_rate(bad)


def test_usd_to_eur_rounding() -> None:
    assert usd_to_eur(100_000, 1100) == 110_000  # 1.000 USD × 1,100
    assert usd_to_eur(105_000, 1100) == 115_500
    assert usd_to_eur(None, 1100) is None
    assert usd_to_eur(100_000, None) is None


def test_usd_without_rate_is_rejected(auth_client) -> None:
    csrf = csrf_for(auth_client, "/watches/new")
    r = post_form(
        auth_client,
        "/watches/new",
        {
            "title": "Ohne Kurs",
            "brand": "Test",
            "status": "in_china",
            "purchase_price": "1.000,00",
        },
        csrf=csrf,
    )
    assert r.status_code == 400
    assert "Kurs" in r.text


def test_rate_endpoint_unreachable_is_clear_error(auth_client) -> None:
    settings = auth_client.app.state.settings
    object.__setattr__(settings, "fx_rate_url", "http://127.0.0.1:9/rate")
    try:
        r = auth_client.get("/api/usd-rate")
        assert r.status_code == 502
        object.__setattr__(settings, "fx_rate_url", "")
        assert auth_client.get("/api/usd-rate").status_code == 404
    finally:
        object.__setattr__(settings, "fx_rate_url", "https://api.frankfurter.app/latest?from=USD&to=EUR")


# ---------- Zusatzkosten-Positionen ----------


def test_cost_row_requires_amount_and_category(auth_client) -> None:
    csrf = csrf_for(auth_client, "/watches/new")
    base = {"title": "Kosten-Test", "brand": "Test", "status": "in_china",
            "extra_cost_rows": "1", "extra_cost_amount_0": "30,00"}

    r = post_form(auth_client, "/watches/new", {**base, "extra_cost_category_0": ""}, csrf=csrf)
    assert r.status_code == 400
    assert "Kostenart" in r.text or "extra_cost" in r.text

    r = post_form(
        auth_client,
        "/watches/new",
        {**base, "extra_cost_amount_0": "", "extra_cost_category_0": "wise"},
        csrf=csrf,
    )
    assert r.status_code == 400

    watch_id = create_watch(auth_client)
    from app.db import make_session_factory
    from app.models import Watch

    Session = make_session_factory(auth_client.app.state.engine)
    with Session() as db:
        watch = db.get(Watch, watch_id)
        assert watch is not None
        total = sum(c.amount_cents for c in watch.extra_costs)
        assert total == 4510 + 1800  # de_dhl + wise


# ---------- Specs-Checkliste ----------


def test_spec_checked_but_empty_is_rejected(auth_client) -> None:
    csrf = csrf_for(auth_client, "/watches/new")
    r = post_form(
        auth_client,
        "/watches/new",
        {
            "title": "Spec-Test",
            "brand": "Test",
            "status": "in_china",
            "spec_on_kaliber": "on",
            "spec_kaliber": "",
        },
        csrf=csrf,
    )
    assert r.status_code == 400
    assert "muss befüllt werden" in r.text


def test_spec_without_checkbox_is_ignored(auth_client) -> None:
    watch_id = create_watch(
        auth_client,
        spec_on_kaliber="",
        spec_kaliber="Wert ohne Haken",
        spec_on_gehaeusedurchmesser="on",
        spec_gehaeusedurchmesser="40 mm",
    )
    from app.db import make_session_factory
    from app.models import Watch

    Session = make_session_factory(auth_client.app.state.engine)
    with Session() as db:
        watch = db.get(Watch, watch_id)
        assert watch is not None
        keys = {item["key"] for item in watch.specs}
        assert "Kaliber" not in keys or watch.specs[0]["value"] != "Wert ohne Haken"
        assert any(i["key"] == "Gehäusedurchmesser" for i in watch.specs)


# ---------- Verkauf = Pflichtfeld ----------


def test_sold_without_sale_price_is_rejected(auth_client) -> None:
    watch_id = create_watch(auth_client)
    csrf = csrf_for(auth_client, f"/watches/{watch_id}")
    r = post_form(
        auth_client,
        f"/watches/{watch_id}/status",
        {"status": "sold"},
        csrf=csrf,
    )
    assert r.status_code == 400
    assert "Verkaufspreis" in r.text


def test_sold_stores_price_and_shows_margin(auth_client) -> None:
    watch_id = create_watch(auth_client)
    csrf = csrf_for(auth_client, f"/watches/{watch_id}")
    r = post_form(
        auth_client,
        f"/watches/{watch_id}/status",
        {"status": "sold", "sale_price": "1.500,00"},
        csrf=csrf,
        follow_redirects=False,
    )
    assert r.status_code == 303

    from app.db import make_session_factory
    from app.models import Watch

    Session = make_session_factory(auth_client.app.state.engine)
    with Session() as db:
        watch = db.get(Watch, watch_id)
        assert watch is not None
        assert watch.sale_price_cents == 150_000
        assert watch.sold_at is not None
        # Marge: 150.000 − (110.000 EK + 6.310 Zusatz) = 33.690 Cent
        page = auth_client.get(f"/watches/{watch_id}")
        assert "336,90" in page.text


# ---------- Bildreihenfolge ohne Reload ----------


def test_reorder_sets_absolute_position(auth_client) -> None:
    watch_id = create_watch(auth_client)
    from app.db import make_session_factory
    from app.models import Watch, WatchImage

    Session = make_session_factory(auth_client.app.state.engine)
    with Session() as db:
        w = db.get(Watch, watch_id)
        assert w is not None
        first = WatchImage(watch=w, stored_name="a.jpg", original_name="a.jpg",
                           mime="image/jpeg", width=10, height=10, byte_size=1,
                           sort_order=0)
        second = WatchImage(watch=w, stored_name="b.jpg", original_name="b.jpg",
                            mime="image/jpeg", width=10, height=10, byte_size=1,
                            sort_order=1)
        db.add_all([first, second])
        db.commit()
        second_id, first_id = second.id, first.id

    csrf = csrf_for(auth_client, f"/watches/{watch_id}")
    r = post_form(
        auth_client,
        f"/images/{second_id}/reorder",
        {"index": "0"},
        csrf=csrf,
        follow_redirects=False,
    )
    assert r.status_code == 303
    with Session() as db:
        a = db.get(WatchImage, first_id)
        b = db.get(WatchImage, second_id)
        assert a is not None and b is not None
        assert b.sort_order == 0
        assert a.sort_order == 1
