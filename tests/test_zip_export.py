"""ZIP-Export: Inhalte, Dateinamen, Auswahl, Schutz vor ZIP-Slip und Datenlecks."""
from __future__ import annotations

import io
import zipfile

from tests.conftest import create_watch, csrf_for, make_image, post_form


def _upload(client, watch_id, files):
    csrf = csrf_for(client, f"/watches/{watch_id}")
    return post_form(
        client,
        f"/watches/{watch_id}/images",
        {},
        csrf=csrf,
        files=files,
        follow_redirects=False,
    )


def _zip_content(client, watch_id, query="") -> tuple[list[str], bytes]:
    r = client.get(f"/watches/{watch_id}/listing/zip?platform=reddit{query}")
    assert r.status_code == 200, r.text[:300]
    assert r.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        return zf.namelist(), r.content


def _read(content: bytes, member: str) -> str:
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        return zf.read(member).decode("utf-8")


def test_zip_contains_text_and_ordered_images(auth_client) -> None:
    watch_id = create_watch(auth_client)
    _upload(
        auth_client,
        watch_id,
        {
            "files": [
                ("first.jpg", make_image(color=(1, 2, 3)), "image/jpeg"),
                ("second.jpg", make_image(color=(4, 5, 6)), "image/jpeg"),
            ]
        },
    )
    names, content = _zip_content(auth_client, watch_id)
    assert names[0] == "anzeige-reddit.txt"
    assert "01-first.jpg" in names
    assert "02-second.jpg" in names
    text = _read(content, "anzeige-reddit.txt")
    assert "Omega" in text
    for secret in ("1.234,56", "STRENG-INTERN-Notiz XYZ", "Versand+Versicherung"):
        assert secret not in text


def test_zip_selection_by_ids(auth_client) -> None:
    watch_id = create_watch(auth_client)
    _upload(
        auth_client,
        watch_id,
        {
            "files": [
                ("a.jpg", make_image(), "image/jpeg"),
                ("b.jpg", make_image(color=(9, 9, 9)), "image/jpeg"),
            ]
        },
    )
    page = auth_client.get(f"/watches/{watch_id}")
    import re

    ids = re.findall(r"/images/(\d+)/adjust", page.text)
    second_id = ids[1]
    names, _ = _zip_content(auth_client, watch_id, query=f"&image_ids={second_id}")
    assert "anzeige-reddit.txt" in names
    assert "01-b.jpg" in names
    assert not any(n.startswith("01-a") for n in names)


def test_zip_rejects_foreign_image_ids(auth_client) -> None:
    watch_a = create_watch(auth_client)
    watch_b = create_watch(auth_client, title="Zweite Uhr")
    _upload(auth_client, watch_b, {"files": ("x.jpg", make_image(), "image/jpeg")})
    r = auth_client.get(
        f"/watches/{watch_a}/listing/zip?platform=reddit&image_ids=999999"
    )
    assert r.status_code == 400


def test_zip_rejects_image_of_other_watch(auth_client) -> None:
    watch_a = create_watch(auth_client, title="Uhr A")
    watch_b = create_watch(auth_client, title="Uhr B")
    _upload(auth_client, watch_b, {"files": ("only-b.jpg", make_image(), "image/jpeg")})
    page = auth_client.get(f"/watches/{watch_b}")
    import re

    rx = re.search(r"/images/(\d+)/adjust", page.text)
    assert rx, "Bild-ID nicht gefunden"
    foreign_id = rx.group(1)
    r = auth_client.get(
        f"/watches/{watch_a}/listing/zip?platform=reddit&image_ids={foreign_id}"
    )
    assert r.status_code == 400


def test_zip_slip_protection_via_label(auth_client) -> None:
    """Ein bösartiger Bildname/Label erzeugt keine Pfad-Traversal-Namen im Archiv."""
    watch_id = create_watch(auth_client)
    _upload(auth_client, watch_id, {"files": ("photo.jpg", make_image(), "image/jpeg")})
    page = auth_client.get(f"/watches/{watch_id}")
    import re

    rx = re.search(r"/images/(\d+)/label", page.text)
    assert rx, "Bild-ID nicht gefunden"
    image_id = rx.group(1)
    csrf = csrf_for(auth_client, f"/watches/{watch_id}")
    post_form(
        auth_client,
        f"/images/{image_id}/label",
        {"label": "../../evil/../..\\hack"},
        csrf=csrf,
        follow_redirects=False,
    )
    names, _ = _zip_content(auth_client, watch_id)
    for name in names:
        assert ".." not in name, name
        assert not name.startswith("/")
        assert "\\" not in name
        # Nur Basisname, kein Verzeichnisanteil:
        assert "/" not in name.replace("anzeige-", "")


def test_zip_export_never_contains_purchase_data_in_images(auth_client) -> None:
    """Vertrauliche Daten tauchen weder im Text noch in Dateinamen des ZIPs auf."""
    watch_id = create_watch(
        auth_client, sku="U-9999", internal_notes="PW-intern-XYZ-123"
    )
    _upload(auth_client, watch_id, {"files": ("front.jpg", make_image(), "image/jpeg")})
    names, content = _zip_content(auth_client, watch_id)
    joined = " ".join(names)
    assert "PW-intern-XYZ-123" not in joined
    assert "1.234,56" not in joined
    text = _read(content, "anzeige-reddit.txt")
    assert "PW-intern-XYZ-123" not in text


def test_zip_without_images_has_only_text_or_fails_cleanly(auth_client) -> None:
    watch_id = create_watch(auth_client)
    r = auth_client.get(f"/watches/{watch_id}/listing/zip?platform=reddit")
    assert r.status_code in {400, 200}
    if r.status_code == 200:
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            assert zf.namelist() == ["anzeige-reddit.txt"]
