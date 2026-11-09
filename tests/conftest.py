"""Gemeinsame Testfixtures: Env-Setup, App-Factory, Login- und CSRF-Hilfen."""
from __future__ import annotations

import os
import re
import struct
import tempfile
import zlib

import pytest

# Umgebung VOR jedem create_app() setzen (Fail-Fast-Pflichtsecrets für Tests).
os.environ.setdefault(
    "SESSION_SECRET", "test-session-secret-0123456789abcdef0123456789abcdef"
)
os.environ.setdefault("COOKIE_SECURE", "0")
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="wi-tests-")
os.environ.pop("WECATALOG_ENABLED", None)
os.environ.pop("WECATALOG_API_URL", None)
os.environ.pop("WECATALOG_ALLOW_IMAGE_EDIT", None)
os.environ.pop("LISTING_TEMPLATE_DIR", None)

from app.security import hash_password  # noqa: E402  (nach Env-Setup)

TEST_PASSWORD = "test-passw0rd-123"
TEST_USERNAME = os.environ.get("APP_USERNAME", "admin")
os.environ["ADMIN_PASSWORD_HASH"] = hash_password(TEST_PASSWORD)

from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402

CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


def extract_csrf(html: str) -> str:
    match = CSRF_RE.search(html)
    assert match, "csrf_token nicht im HTML gefunden"
    return match.group(1)


@pytest.fixture()
def env(tmp_path, monkeypatch) -> object:
    """Isoliertes DATA_DIR + sichere Testdefaults pro Test."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("COOKIE_SECURE", "0")
    monkeypatch.setenv("LOGIN_MAX_ATTEMPTS", "5")
    monkeypatch.setenv("MAX_UPLOAD_MB", "1")
    monkeypatch.delenv("WECATALOG_ENABLED", raising=False)
    monkeypatch.delenv("WECATALOG_API_URL", raising=False)
    monkeypatch.delenv("WECATALOG_ALLOW_IMAGE_EDIT", raising=False)
    monkeypatch.delenv("LISTING_TEMPLATE_DIR", raising=False)
    return tmp_path


@pytest.fixture()
def app(env):
    return create_app()


@pytest.fixture()
def client(app):
    return TestClient(app, base_url="http://testserver")


def login(client: TestClient) -> None:
    r = client.get("/login")
    assert r.status_code == 200
    token = extract_csrf(r.text)
    r = client.post(
        "/login",
        data={
            "username": TEST_USERNAME,
            "password": TEST_PASSWORD,
            "csrf_token": token,
            "next_to": "/",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303, f"Login fehlgeschlagen: {r.status_code} {r.text[:300]}"


@pytest.fixture()
def auth_client(client):
    login(client)
    return client


def csrf_for(client: TestClient, path: str = "/watches") -> str:
    r = client.get(path)
    assert r.status_code == 200, f"GET {path} -> {r.status_code}"
    return extract_csrf(r.text)


def post_form(
    client: TestClient,
    url: str,
    data: dict | None = None,
    *,
    csrf: str | None = None,
    files: dict | None = None,
    follow_redirects: bool = False,
):
    payload = dict(data or {})
    payload["csrf_token"] = csrf if csrf is not None else extract_csrf(client.get("/watches").text)
    if files:
        # httpx erwartet die kanonische Form [(feld, (name, bytes, typ)), …].
        file_list: list = []
        for field, value in files.items():
            if isinstance(value, list):
                file_list.extend((field, item) for item in value)
            else:
                file_list.append((field, value))
        return client.post(
            url, data=payload, files=file_list, follow_redirects=follow_redirects
        )
    return client.post(url, data=payload, follow_redirects=follow_redirects)


def make_image(width: int = 64, height: int = 64, color=(200, 40, 40), fmt="JPEG") -> bytes:
    """Echtes Bildbytes (Pillow) für Upload-Tests."""
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format=fmt)
    return buffer.getvalue()


def png_with_dims(width: int, height: int) -> bytes:
    """Minimal gültige PNG-Header-Datei mit behaupteten Riesen-Maßen (Decompression-Bomb)."""

    def chunk(ctype: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + ctype
            + data
            + struct.pack(">I", zlib.crc32(ctype + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\x00\x00\x00" * 8)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", idat)
        + chunk(b"IEND", b"")
    )


def cost_category_id(client: TestClient, label: str) -> str:
    """Liest die Kategorie-ID aus dem frischen Anlage-Formular (DB-Startbestand)."""
    r = client.get("/watches/new")
    assert r.status_code == 200, r.status_code
    m = re.search(rf'value="([0-9a-f]{{32}})">{re.escape(label)}', r.text)
    assert m, f"Kategorie nicht im Formular gefunden: {label}"
    return m.group(1)


def create_watch(client: TestClient, **overrides) -> str:
    """Legt eine Uhr an und liefert die Watch-ID (aus der Redirect-Location).

    `description` ist kein Formularfeld mehr (bewusst nur noch
    Wecatalog-Übernahme) und wird deshalb direkt in die DB gesetzt.
    """
    description = overrides.pop("description", None)
    data = {
        "title": "Omega Speedmaster Professional",
        "brand": "Omega",
        "model": "Speedmaster Professional",
        "status": "in_germany",
        "purchase_price": "1.000,00",
        "usd_eur_rate": "1,1000",
        "extra_cost_rows": "2",
        "extra_cost_amount_0": "45,10",
        "extra_cost_currency_0": "EUR",
        "extra_cost_category_0": cost_category_id(client, "Versand DHL (DE)"),
        "extra_cost_amount_1": "18,00",
        "extra_cost_currency_1": "EUR",
        "extra_cost_category_1": cost_category_id(client, "Wise-Gebühren"),
        "target_price": "2.100,00",
        "condition": "sehr_gut",
        "shipping_info": "dhl_de_incl",
        "spec_on_kaliber": "on",
        "spec_kaliber": "3861",
        "spec_on_gehaeusedurchmesser": "on",
        "spec_gehaeusedurchmesser": "42 mm",
        "internal_notes": "STRENG-INTERN-Notiz XYZ",
    }
    data.update(overrides)
    csrf = csrf_for(client, "/watches/new")
    r = post_form(client, "/watches/new", data, csrf=csrf, follow_redirects=False)
    assert r.status_code == 303, f"Anlegen fehlgeschlagen: {r.status_code} {r.text[:500]}"
    location = r.headers["location"]
    match = re.search(r"/watches/([\w-]+)", location)
    assert match, f"Unerwartete Location: {location}"
    watch_id = match.group(1)
    if description is not None:
        from app.db import make_session_factory
        from app.models import Watch

        Session = make_session_factory(client.app.state.engine)
        with Session() as db:
            watch = db.get(Watch, watch_id)
            assert watch is not None
            watch.description = description
            db.commit()
    return watch_id
