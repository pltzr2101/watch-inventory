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
    bomb = png_with_dims(12000, 12000)  # 144 Mio. Pixel > Standardlimit 80 Mio.
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


def test_image_classify_orders_and_fills_labels() -> None:
    from types import SimpleNamespace

    from app.services import image_classify

    images = [
        SimpleNamespace(original_name="box.jpg", label="", sort_order=1),
        SimpleNamespace(original_name="dial-macro.jpg", label="", sort_order=0),
        SimpleNamespace(original_name="unbekannt.jpg", label="custom", sort_order=2),
    ]
    image_classify.auto_order(images)
    # auto_order setzt sort_order (die ORM-Query sortiert danach neu):
    by_order = sorted(images, key=lambda i: i.sort_order)
    assert [i.original_name for i in by_order] == ["dial-macro.jpg", "box.jpg", "unbekannt.jpg"]
    assert [i.sort_order for i in by_order] == [0, 1, 2]
    # Nur leere Labels werden gefüllt, manuelle Eingaben bleiben:
    assert by_order[0].label == "dial"
    assert by_order[1].label == "box"
    assert by_order[2].label == "custom"


def test_auto_order_route_resorts_upload(auth_client) -> None:
    watch_id = create_watch(auth_client)
    _upload(
        auth_client,
        watch_id,
        {
            "files": [
                ("box.jpg", make_image(color=(1, 1, 1)), "image/jpeg"),
                ("dial.jpg", make_image(color=(2, 2, 2)), "image/jpeg"),
            ]
        },
    )
    csrf = csrf_for(auth_client, f"/watches/{watch_id}")
    r = post_form(
        auth_client,
        f"/watches/{watch_id}/images/auto-order",
        {},
        csrf=csrf,
        follow_redirects=False,
    )
    assert r.status_code == 303
    # Reihenfolge für den Export: Dial vor Box:
    zip_resp = auth_client.get(f"/watches/{watch_id}/listing/zip?platform=reddit")
    assert zip_resp.status_code == 200
    import io
    import zipfile

    with zipfile.ZipFile(io.BytesIO(zip_resp.content)) as zf:
        assert "01-dial.jpg" in zf.namelist()
        assert "02-box.jpg" in zf.namelist()


def test_watermark_only_on_export_copies(auth_client, env) -> None:
    import io
    import zipfile
    from pathlib import Path

    watch_id = create_watch(auth_client)
    original_bytes = make_image(color=(3, 3, 3))
    _upload(auth_client, watch_id, {"files": ("dial.jpg", original_bytes, "image/jpeg")})

    # Originaldatei unverändert (nicht überschrieben, byte-identisch):
    images_dir = Path(env) / "images"
    stored = list(images_dir.iterdir())
    assert len(stored) == 1
    assert stored[0].read_bytes() == original_bytes

    plain = auth_client.get(
        f"/watches/{watch_id}/listing/zip?platform=reddit&watermark=0"
    )
    marked = auth_client.get(
        f"/watches/{watch_id}/listing/zip?platform=reddit"
        "&watermark=1&watermark_text=TESTMARKER"
    )
    assert plain.status_code == 200 and marked.status_code == 200

    def _image_bytes(resp) -> bytes:
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            name = [n for n in zf.namelist() if n.endswith(".jpg")][0]
            return zf.read(name)

    assert _image_bytes(plain) != _image_bytes(marked)  # Wasserzeichen wirkt
    # ... und das gespeicherte Original ist nach beiden Exporten unangetastet:
    assert stored[0].read_bytes() == original_bytes


def _heic_bytes(width: int = 64, height: int = 64, color=(70, 80, 90)) -> bytes:
    """iPhone-nahes HEIC (Encodierung über pillow-heif, Opener aus image_ops)."""
    import io

    import pillow_heif
    from PIL import Image

    from app.services import image_ops  # noqa: F401  (registriert den HEIC-Opener)

    buf = io.BytesIO()
    pillow_heif.from_pillow(Image.new("RGB", (width, height), color)).save(buf)
    return buf.getvalue()


def test_heic_upload_preview_and_delete_cleanup(auth_client, env) -> None:
    watch_id = create_watch(auth_client)
    heic = _heic_bytes()
    r = _upload(auth_client, watch_id, {"files": ("IMG_0001.heic", heic, "image/heic")})
    assert r.status_code == 303, r.text[:400]

    images_dir = Path(env) / "images"
    files = sorted(p.name for p in images_dir.iterdir())
    # pillow-heif meldet das Format als HEIF (Endung .heif/.heic je Quelldatei):
    originals = [n for n in files if n.endswith((".heic", ".heif"))]
    assert len(originals) == 1, files
    previews = [images_dir / n for n in files if n.endswith(".preview.jpg")]
    assert len(previews) == 1, files
    # Original byte-identisch, Preview als JPEG-Derivat daneben:
    assert (images_dir / originals[0]).read_bytes() == heic

    import re

    page = auth_client.get(f"/watches/{watch_id}")
    image_id = re.search(r"/images/(\d+)/adjust", page.text).group(1)

    # /file = unverändertes HEIC-Original, /file?display=1 = JPEG-Ansicht:
    raw = auth_client.get(f"/images/{image_id}/file")
    assert raw.content == heic
    assert raw.headers["content-type"] in {"image/heic", "image/heif"}
    shown = auth_client.get(f"/images/{image_id}/file?display=1")
    assert shown.headers["content-type"] == "image/jpeg"
    assert shown.content != heic

    # Löschen entfernt Original UND Preview:
    csrf = csrf_for(auth_client, f"/watches/{watch_id}")
    post_form(
        auth_client,
        f"/images/{image_id}/delete",
        {},
        csrf=csrf,
        follow_redirects=False,
    )
    assert list(images_dir.iterdir()) == []


def test_heic_zip_export_gets_jpg_name_and_jpeg_content(auth_client) -> None:
    watch_id = create_watch(auth_client)
    _upload(auth_client, watch_id, {"files": ("dial.heic", _heic_bytes(), "image/heic")})
    r = auth_client.get(f"/watches/{watch_id}/listing/zip?platform=reddit")
    assert r.status_code == 200

    import io
    import zipfile

    from PIL import Image

    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        names = zf.namelist()
        assert "01-dial.jpg" in names, names
        assert not any(n.endswith((".heic", ".heif")) for n in names)
        with Image.open(io.BytesIO(zf.read("01-dial.jpg"))) as img:
            assert img.format == "JPEG"  # Endung und Inhalt stimmen überein


def test_default_pixel_limit_fits_48mp_iphone_photos() -> None:
    from app.config import load_settings

    assert load_settings().max_image_pixels >= 48_000_000
