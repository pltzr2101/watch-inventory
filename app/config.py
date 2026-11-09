"""Umgebungsbasierte Konfiguration mit Fail-Fast für Pflicht-Secrets.

Es gibt keine hartcodierten Zugangsdaten: Fehlen Pflicht-Secrets, bricht die
Anwendung mit klarer Meldung ab, statt unsicher zu starten.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ARGON2_PREFIX = "$argon2"


class ConfigError(RuntimeError):
    """Pflicht-Konfiguration fehlt oder ist ungültig."""


def _require_env(name: str, *, min_length: int = 1) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise ConfigError(
            f"Pflicht-Umgebungsvariable '{name}' ist nicht gesetzt. "
            "Die Anwendung startet aus Sicherheitsgründen nicht. "
            "Vorlage und Erklärung: .env.example (Abschnitt 'Secrets')."
        )
    if len(value) < min_length:
        raise ConfigError(
            f"Umgebungsvariable '{name}' ist zu kurz (mindestens {min_length} Zeichen)."
        )
    return value


def _opt_env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or "").strip() or default


def fx_rate_url_from_env(raw: str | None) -> str:
    """FX_RATE_URL: nicht gesetzt → Standarddienst, ausdrücklich leer → deaktiviert.

    Anders als `_opt_env` muss hier ein ausdrücklich leer gesetzter Wert
    erhalten bleiben – er schaltet den Kursdienst ab.
    """
    if raw is None:
        return "https://api.frankfurter.dev/v1/latest?from=USD&to=EUR"
    return raw.strip()


def _int_env(name: str, default: int, min_value: int, max_value: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(
            f"Umgebungsvariable '{name}' muss eine ganze Zahl sein, bekam: {raw!r}"
        ) from exc
    if not min_value <= value <= max_value:
        raise ConfigError(
            f"Umgebungsvariable '{name}' muss zwischen {min_value} und {max_value} "
            f"liegen, bekam: {value}"
        )
    return value


def _bool_env(name: str, default: bool) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"Umgebungsvariable '{name}' muss true oder false sein, bekam: {raw!r}")


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    session_secret: str
    admin_password_hash: str
    operator_id: str = "operator"
    cookie_secure: bool = False
    cookie_max_age_seconds: int = 12 * 60 * 60
    max_upload_bytes: int = 15 * 1024 * 1024
    max_image_pixels: int = 80_000_000
    login_max_attempts: int = 8
    login_window_seconds: int = 300
    wecatalog_enabled: bool = False
    wecatalog_base_url: str = "https://www.wecatalog.cn"
    wecatalog_api_url: str = ""
    wecatalog_api_token: str = ""
    wecatalog_timeout_seconds: int = 10
    wecatalog_max_fetches_per_hour: int = 6
    # Standard false: Katalogbilder werden unverändert behandelt, solange der
    # Erlaubnisumfang für Bearbeitungen nicht eindeutig geklärt ist.
    wecatalog_allow_image_edit: bool = False
    # Export-Ausgabe (nur ZIP/Exportkopien, Originale bleiben unangetastet):
    export_max_edge: int = 2000  # lange Kante in px; 0 = nicht verkleinern
    export_quality: int = 85  # JPEG-/WEBP-Qualität beim Export
    # Wasserzeichen: standardmäßig AUS; Text pro Export in der UI überschreibbar.
    watermark_enabled: bool = False
    watermark_text: str = ""
    listing_template_dir: Path | None = None
    # USD→EUR-Kursdienst für das Einkaufsformular (nur Vorschlag per Knopf;
    # "" deaktiviert den Dienst, Eingabe dann rein manuell).
    fx_rate_url: str = "https://api.frankfurter.dev/v1/latest?from=USD&to=EUR"
    extra: dict = field(default_factory=dict)

    @property
    def database_path(self) -> Path:
        return self.data_dir / "watch-inventory.sqlite3"

    @property
    def images_dir(self) -> Path:
        return self.data_dir / "images"

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.images_dir, self.tmp_dir):
            path.mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    """Liest die Konfiguration aus der Umgebung und validiert Pflicht-Secrets."""
    session_secret = _require_env("SESSION_SECRET", min_length=32)
    admin_password_hash = (os.environ.get("ADMIN_PASSWORD_HASH") or "").strip()
    if admin_password_hash and not admin_password_hash.startswith(ARGON2_PREFIX):
        raise ConfigError(
            "ADMIN_PASSWORD_HASH ist kein Argon2-Hash. Häufigste Ursache: Der "
            "Wert wurde beim Eintragen verstümmelt ($-Zeichen werden in der "
            "Stack-YAML als Variablen gelesen). Am einfachsten stattdessen "
            "ADMIN_PASSWORD (Klartext) in den Stack-Variablen setzen – die App "
            "hasht es beim Start selbst."
        )
    if not admin_password_hash:
        plain_password = (os.environ.get("ADMIN_PASSWORD") or "").strip()
        if not plain_password:
            raise ConfigError(
                "ADMIN_PASSWORD (oder ADMIN_PASSWORD_HASH) fehlt. Die Anwendung "
                "startet aus Sicherheitsgründen nicht. "
                "Vorlage und Erklärung: .env.example (Abschnitt 'Secrets')."
            )
        # Klartext-Passwort wie bei Grafana & Co.: wird hier sofort intern zu
        # Argon2 gehasht; der Hash existiert nur im Speicher des Prozesses.
        from .security import hash_password

        admin_password_hash = hash_password(plain_password)

    template_dir_raw = _opt_env("LISTING_TEMPLATE_DIR")
    if template_dir_raw:
        listing_template_dir = Path(template_dir_raw).expanduser().resolve()
    else:
        # Persistente Vorlagenordner in den Daten (Volume) – Upload/Editor-UI
        # schreibt dorthin; Built-ins bleiben Fallback (ChoiceLoader).
        listing_template_dir = None  # wird nach data_dir unten ergänzt

    data_dir = Path(_opt_env("DATA_DIR", "./data")).expanduser().resolve()
    if listing_template_dir is None:
        listing_template_dir = data_dir / "listing_templates"

    return Settings(
        data_dir=data_dir,
        session_secret=session_secret,
        admin_password_hash=admin_password_hash,
        cookie_secure=_bool_env("COOKIE_SECURE", False),
        cookie_max_age_seconds=_int_env(
            "COOKIE_MAX_AGE_SECONDS", 12 * 60 * 60, 300, 60 * 60 * 24 * 30
        ),
        max_upload_bytes=_int_env("MAX_UPLOAD_MB", 15, 1, 100) * 1024 * 1024,
        max_image_pixels=_int_env("MAX_IMAGE_PIXELS", 80_000_000, 1_000_000, 500_000_000),
        login_max_attempts=_int_env("LOGIN_MAX_ATTEMPTS", 8, 1, 100),
        login_window_seconds=_int_env("LOGIN_WINDOW_SECONDS", 300, 10, 3600),
        wecatalog_enabled=_bool_env("WECATALOG_ENABLED", False),
        wecatalog_base_url=_opt_env("WECATALOG_BASE_URL", "https://www.wecatalog.cn"),
        wecatalog_api_url=_opt_env("WECATALOG_API_URL"),
        wecatalog_api_token=_opt_env("WECATALOG_API_TOKEN"),
        wecatalog_timeout_seconds=_int_env("WECATALOG_TIMEOUT_SECONDS", 10, 1, 120),
        wecatalog_max_fetches_per_hour=_int_env(
            "WECATALOG_MAX_FETCHES_PER_HOUR", 6, 1, 1000
        ),
        wecatalog_allow_image_edit=_bool_env("WECATALOG_ALLOW_IMAGE_EDIT", False),
        export_max_edge=_int_env("EXPORT_MAX_EDGE", 2000, 0, 8000),
        export_quality=_int_env("EXPORT_QUALITY", 85, 60, 95),
        watermark_enabled=_bool_env("WATERMARK_ENABLED", False),
        watermark_text=_opt_env("WATERMARK_TEXT")[:80],
        listing_template_dir=listing_template_dir,
        fx_rate_url=fx_rate_url_from_env(os.environ.get("FX_RATE_URL")),
    )
