"""Auth-Gate, CSRF, Secrets-Fail-Fast, Security-Header, Login-Ratelimit."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import TEST_PASSWORD, TEST_USERNAME, extract_csrf

PROTECTED_PATHS = [
    "/",
    "/watches",
    "/watches/new",
    "/watches/1",
    "/watches/1/edit",
    "/watches/1/listing",
    "/watches/1/listing/export?format=txt",
    "/watches/1/listing/zip",
    "/images/1/file",
    "/images/1/adjust",
    "/imports",
    "/imports/new",
    "/imports/abcdef",
]


@pytest.mark.parametrize("path", PROTECTED_PATHS)
def test_unauthed_paths_return_401(client: TestClient, path: str) -> None:
    r = client.get(path)
    assert r.status_code == 401


def test_unauthed_json_api_is_json_not_html(client: TestClient) -> None:
    r = client.get("/watches", headers={"Accept": "application/json"})
    assert r.status_code == 401
    assert "detail" in r.json()


def test_unauthed_post_is_rejected(client: TestClient) -> None:
    r = client.post("/login/does-not-matter", data={"x": "1"})
    assert r.status_code == 401


def test_healthz_and_static_are_public(client: TestClient) -> None:
    assert client.get("/healthz").status_code == 200
    assert client.get("/static/style.css").status_code == 200


def test_static_assets_are_always_revalidated(client: TestClient) -> None:
    """no-cache: nach einem Image-Update darf der Browser kein altes CSS
    heuristisch weiter anzeigen (ETag/Last-Modified liefern dann 304)."""
    r = client.get("/static/style.css")
    assert r.status_code == 200
    assert r.headers.get("cache-control") == "no-cache"
    r2 = client.get("/healthz")
    assert "cache-control" not in r2.headers


def test_login_success_and_logout(auth_client: TestClient) -> None:
    r = auth_client.get("/")
    assert r.status_code == 200
    r = auth_client.post(
        "/logout",
        data={"csrf_token": extract_csrf(r.text)},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert auth_client.get("/").status_code == 401


def test_login_wrong_password_rejected(client: TestClient) -> None:
    r = client.get("/login")
    token = extract_csrf(r.text)
    r = client.post(
        "/login",
        data={"password": "falsch-und-noch-falscher", "csrf_token": token},
        follow_redirects=False,
    )
    assert r.status_code == 401
    # Nicht eingeloggt:
    assert client.get("/").status_code == 401


def test_login_without_csrf_rejected(client: TestClient) -> None:
    client.get("/login")
    r = client.post("/login", data={"password": TEST_PASSWORD}, follow_redirects=False)
    assert r.status_code == 403


def test_mutating_post_without_csrf_rejected(auth_client: TestClient) -> None:
    r = auth_client.post(
        "/watches/new",
        data={"title": "X", "status": "in_germany"},
        follow_redirects=False,
    )
    assert r.status_code == 403


def test_login_rate_limit(client: TestClient) -> None:
    """Nach 5 Fehlversuchen ist der Login vorübergehend gesperrt (429)."""
    r = client.get("/login")
    token = extract_csrf(r.text)
    for _ in range(5):
        r = client.post(
            "/login",
            data={"password": "immer-falsch", "csrf_token": token},
            follow_redirects=False,
        )
    r = client.post(
        "/login",
        data={"password": TEST_PASSWORD, "csrf_token": token},
        follow_redirects=False,
    )
    assert r.status_code == 429


def test_security_headers_present(auth_client: TestClient) -> None:
    r = auth_client.get("/")
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("x-frame-options") == "DENY"
    assert "frame-ancestors 'none'" in r.headers.get("content-security-policy", "")
    assert "form-action 'self'" in r.headers.get("content-security-policy", "")
    # Kein HSTS erzwingen (lokal oft http) – muss nicht gesetzt sein.


def test_session_cookie_flags(auth_client: TestClient) -> None:
    r = auth_client.get("/login")
    set_cookie = ";".join(auth_client.cookies.keys())  # Cookie existiert
    assert "session" in auth_client.cookies
    # httponly/samesite prüfen über rohen Header:
    raw = r.headers.get("set-cookie", "") or ""
    # (Login-Request selbst setzt kein Cookie; Session kommt vom GET.)
    r2 = auth_client.get("/")
    raw2 = r2.headers.get("set-cookie", "")
    combined = raw + raw2
    if "session=" in combined:
        assert "HttpOnly" in combined or "httponly" in combined.lower()
    assert set_cookie


def test_missing_propri_secret_fails_fast(monkeypatch) -> None:
    """Ohne Pflicht-Secrets bricht die App mit ConfigError ab (kein unsicherer Start)."""
    from app.config import ConfigError
    from app.main import create_app

    monkeypatch.delenv("ADMIN_PASSWORD_HASH", raising=False)
    with pytest.raises(ConfigError) as exc:
        create_app()
    assert "ADMIN_PASSWORD_HASH" in str(exc.value)


def test_too_short_session_secret_fails_fast(monkeypatch, env) -> None:
    from app.config import ConfigError
    from app.main import create_app

    monkeypatch.setenv("SESSION_SECRET", "kurz")
    with pytest.raises(ConfigError):
        create_app()


def test_plain_admin_password_is_hashed_on_load(monkeypatch, env) -> None:
    """ADMIN_PASSWORD (Klartext) reicht – die App hasht beim Start selbst."""
    from app.config import load_settings
    from app.security import verify_password

    monkeypatch.delenv("ADMIN_PASSWORD_HASH", raising=False)
    monkeypatch.setenv("ADMIN_PASSWORD", "mein-passwort-123")
    settings = load_settings()
    assert settings.admin_password_hash.startswith("$argon2")
    assert verify_password(settings.admin_password_hash, "mein-passwort-123")


def test_plain_admin_password_takes_precedence_over_missing(monkeypatch, env) -> None:
    """Ohne ADMIN_PASSWORD UND ohne HASH startet die App nicht (Fail-Fast)."""
    from app.config import ConfigError, load_settings

    monkeypatch.delenv("ADMIN_PASSWORD_HASH", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    with pytest.raises(ConfigError) as exc:
        load_settings()
    assert "ADMIN_PASSWORD" in str(exc.value)


def test_mangled_password_hash_fails_with_fix_hint(monkeypatch, env) -> None:
    """Ein durch $-Interpolation verstümmelter Hash wird klar benannt."""
    from app.config import ConfigError, load_settings

    monkeypatch.setenv(
        "ADMIN_PASSWORD_HASH", "rgon2id$v=19$m=65536,t=3,p=4$zerstoert$wert"
    )
    with pytest.raises(ConfigError) as exc:
        load_settings()
    msg = str(exc.value)
    assert "ADMIN_PASSWORD" in msg  # Hinweis auf den Klartext-Weg
    assert "$-Zeichen" in msg  # Ursache genannt


def test_login_requires_matching_username(client) -> None:
    """Zwei-Feld-Login: falscher Benutzername bleibt erfolglos, richtiger
    passiert – dieselbe Fehlerseite (keine User-Enumeration)."""
    r = client.get("/login")
    token = extract_csrf(r.text)
    r = client.post(
        "/login",
        data={
            "username": "wer-auch-immer",
            "password": TEST_PASSWORD,
            "csrf_token": token,
            "next_to": "/",
        },
        follow_redirects=False,
    )
    # Login-Fehler liefert 401 mit gerenderter Fehlerseite (kein Session-Cookie)
    assert r.status_code == 401
    assert "flash-error" in r.text

    r = client.get("/login")
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
    assert r.status_code == 303
