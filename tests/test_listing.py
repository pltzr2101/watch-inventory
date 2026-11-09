"""Anzeigengenerator: Vorlagen, Vertraulichkeit, Freigabe, Exporte."""
from __future__ import annotations

from tests.conftest import create_watch, csrf_for, post_form

CONFIDENTIAL = ["1.234,56", "STRENG-INTERN-Notiz XYZ", "Versand+Versicherung", "45,10"]


def _assert_confidential_free(text: str, *, context: str) -> None:
    for secret in CONFIDENTIAL:
        assert secret not in text, f"Vertrauliche Daten '{secret}' in {context}"


def test_reddit_and_facebook_generated_from_saved_data(auth_client) -> None:
    watch_id = create_watch(auth_client)

    reddit = auth_client.get(f"/watches/{watch_id}/listing?platform=reddit")
    assert reddit.status_code == 200
    body = reddit.text
    assert "Omega" in body and "Speedmaster Professional" in body
    assert "Sehr gut" in body
    assert "Versicherter Versand DE" in body
    assert "2.100,00" in body  # Verkaufspreis darf rein
    assert "3861" in body
    _assert_confidential_free(body, context="Reddit-Seite")

    facebook = auth_client.get(f"/watches/{watch_id}/listing?platform=facebook")
    assert facebook.status_code == 200
    fb_body = facebook.text
    assert "Omega" in fb_body
    assert "reddit" in fb_body.lower() or "Facebook" in fb_body
    _assert_confidential_free(fb_body, context="Facebook-Seite")


def test_missing_fields_are_omitted_not_guessed(auth_client) -> None:
    watch_id = create_watch(
        auth_client,
        title="Nur Titel",
        condition="",
        condition_description="",
        included_accessories="",
        shipping_info="",
        description="",
        specs_text="",
        purchase_price="",
        target_price="",
        brand="",
        model="",
    )
    r = auth_client.get(f"/watches/{watch_id}/listing?platform=facebook")
    assert r.status_code == 200
    assert "Lieferumfang:" not in r.text
    assert "Preis:" not in r.text
    assert "Zustand:" not in r.text
    # Statische Betreiber-Stammdaten (Standort/Zahlung/Versand) sind
    # Template-Vorgabe und keine geratenen Daten – aber KEIN Feld darf mit
    # "None" oder anderen Platzhalter-Resten aufgefüllt werden:
    assert "None" not in r.text
    # Hinweis auf fehlende Pflichtangabe statt ratender Werte:
    assert "Nicht gepflegt" in r.text


def test_draft_save_edit_regenerate(auth_client) -> None:
    watch_id = create_watch(auth_client)
    csrf = csrf_for(auth_client, f"/watches/{watch_id}/listing")
    r = post_form(
        auth_client,
        f"/watches/{watch_id}/listing/save",
        {"platform": "reddit", "title": "Mein eigener Titel", "body": "Mein Text."},
        csrf=csrf,
        follow_redirects=False,
    )
    assert r.status_code == 303
    page = auth_client.get(f"/watches/{watch_id}/listing?platform=reddit")
    assert "Mein eigener Titel" in page.text
    assert "gespeicherter Entwurf" in page.text

    # Neu erzeugen verwirft den Entwurf:
    r = post_form(
        auth_client,
        f"/watches/{watch_id}/listing/regenerate",
        {"platform": "reddit"},
        csrf=csrf,
        follow_redirects=False,
    )
    assert r.status_code == 303
    page = auth_client.get(f"/watches/{watch_id}/listing?platform=reddit")
    assert "Mein eigener Titel" not in page.text
    assert "frisch erzeugt" in page.text


def test_export_txt_contains_title_and_text(auth_client) -> None:
    watch_id = create_watch(auth_client)
    r = auth_client.get(f"/watches/{watch_id}/listing/export?platform=reddit&format=txt")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert "anzeige-" in r.headers["content-disposition"]
    text = r.text
    assert "Omega" in text
    _assert_confidential_free(text, context="TXT-Export")

    r = auth_client.get(f"/watches/{watch_id}/listing/export?platform=reddit&format=md")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")

    r = auth_client.get(f"/watches/{watch_id}/listing/export?platform=reddit&format=exe")
    assert r.status_code == 400


