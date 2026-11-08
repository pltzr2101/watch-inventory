"""Wecatalog.cn-Integration: Preisseperation, Vorschau/Bestätigung, Grenzen."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

import app.routers.imports as imports_module
from app.config import Settings
from app.services import wecatalog
from app.services.wecatalog import (
    WecatalogDisabledError,
    WecatalogFetchError,
    fetch_catalog_entry,
    payload_has_prices,
    sanitize_payload,
    strip_prices,
)
from tests.conftest import create_watch, csrf_for, login, make_image, post_form


@pytest.fixture(autouse=True)
def clean_rate_limit():
    wecatalog._FETCH_TIMES.clear()
    yield
    wecatalog._FETCH_TIMES.clear()


# ---------------------------------------------------------------- Vorhöfe

def test_strip_prices_removes_all_price_shapes() -> None:
    dirty = {
        "title": "Rolex Sub",
        "price": "12.345,67 EUR",
        "retail_price": 9999,
        "售价": "8000 CNY",
        "USD_PRICE": "$ 5.000",
        "cost_breakdown": {"shipping": 40},
        "description": "Feine Uhr, price: 1234 EUR, nur 1.234,56€ zu haben.",
        "specs": [
            {"key": "Kaliber", "value": "3235"},
            {"key": "price", "value": "100"},
            {"key": "Street", "value": "1234"},  # enthält Ziffern, aber kein Preis
        ],
        "amount_due": 100,
        "note": "retail price only 200 USD",
    }
    cleaned = sanitize_payload(dirty)
    assert "price" not in cleaned
    assert "retail_price" not in cleaned
    assert "售价" not in cleaned
    assert "cost_breakdown" not in cleaned
    assert "amount_due" not in cleaned
    assert not payload_has_prices(cleaned)
    # Beschreibung bereinigt:
    assert "1.234,56" not in cleaned["description"]
    assert "price: 1234" not in cleaned["description"]
    assert "Feine Uhr" in cleaned["description"]
    # Spez-Preisschlüssel entfernt, inhaltlich neutrale Zeile bleibt:
    keys = [s["key"] for s in cleaned["specs"]]
    assert "price" not in keys
    assert "Kaliber" in keys
    assert "Street" in keys


def test_strip_prices_helper_handles_non_dict() -> None:
    assert strip_prices([{"price": 1}, "ok"]) == ["ok"]
    with pytest.raises(ValueError):
        sanitize_payload("kein dict")  # type: ignore[arg-type]  # absichtlich ungültig


# ---------------------------------------------------------------- Feature-Flag

def test_feature_disabled_returns_404_and_app_works(auth_client) -> None:
    for path in ("/imports", "/imports/new"):
        assert auth_client.get(path).status_code == 404
    assert auth_client.get("/").status_code == 200  # Rest der App funktioniert
    assert auth_client.get("/watches").status_code == 200


def test_disabled_raises_clear_error(env, monkeypatch) -> None:
    monkeypatch.setenv("WECATALOG_ENABLED", "0")
    from app.main import create_app

    settings = create_app().state.settings
    with pytest.raises(WecatalogDisabledError):
        wecatalog.require_enabled(settings)


# ---------------------------------------------------------------- Vorschau/Confirm

def _create_preview(client, payload: dict, files=None, watch_id: str | None = None):
    data = {"source_url": "https://www.wecatalog.cn/item/42", **payload}
    if watch_id:
        data["watch_id"] = watch_id
    csrf = csrf_for(client, "/imports/new")
    r = post_form(
        client, "/imports/new", data, csrf=csrf, files=files, follow_redirects=False
    )
    assert r.status_code == 303, r.text[:400]
    return r.headers["location"].rsplit("/", 1)[-1]


@pytest.fixture()
def wclient(env, monkeypatch):
    """Client mit aktiviertem Wecatalog-Feature."""
    monkeypatch.setenv("WECATALOG_ENABLED", "1")
    from fastapi.testclient import TestClient

    from app.main import create_app

    client = TestClient(create_app(), base_url="http://testserver")
    login(client)
    return client


def test_preview_strips_prices_and_shows_source(wclient) -> None:
    preview_id = _create_preview(
        wclient,
        {
            "title": "Omega Seamaster",
            "description": "Autorisierter Text. price: 3000 EUR, nur 3.000,00€.",
            "specs_text": "Kaliber: 8800\nprice: 2000",
        },
    )
    page = wclient.get(f"/imports/{preview_id}")
    assert page.status_code == 200
    assert "price:" not in page.text
    assert "3.000,00" not in page.text
    assert "Kaliber" in page.text
    assert "www.wecatalog.cn/item/42" in page.text

    from app.db import make_session_factory

    Session = make_session_factory(wclient.app.state.engine)
    with Session() as db:
        from app.models import ImportPreview

        preview = db.get(ImportPreview, preview_id)
        dump = json.dumps(preview.payload, ensure_ascii=False)
        assert "price" not in dump
        assert "2000" not in dump
        assert not payload_has_prices(preview.payload)


def test_confirm_creates_watch_without_source_prices(wclient, env) -> None:
    preview_id = _create_preview(
        wclient, {"title": "Cartier Santos", "description": "Katalogtext hier."}
    )
    r = post_form(
        wclient,
        f"/imports/{preview_id}/confirm",
        {"title": "Cartier Santos", "description": "Katalogtext hier.", "status": "in_china"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    watch_id = r.headers["location"].rsplit("/", 1)[-1]

    from app.db import make_session_factory
    from app.models import Watch

    Session = make_session_factory(wclient.app.state.engine)
    with Session() as db:
        watch = db.get(Watch, watch_id)
        assert watch.provenance.value == "wecatalog"
        assert watch.source_name == "Wecatalog.cn"
        assert watch.source_url == "https://www.wecatalog.cn/item/42"
        assert watch.imported_at is not None
        assert watch.import_confirmed_at is not None
        assert watch.purchase_price_cents is None  # Quellpreise importieren nie
        assert watch.target_price_cents is None
        assert watch.internal_notes in (None, "")
        assert watch.import_approved_for_listing is False  # erst separat freigeben
        assert "title" in watch.source_fields

    # UI-Kennzeichnung:
    page = wclient.get(f"/watches/{watch_id}")
    assert "Quelle: Wecatalog.cn" in page.text
    assert "Nicht freigegeben" in page.text


def test_imported_images_staged_then_adopted(wclient, env) -> None:
    from pathlib import Path

    preview_id = _create_preview(
        wclient,
        {"title": "Patek Nautilus", "description": "Text"},
        files={
            "files": [
                ("k1.jpg", make_image(color=(10, 10, 200)), "image/jpeg"),
                ("k2.jpg", make_image(color=(200, 200, 10)), "image/jpeg"),
            ]
        },
    )
    # Gestaffelt vorhanden, Vorschau abrufbar:
    staged = wclient.get(f"/imports/{preview_id}/images/0")
    assert staged.status_code == 200
    staging_root = Path(env) / "staging" / preview_id
    assert staging_root.is_dir() and len(list(staging_root.iterdir())) == 2

    r = post_form(
        wclient,
        f"/imports/{preview_id}/confirm",
        {"title": "Patek Nautilus", "description": "Text", "status": "in_china"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    watch_id = r.headers["location"].rsplit("/", 1)[-1]
    assert not staging_root.exists()  # aufgeräumt
    images_dir = Path(env) / "images"
    assert len(list(images_dir.iterdir())) == 2

    page = wclient.get(f"/watches/{watch_id}")
    assert "Katalog" in page.text  # Quellkennzeichnung der Bilder

    # Nicht freigegeben → Katalogbilder bleiben aus dem Export:
    r = wclient.get(f"/watches/{watch_id}/listing/zip?platform=reddit")
    assert r.status_code == 400  # keine freigegebenen Bilder → nur Text verweigert

    # Nach Freigabe sind Katalogbilder teilnahmeberechtigt:
    csrf = csrf_for(wclient, f"/watches/{watch_id}")
    post_form(
        wclient,
        f"/watches/{watch_id}/import-approve",
        {"approve": "1"},
        csrf=csrf,
        follow_redirects=False,
    )
    r = wclient.get(f"/watches/{watch_id}/listing/zip?platform=reddit")
    assert r.status_code == 200
    import io
    import zipfile

    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        names = zf.namelist()
        assert "anzeige-reddit.txt" in names
        assert sum(1 for n in names if n.endswith(".jpg")) == 2


def test_update_preview_never_overwrites_internal_fields(wclient) -> None:
    watch_id = create_watch(wclient)  # eigene Uhr mit Einkauf/Notizen
    # Quell-Snapshot einmalig setzen wie nach erstem Import:
    from app.db import make_session_factory
    from app.models import Watch

    Session = make_session_factory(wclient.app.state.engine)
    with Session() as db:
        watch = db.get(Watch, watch_id)
        watch.provenance = watch.provenance.WECATALOG
        watch.source_fields = ["title", "description"]
        watch.source_content = {"title": watch.title, "description": watch.description}
        db.commit()
        purchase_before = watch.purchase_price_cents
        notes_before = watch.internal_notes

    # Nutzer ändert DANACH seine Beschreibung eigenständig:
    csrf = csrf_for(wclient, f"/watches/{watch_id}/edit")
    post_form(
        wclient,
        f"/watches/{watch_id}/edit",
        {
            "title": "Omega Speedmaster Professional",
            "status": "in_germany",
            "purchase_price": "1.234,56",
            "description": "EIGENE überarbeitete Beschreibung.",
            "internal_notes": notes_before,
            "extra_costs": "45,10",
            "target_price": "2.100,00",
        },
        csrf=csrf,
        follow_redirects=False,
    )

    # Importvorschlag der Quelle mit NEUER Katalogbeschreibung + Titeländerung:
    preview_id = _create_preview(
        wclient,
        {
            "title": "OMEGA Speedmaster (Katalog-Neutitel)",
            "description": "Neue Katalogbeschreibung aus Quelle.",
            "specs_text": "Referenz: 3861",
        },
        watch_id=watch_id,
    )
    page = wclient.get(f"/imports/{preview_id}")
    assert page.status_code == 200
    assert "EIGENE überarbeitete Beschreibung." in page.text  # Diff zeigt Bestand
    assert "Neue Katalogbeschreibung aus Quelle." in page.text
    assert "NICHT automatisch übernommen" in page.text

    r = post_form(
        wclient, f"/imports/{preview_id}/confirm", {}, follow_redirects=False
    )
    assert r.status_code == 303

    with Session() as db:
        watch = db.get(Watch, watch_id)
        # Kein Preissetz aus der Quelle:
        assert watch.purchase_price_cents == purchase_before == 123456
        assert watch.target_price_cents == 210000
        assert watch.internal_notes == notes_before
        # Eigene Beschreibung NICHT stillschweigend überschrieben (und nicht
        # durch die neue Quellbeschreibung ersetzt):
        assert watch.description == "EIGENE überarbeitete Beschreibung."
        assert watch.description != "Neue Katalogbeschreibung aus Quelle."
        # Titel nicht automatisch ersetzt:
        assert watch.title == "Omega Speedmaster Professional"
        # Aber Quell-Snapshot aktualisiert:
        assert watch.source_content["description"] == "Neue Katalogbeschreibung aus Quelle."
        assert watch.source_content["title"] == "OMEGA Speedmaster (Katalog-Neutitel)"
        assert watch.imported_at is not None


def test_update_applies_when_user_has_not_edited(wclient) -> None:
    watch_id = create_watch(wclient, description="Erstbeschreibung")
    from app.db import make_session_factory
    from app.models import Watch

    Session = make_session_factory(wclient.app.state.engine)
    with Session() as db:
        watch = db.get(Watch, watch_id)
        watch.provenance = watch.provenance.WECATALOG
        watch.source_fields = ["description"]
        watch.source_content = {"description": "Erstbeschreibung"}
        db.commit()

    preview_id = _create_preview(
        wclient, {"description": "Aktualisierter Katalogtext."}, watch_id=watch_id
    )
    r = post_form(wclient, f"/imports/{preview_id}/confirm", {}, follow_redirects=False)
    assert r.status_code == 303

    with Session() as db:
        watch = db.get(Watch, watch_id)
        assert watch.description == "Aktualisierter Katalogtext."


def test_reject_discards_staging_and_changes_nothing(wclient, env) -> None:
    from pathlib import Path

    watch_id = create_watch(wclient)
    preview_id = _create_preview(
        wclient,
        {"title": "Weg damit", "description": "x"},
        files={"files": ("s.jpg", make_image(), "image/jpeg")},
    )
    staging = Path(env) / "staging" / preview_id
    assert staging.is_dir()

    r = post_form(
        wclient, f"/imports/{preview_id}/reject", {}, follow_redirects=False
    )
    assert r.status_code == 303
    assert not staging.exists()

    from app.db import make_session_factory
    from app.models import ImportPreview, Watch

    Session = make_session_factory(wclient.app.state.engine)
    with Session() as db:
        assert db.get(ImportPreview, preview_id).status == "rejected"
        assert db.get(Watch, watch_id).provenance.value == "manual"


def test_double_decide_conflict(wclient) -> None:
    preview_id = _create_preview(wclient, {"title": "Einmal", "description": "x"})
    r = post_form(
        wclient, f"/imports/{preview_id}/reject", {}, follow_redirects=False
    )
    assert r.status_code == 303
    r = post_form(
        wclient, f"/imports/{preview_id}/confirm",
        {"title": "Einmal", "description": "x"},
        follow_redirects=False,
    )
    assert r.status_code == 409


def test_invalid_source_url_rejected(wclient) -> None:
    csrf = csrf_for(wclient, "/imports/new")
    r = post_form(
        wclient,
        "/imports/new",
        {"title": "T", "description": "d", "source_url": "javascript:alert(1)"},
        csrf=csrf,
    )
    assert r.status_code == 400
    assert "http" in r.text


# ---------------------------------------------------------------- Abruf (API)

def _settings(**overrides) -> Settings:
    base: dict[str, Any] = {
        "data_dir": Path("/tmp"),
        "session_secret": "x" * 40,
        "admin_password_hash": (
            "$argon2id$v=19$m=65536,t=1,p=1$ZmFrZXNhbHQ$"
            "0000000000000000000000000000000000000000000"
        ),
        "wecatalog_enabled": True,
        "wecatalog_api_url": "https://api.example.org/v1/catalog",
        "wecatalog_timeout_seconds": 5,
        "wecatalog_max_fetches_per_hour": 6,
    }
    base.update(overrides)
    return Settings(**base)


def test_fetch_requires_enabled_flag() -> None:
    settings = _settings(wecatalog_enabled=False)
    with pytest.raises(WecatalogDisabledError):
        fetch_catalog_entry(settings, "https://api.example.org/v1/catalog/1")


def test_fetch_requires_api_url_configured() -> None:
    settings = _settings(wecatalog_api_url="")
    with pytest.raises(WecatalogFetchError) as exc:
        fetch_catalog_entry(settings, "https://api.example.org/v1/catalog/1")
    assert "WECATALOG_API_URL" in str(exc.value)


def test_fetch_rejects_foreign_url() -> None:
    settings = _settings()
    with pytest.raises(WecatalogFetchError) as exc:
        fetch_catalog_entry(settings, "https://evil.example.net/steal")
    assert "außerhalb" in str(exc.value)


def test_fetch_success_is_price_free() -> None:
    settings = _settings()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "title": "Importiertes Modell",
                "description": "Katalogtext",
                "price": "4999",
                "specs": {"Kaliber": "321"},
            },
        )

    payload = fetch_catalog_entry(
        settings,
        "https://api.example.org/v1/catalog/7",
        transport=httpx.MockTransport(handler),
    )
    assert payload["title"] == "Importiertes Modell"
    assert "price" not in payload
    assert not payload_has_prices(payload)


def test_fetch_unreachable_source_fails_safely() -> None:
    settings = _settings()

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(WecatalogFetchError) as exc:
        fetch_catalog_entry(
            settings,
            "https://api.example.org/v1/catalog/7",
            transport=httpx.MockTransport(boom),
        )
    assert "nicht erreichbar" in str(exc.value)


def test_fetch_non_json_fails_safely() -> None:
    settings = _settings()

    def html(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>Login bitte</html>")

    with pytest.raises(WecatalogFetchError) as exc:
        fetch_catalog_entry(
            settings,
            "https://api.example.org/v1/catalog/7",
            transport=httpx.MockTransport(html),
        )
    assert "JSON" in str(exc.value)


def test_fetch_rate_limit_enforced() -> None:
    settings = _settings(wecatalog_max_fetches_per_hour=2)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"title": "x"})

    transport = httpx.MockTransport(handler)
    fetch_catalog_entry(settings, "https://api.example.org/v1/catalog/1", transport=transport)
    fetch_catalog_entry(settings, "https://api.example.org/v1/catalog/1", transport=transport)
    with pytest.raises(WecatalogFetchError) as exc:
        fetch_catalog_entry(
            settings, "https://api.example.org/v1/catalog/1", transport=transport
        )
    assert "Abruflimit" in str(exc.value)


def test_route_fetch_error_renders_readable_message(wclient, monkeypatch) -> None:
    def fail(settings, url, **kwargs):  # type: ignore[no-untyped-def]
        raise WecatalogFetchError("Quelle nicht erreichbar: ConnectError – später erneut.")

    monkeypatch.setattr(imports_module, "fetch_catalog_entry", fail)
    csrf = csrf_for(wclient, "/imports/new")
    r = post_form(
        wclient,
        "/imports/new",
        {
            "fetch_url": "https://api.example.org/v1/catalog/9",
            "title": "",
            "description": "",
        },
        csrf=csrf,
    )
    assert r.status_code == 400
    assert "nicht erreichbar" in r.text
