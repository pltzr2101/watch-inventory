"""Pump&Dump: Schnellanlage mit Bildern, serverseitige Validierung, Review."""
from __future__ import annotations

from tests.conftest import csrf_for, make_image, post_form


def test_dump_requires_title(auth_client) -> None:
    csrf = csrf_for(auth_client, "/dump")
    r = post_form(
        auth_client,
        "/dump",
        {"title": "", "status": "in_germany"},
        csrf=csrf,
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert "Pflichtfeld" in r.text


def test_dump_rejects_invalid_price(auth_client) -> None:
    csrf = csrf_for(auth_client, "/dump")
    r = post_form(
        auth_client,
        "/dump",
        {"title": "Clean Factory Submariner", "price": "banana"},
        csrf=csrf,
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert "Geldbetrag" in r.text


def test_dump_rejects_wrong_file_type_and_creates_nothing(auth_client) -> None:
    csrf = csrf_for(auth_client, "/dump")
    r = post_form(
        auth_client,
        "/dump",
        {"title": "X-Bad-File-Watch"},
        csrf=csrf,
        files={"files": ("evil.php", b"<?php echo 1;", "text/plain")},
        follow_redirects=False,
    )
    assert r.status_code == 400
    # Der Datensatz darf trotz Formularfehler NICHT angelegt worden sein:
    listing = auth_client.get("/watches")
    assert "X-Bad-File-Watch" not in listing.text


def test_dump_create_with_images_opens_review(auth_client, env) -> None:
    csrf = csrf_for(auth_client, "/dump")
    r = post_form(
        auth_client,
        "/dump",
        {
            "title": "Clean Factory Submariner 126610LN",
            "factory": "Clean Factory",
            "movement": "VS352",
            "condition": "Wie neu",
            "box": "box",
            "price": "1.150,00",
            "status": "in_germany",
            "description": "Leicht getragen, keine Kratzer.",
        },
        csrf=csrf,
        files={
            "files": [
                ("box.jpg", make_image(color=(5, 5, 5)), "image/jpeg"),
                ("dial.jpg", make_image(color=(9, 9, 9)), "image/jpeg"),
            ]
        },
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text[:500]
    location = r.headers["location"]
    assert location.startswith("/dump/")
    assert location.endswith("/review")

    review = auth_client.get(location)
    assert review.status_code == 200
    assert "Clean Factory Submariner 126610LN" in review.text
    assert "Anzeigentext" in review.text
    assert "Reihenfolge automatisch" in review.text
    # Auto-Order hat per Filenamen einsortiert: dial (Rolle) vor box:
    assert "01-dial.jpg" in review.text
    assert "02-box.jpg" in review.text

    # Box-Option wurde gespeichert und wird angezeigt (kein Rate-Raten):
    watch_id = location.removeprefix("/dump/").removesuffix("/review")
    detail = auth_client.get(f"/watches/{watch_id}")
    assert "Box inklusive" in detail.text


def test_dump_requires_auth(client) -> None:
    # Auth-Gate beantwortet unauthentifizierte Zugriffe mit Redirect (Browser)
    # oder 401 (API-Konvention) – niemals mit Inhalt:
    r = client.get("/dump")
    assert r.status_code in (302, 303, 401)
    r = client.get("/dump/x/review")
    assert r.status_code in (302, 303, 401)
    r = client.post("/dump", data={"title": "X"})
    assert r.status_code in (302, 303, 401, 403)