def test_unapproved_catalog_title_blocks_generation(auth_client, env, monkeypatch) -> None:
    """Importierter Titel wird erst nach ausdrücklicher Freigabe für Anzeigen genutzt."""
    monkeypatch.setenv("WECATALOG_ENABLED", "1")
    from fastapi.testclient import TestClient

    from app.main import create_app

    client = TestClient(create_app(), base_url="http://testserver")
    from tests.conftest import login

    login(client)
    # Importvorschlag mit Katalog-Titel:
    csrf = csrf_for(client, "/imports/new")
    r = post_form(
        client,
        "/imports/new",
        {
            "title": "Katalog-Titel Rolex Daytona",
            "description": "Autorisierter Katalogtext.",
            "source_url": "https://www.wecatalog.cn/item/1",
        },
        csrf=csrf,
        follow_redirects=False,
    )
    assert r.status_code == 303
    preview_id = r.headers["location"].rsplit("/", 1)[-1]
    r = post_form(
        client,
        f"/imports/{preview_id}/confirm",
        {
            "title": "Katalog-Titel Rolex Daytona",
            "description": "Autorisierter Katalogtext.",
            "specs_text": "",
            "status": "in_china",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    watch_id = r.headers["location"].rsplit("/", 1)[-1]

    # Noch NICHT freigegeben → Generator verweigert den Katalog-Titel:
    page = client.get(f"/watches/{watch_id}/listing?platform=reddit")
    assert page.status_code == 400
    assert "Katalog-Titel Rolex Daytona" not in page.text
    assert "freigegeben" in page.text

    # Erst nach ausdrücklicher Freigabe:
    csrf = csrf_for(client, f"/watches/{watch_id}")
    post_form(
        client,
        f"/watches/{watch_id}/import-approve",
        {"approve": "1"},
        csrf=csrf,
        follow_redirects=False,
    )
    page = client.get(f"/watches/{watch_id}/listing?platform=reddit")
    assert page.status_code == 200
    assert "Katalog-Titel Rolex Daytona" in page.text


def test_platform_hints_are_configurable(auth_client, tmp_path) -> None:
    watch_id = create_watch(auth_client)
    r = auth_client.get(f"/watches/{watch_id}/listing?platform=reddit")
    assert "Subreddit" in r.text  # aus hints.json


def test_platform_templates_and_hints_configurable(auth_client, env, monkeypatch) -> None:
    """Vorlagen + Hinweise sind austauschbar (LISTING_TEMPLATE_DIR), ohne Codeänderung."""
    import json
    from pathlib import Path

    custom_dir = Path(env) / "custom-templates"
    custom_dir.mkdir()
    (custom_dir / "reddit.txt.j2").write_text("CUSTOM-TEMPLATE {{ title }}", encoding="utf-8")
    (custom_dir / "hints.json").write_text(
        json.dumps({"reddit": "Eigene Reddit-Regel.", "facebook": "fb"}),
        encoding="utf-8",
    )

    monkeypatch.setenv("LISTING_TEMPLATE_DIR", str(custom_dir))
    from fastapi.testclient import TestClient

    from app.main import create_app

    client = TestClient(create_app(), base_url="http://testserver")
    from tests.conftest import login

    login(client)
    watch_id = create_watch(client)
    r = client.get(f"/watches/{watch_id}/listing?platform=reddit")
    assert r.status_code == 200
    assert "CUSTOM-TEMPLATE" in r.text
    assert "Eigene Reddit-Regel." in r.text
    # Andere Plattform greift weiterhin auf den mitgelieferten Fallback zurück:
    r = client.get(f"/watches/{watch_id}/listing?platform=facebook")
    assert r.status_code == 200
    assert "fb" in r.text  # eigene hints.json enthält auch facebook


def test_reddit_code_and_telegram_templates_render(auth_client) -> None:
    watch_id = create_watch(
        auth_client,
        factory="Clean Factory",
        spec_on_kaliber="on",
        spec_kaliber="3861",
    )

    rc = auth_client.get(f"/watches/{watch_id}/listing?platform=reddit_code")
    assert rc.status_code == 200
    # Werks-Code im Titel + Notes-Feld inkl. Werks-Code (Plattform-Vorgabe):
    assert "Clean Factory" in rc.text
    assert "Seller Code: 1949" in rc.text
    assert "Werks-Code Clean Factory" in rc.text
    _assert_confidential_free(rc.text, context="Reddit-Code")

    tg = auth_client.get(f"/watches/{watch_id}/listing?platform=telegram")
    assert tg.status_code == 200
    assert "For Sale:" in tg.text
    assert "Factory: Clean Factory" in tg.text
    _assert_confidential_free(tg.text, context="Telegram")


def test_box_and_factory_appear_in_listing(auth_client) -> None:
    watch_id = create_watch(
        auth_client,
        factory="Clean Factory",
        box="box",
    )
    r = auth_client.get(f"/watches/{watch_id}/listing?platform=reddit")
    assert r.status_code == 200
    assert " - Clean Factory - " in r.text  # Werks-Code im Titelblock
    assert "Box included" in r.text  # box-Option, nicht geraten
    assert "Box optional" not in r.text  # ausschließlich die gewählte Option


def test_publication_status_tracking(auth_client) -> None:
    watch_id = create_watch(auth_client)
    listing_url = f"/watches/{watch_id}/listing?platform=reddit"
    page = auth_client.get(listing_url)
    assert "Entwurf" in page.text  # Default-Status

    csrf = csrf_for(auth_client, listing_url)
    r = post_form(
        auth_client,
        f"/watches/{watch_id}/listing/publication",
        {"platform": "reddit", "status": "ready"},
        csrf=csrf,
        follow_redirects=False,
    )
    assert r.status_code == 303
    page = auth_client.get(listing_url)
    assert "Bereit" in page.text

    # "Gepostet" ohne URL ist zulässig (Tracking manuell), mit URL-valid:
    r = post_form(
        auth_client,
        f"/watches/{watch_id}/listing/publication",
        {"platform": "reddit", "status": "posted"},
        csrf=csrf,
        follow_redirects=False,
    )
    assert r.status_code == 303
    page = auth_client.get(listing_url)
    assert "Gepostet" in page.text

    r = post_form(
        auth_client,
        f"/watches/{watch_id}/listing/publication",
        {"platform": "reddit", "status": "posted", "url": "keine-URL"},
        csrf=csrf,
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert "http" in r.text

    r = post_form(
        auth_client,
        f"/watches/{watch_id}/listing/publication",
        {
            "platform": "reddit",
            "status": "posted",
            "url": "https://www.reddit.com/r/Test/comments/x/",
        },
        csrf=csrf,
        follow_redirects=False,
    )
    assert r.status_code == 303
    page = auth_client.get(listing_url)
    assert "https://www.reddit.com/r/Test/comments/x/" in page.text
