"""Vorlagenverwaltung: Whitelist, Validierung, Override-Roundtrip, CSRF/Auth."""
from __future__ import annotations

import json

from tests.conftest import csrf_for, post_form
from tests.test_listing import _assert_confidential_free


def test_templates_list_and_edit(auth_client) -> None:
    listing = auth_client.get("/templates")
    assert listing.status_code == 200
    assert "reddit.txt.j2" in listing.text
    assert "hints.json" in listing.text

    edit = auth_client.get("/templates/edit?name=reddit.txt.j2")
    assert edit.status_code == 200
    assert "For Sale:" in edit.text  # Built-in-Inhalt im Editor


def test_override_roundtrip_changes_listing(auth_client) -> None:
    watch_create = post_form(
        auth_client,
        "/watches/new",
        {"title": "Override-Watch", "status": "in_germany"},
        csrf=csrf_for(auth_client, "/watches/new"),
        follow_redirects=False,
    )
    assert watch_create.status_code == 303

    csrf = csrf_for(auth_client, "/templates/edit?name=reddit.txt.j2")
    r = post_form(
        auth_client,
        "/templates/save",
        {"name": "reddit.txt.j2", "content": "OVERRIDE-VORLAGE fuer {{ title }}"},
        csrf=csrf,
        follow_redirects=False,
    )
    assert r.status_code == 303

    edit = auth_client.get("/templates/edit?name=reddit.txt.j2")
    assert "OVERRIDE-VORLAGE" in edit.text

    # Der Generator nutzt den Override (gleicher Settings-Ordner wie der
    # GenerateService) – und verrät dabei keine vertraulichen Daten:
    watch_id = watch_create.headers["location"].rsplit("/", 1)[-1]
    page = auth_client.get(f"/watches/{watch_id}/listing?platform=reddit")
    assert page.status_code == 200
    assert "OVERRIDE-VORLAGE fuer Override-Watch" in page.text
    _assert_confidential_free(page.text, context="Override-Listing")

    # Löschen stellt die mitgelieferte Vorlage wieder her:
    r = post_form(
        auth_client,
        "/templates/delete",
        {"name": "reddit.txt.j2"},
        csrf=csrf_for(auth_client, "/templates/edit?name=reddit.txt.j2"),
        follow_redirects=False,
    )
    assert r.status_code == 303
    page = auth_client.get(f"/watches/{watch_id}/listing?platform=reddit")
    assert "For Sale:" in page.text


def test_invalid_templates_are_rejected(auth_client) -> None:
    csrf = csrf_for(auth_client, "/templates/edit?name=reddit.txt.j2")
    # Defektes Jinja-Syntax:
    r = post_form(
        auth_client,
        "/templates/save",
        {"name": "reddit.txt.j2", "content": "{{ title"},
        csrf=csrf,
        follow_redirects=False,
    )
    assert r.status_code == 400
    # Unbekannte Variable (StrictUndefined) – fängt Tippfehler beim Editieren:
    r = post_form(
        auth_client,
        "/templates/save",
        {"name": "reddit.txt.j2", "content": "{{ totally_unknown_key }}"},
        csrf=csrf,
        follow_redirects=False,
    )
    assert r.status_code == 400
    # Ungültige JSON-Datei:
    r = post_form(
        auth_client,
        "/templates/save",
        {"name": "hints.json", "content": "kein json"},
        csrf=csrf,
        follow_redirects=False,
    )
    assert r.status_code == 400


def test_hints_json_roundtrip(auth_client) -> None:
    csrf = csrf_for(auth_client, "/templates/edit?name=hints.json")
    payload = json.dumps({"reddit": "Mein Hinweis reddit."}, ensure_ascii=False)
    r = post_form(
        auth_client,
        "/templates/save",
        {"name": "hints.json", "content": payload},
        csrf=csrf,
        follow_redirects=False,
    )
    assert r.status_code == 303
    edit = auth_client.get("/templates/edit?name=hints.json")
    assert "Mein Hinweis reddit." in edit.text


def test_preview_renders_sample_context(auth_client) -> None:
    csrf = csrf_for(auth_client, "/templates/edit?name=reddit.txt.j2")
    r = post_form(
        auth_client,
        "/templates/preview",
        {"name": "reddit.txt.j2", "content": "VORSCHAU: {{ title }}"},
        csrf=csrf,
        follow_redirects=False,
    )
    assert r.status_code == 200
    assert "VORSCHAU:" in r.text
    assert "preview-output" in r.text


def test_template_name_whitelist_blocks_traversal(auth_client) -> None:
    csrf = csrf_for(auth_client, "/templates/edit?name=reddit.txt.j2")
    r = post_form(
        auth_client,
        "/templates/save",
        {"name": "../../../etc/passwd", "content": "x"},
        csrf=csrf,
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert "Vorlagenname" in r.text


def test_templates_require_csrf(auth_client) -> None:
    # Eingeloggt, aber OHNE CSRF-Token: strikt verboten (403).
    # (Unauth-Zugriff ist in test_dump/test_auth bereits abgedeckt.)
    r = auth_client.post("/templates/save", data={"name": "reddit.txt.j2", "content": "x"})
    assert r.status_code == 403
