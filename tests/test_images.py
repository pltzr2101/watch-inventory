"""Upload-Validierung, Korrekturen (nicht-destruktiv), Reihenfolge, Löschung."""
from __future__ import annotations

from pathlib import Path

from tests.conftest import create_watch, csrf_for, make_image, png_with_dims, post_form


def _upload(client, watch_id, files, csrf=None):
    csrf = csrf or csrf_for(client, f"/watches/{watch_id}")
    return post_form(
        client,
        f"/watches/{watch_id}/images",
        {},
        csrf=csrf,
        files=files,
        follow_redirects=False,
    )


def test_upload_multiple_images_and_sort(auth_client, env) -> None:
    watch_id = create_watch(auth_client)
    r = _upload(
        auth_client,
        watch_id,
        {
            "files": [
                ("front.jpg", make_image(color=(200, 0, 0)), "image/jpeg"),
                ("dial.jpg", make_image(color=(0, 200, 0)), "image/jpeg"),
            ]
        },
    )
    assert r.status_code == 303
    page = auth_client.get(f"/watches/{watch_id}")
    assert "front.jpg" in page.text and "dial.jpg" in page.text

    # Original unverändert auf Disk, korrekte Speicherung unter DATA_DIR/images:
    images_dir = Path(env) / "images"
    assert images_dir.is_dir()
    originals = list(images_dir.iterdir())
    assert len(originals) == 2

    # Reihenfolge tauschen (zweites Bild nach oben):
    import re

    ids = [int(m) for m in re.findall(r"/images/(\d+)/adjust", page.text)]
    assert len(ids) == 2
    first_id, second_id = ids[0], ids[1]
    csrf = csrf_for(auth_client, f"/watches/{watch_id}")
    r = post_form(
        auth_client,
        f"/images/{second_id}/move",
        {"direction": "up"},
        csrf=csrf,
        follow_redirects=False,
    )
    assert r.status_code == 303
    page = auth_client.get(f"/watches/{watch_id}")
    new_ids = [int(m) for m in re.findall(r"/images/(\d+)/adjust", page.text)]
    assert new_ids == [second_id, first_id]


def test_reject_non_image_and_bad_extension(auth_client, env) -> None:
    watch_id = create_watch(auth_client)
    r = _upload(
        auth_client,
        watch_id,
        {"files": ("evil.jpg", b"<html>not an image</html>", "image/jpeg")},
    )
    assert r.status_code == 303  # Umleitung mit Fehler-Flash
    page = auth_client.get(f"/watches/{watch_id}")
    assert "Foto" not in page.text or "Noch keine Fotos" in page.text
    assert list((Path(env) / "images").glob("*")) == []


def test_reject_oversize_upload(auth_client, env) -> None:
    watch_id = create_watch(auth_client)
    big = b"\xff\xd8\xff\xe0" + b"\x00" * (1100 * 1024)  # > MAX_UPLOAD_MB=1
    r = _upload(auth_client, watch_id, {"files": ("big.jpg", big, "image/jpeg")})
    assert r.status_code == 303
    assert list((Path(env) / "images").glob("*")) == []
    page = auth_client.get(f"/watches/{watch_id}")
    assert "Datei zu groß" in page.text or "Noch keine Fotos" in page.text


def test_reject_pixel_bomb(auth_client, env) -> None:
    watch_id = create_watch(auth_client)
    bomb = png_with_dims(7000, 7000)  # 49 Mio. Pixel > Standardlimit 40 Mio.
    r = _upload(auth_client, watch_id, {"files": ("bomb.png", bomb, "image/png")})
    assert r.status_code == 303
    assert list((Path(env) / "images").glob("*")) == []
    page = auth_client.get(f"/watches/{watch_id}")
    assert "Pixel" in page.text or "Noch keine Fotos" in page.text


def test_adjustments_never_touch_original(auth_client, env) -> None:
    watch_id = create_watch(auth_client)
    _upload(auth_client, watch_id, {"files": ("a.jpg", make_image(), "image/jpeg")})
    page = auth_client.get(f"/watches/{watch_id}")
    import re

    rx = re.search(r"/images/(\d+)/adjust", page.text)
    assert rx, "Bild-ID nicht gefunden"
    image_id = int(rx.group(1))

    images_dir = Path(env) / "images"
    stored_before = {p.name: p.read_bytes() for p in images_dir.iterdir()}
    assert len(stored_before) == 1
    original_bytes = next(iter(stored_before.values()))

    csrf = csrf_for(auth_client, f"/images/{image_id}/adjust")
    r = post_form(
        auth_client,
        f"/images/{image_id}/adjust",
        {"brightness": "1.4", "contrast": "0.8", "rotation": "90"},
        csrf=csrf,
        follow_redirects=False,
    )
    assert r.status_code == 303

    stored_after = {p.name: p.read_bytes() for p in images_dir.iterdir()}
    assert stored_after == stored_before  # Original byte-identisch

    original = auth_client.get(f"/images/{image_id}/file")
    adjusted = auth_client.get(f"/images/{image_id}/file?adjusted=1")
    assert original.status_code == 200 and adjusted.status_code == 200
    assert original.content != adjusted.content
    assert original.content == original_bytes

    # Zurücksetzen:
    csrf = csrf_for(auth_client, f"/images/{image_id}/adjust")
    post_form(
        auth_client, f"/images/{image_id}/reset", {}, csrf=csrf, follow_redirects=False
    )
    reset_view = auth_client.get(f"/images/{image_id}/file?adjusted=1")
    assert reset_view.content == original_bytes


def test_preview_params_sanitized(auth_client, env) -> None:
    watch_id = create_watch(auth_client)
    _upload(auth_client, watch_id, {"files": ("a.jpg", make_image(), "image/jpeg")})
    page = auth_client.get(f"/watches/{watch_id}")
    import re

    rx = re.search(r"/images/(\d+)/adjust", page.text)
    assert rx, "Bild-ID nicht gefunden"
    image_id = int(rx.group(1))
    # Extremwerte werden geklemmt, nicht durchgereicht:
    r = auth_client.get(
        f"/images/{image_id}/file?adjusted=preview&brightness=99&contrast=-3&rotation=77&crop=bad"
    )
    assert r.status_code == 200


def test_delete_image_removes_file(auth_client, env) -> None:
    watch_id = create_watch(auth_client)
    _upload(auth_client, watch_id, {"files": ("gone.jpg", make_image(), "image/jpeg")})
    images_dir = Path(env) / "images"
    stored = list(images_dir.iterdir())
    assert len(stored) == 1

    page = auth_client.get(f"/watches/{watch_id}")
    import re

    rx = re.search(r"/images/(\d+)/delete", page.text)
    assert rx, "Bild-ID nicht gefunden"
    image_id = int(rx.group(1))
    csrf = csrf_for(auth_client, f"/watches/{watch_id}")
    r = post_form(
        auth_client, f"/images/{image_id}/delete", {}, csrf=csrf, follow_redirects=False
    )
    assert r.status_code == 303
    assert list(images_dir.iterdir()) == []
    assert auth_client.get(f"/images/{image_id}/file").status_code == 404


def test_label_used_for_export_name(auth_client) -> None:
    watch_id = create_watch(auth_client)
    _upload(auth_client, watch_id, {"files": ("photo.jpg", make_image(), "image/jpeg")})
    page = auth_client.get(f"/watches/{watch_id}")
    import re

    rx = re.search(r"/images/(\d+)/label", page.text)
    assert rx, "Bild-ID nicht gefunden"
    image_id = int(rx.group(1))
    csrf = csrf_for(auth_client, f"/watches/{watch_id}")
    post_form(
        auth_client,
        f"/images/{image_id}/label",
        {"label": "front"},
        csrf=csrf,
        follow_redirects=False,
    )
    r = auth_client.get(f"/watches/{watch_id}/listing/zip?platform=reddit")
    assert r.status_code == 200
    import io
    import zipfile

    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert "01-front.jpg" in zf.namelist()
        assert "anzeige-reddit.txt" in zf.namelist()
