"""Autorisierter Wecatalog.cn-Import – standardmäßig hinter Feature-Flag deaktiviert.

Rahmen (siehe docs/WECATALOG.md):
- robots.txt der Quelle sperrt die gesamte Site, eine öffentliche offizielle
  API/Export-Methode war nicht auffindbar → KEIN automatisiertes Scrapen.
  Der MVP-Import ist manuell: Inhalte werden vom autorisierten Betreiber
  selbst übertragen und hier als Vorschau eingesetzt.
- Optional (nur mit WECATALOG_ENABLED=true und konfigurierter WECATALOG_API_URL)
  kann ein einzelner Katalogeintrag über einen ausdrücklich freigegebenen
  Endpunkt geladen werden – ausschließlich mit Allowlist-Präfix, Timeout,
  Größenlimit und Sichtschutz pro Stunde. Kein Umgehen von Zugangskontrollen.
- Preise/Preisangaben werden serverseitig IMMER entfernt (auch vor der Vorschau).
- Quelldaten überschreiben nie Einkaufs-/Verkaufspreise oder interne Notizen;
  bestätigte Aktualisierungen berühren eigene, zwischenzeitlich geänderte
  Inhalte nicht (Vorschau + ausdrückliche Bestätigung).
"""
from __future__ import annotations

import json
import re
from collections import deque
from time import monotonic

import httpx

from ..config import Settings

SOURCE_NAME = "Wecatalog.cn"

MAX_PAYLOAD_BYTES = 1_000_000
MAX_SOURCE_URL_LEN = 500


class WecatalogDisabledError(RuntimeError):
    """Feature-Flag ist nicht gesetzt – Importwege sind geschlossen."""


class WecatalogFetchError(RuntimeError):
    """Quelle nicht erreichbar, ungültig oder Limit überschritten."""


PRICE_KEY_RE = re.compile(
    r"(?i)(price|pricing|prices|retail|msrp|sale[_ -]?price|list[_ -]?price|"
    r"cost|amount|currency|subtotal|total|"
    r"售价|价格|报价|原价|到手价|促销价|折扣价|市场价)"
)
CURRENCY_VALUE_RE = re.compile(
    r"(?<!\d)(?:[$€£¥￥]\s?\d[\d.,]*|\d[\d.,]{1,12}\s?(?:EUR|USD|CNY|RMB|€|€|元)(?!\w))"
)
PRICE_SENTENCE_RE = re.compile(
    r"(?i)\b(?:price|pricing|retail\s*price|msrp)\b\s*[:：]?\s*=?\s*\d[\d.,]*"
    r"|(?:售价|价格|报价)\s*[:：]?\s*\d[\d.,]*"
)

_FETCH_TIMES: deque[float] = deque()


def strip_prices(value):  # type: ignore[no-untyped-def]
    """Entfernt Preisschlüssel und Preisangaben rekursiv aus Importdaten."""
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if PRICE_KEY_RE.search(str(key)):
                continue
            cleaned[key] = strip_prices(item)
        return cleaned
    if isinstance(value, list):
        # Komplett ausgehöhlte Objekte (nur Preisbestandteile) fallen weg.
        return [cleaned for item in value if (cleaned := strip_prices(item)) != {}]
    if isinstance(value, str):
        text = PRICE_SENTENCE_RE.sub("", value)
        text = CURRENCY_VALUE_RE.sub("", text)
        text = re.sub(r"\s{2,}", " ", text).strip()
        return text
    return value


def sanitize_payload(payload: dict) -> dict:
    """Nur freigegebene Inhaltsfelder; Preise werden strikt entfernt."""
    if not isinstance(payload, dict):
        raise ValueError("Importpayload muss ein Objekt sein.")
    cleaned = strip_prices(payload)
    # Auf erlaubte Inhaltsfelder zuschneiden (whitelist statt blacklist).
    allowed = {}
    for key in ("title", "brand", "model", "description", "specs"):
        if key in cleaned and cleaned[key] not in (None, "", [], {}):
            allowed[key] = cleaned[key]
    if "specs" in allowed:
        specs = _normalize_specs(allowed["specs"])
        if specs:
            allowed["specs"] = specs
        else:
            del allowed["specs"]
    return allowed


def _normalize_specs(value) -> list[dict[str, str]]:  # type: ignore[no-untyped-def]
    """Specs aus beliebiger Quellstruktur in [{'key','value'}, …] überführen."""
    if isinstance(value, dict):
        value = [{"key": k, "value": v} for k, v in value.items()]
    if isinstance(value, str):
        try:
            return parse_specs(value)
        except ValueError:
            return []
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        if isinstance(item, dict) and str(item.get("key", "")).strip():
            key = str(item["key"]).strip()
            if PRICE_KEY_RE.search(key):
                continue  # Preisschlüssel auch innerhalb von Specs strikt entfernen
            result.append({"key": key, "value": str(item.get("value", "")).strip()})
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            key = str(item[0]).strip()
            if PRICE_KEY_RE.search(key):
                continue
            result.append({"key": key, "value": str(item[1]).strip()})
    return result


def payload_has_prices(payload: dict) -> bool:
    """Prüft (für Tests/Sicherheitsnetz) ob Preisreste übrig geblieben sind."""
    dump = json.dumps(payload, ensure_ascii=False)
    return bool(PRICE_KEY_RE.search(dump) or CURRENCY_VALUE_RE.search(dump))


def parse_specs(text: str) -> list[dict[str, str]]:
    """Zerlegt „Schlüssel: Wert“-Zeilen oder JSON in eine Specs-Liste."""
    text = (text or "").strip()
    if not text:
        return []
    if text.startswith("{") or text.startswith("["):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Specs-JSON ungültig: {exc.msg}") from exc
        if isinstance(data, dict):
            data = [{"key": k, "value": v} for k, v in data.items()]
        if not isinstance(data, list):
            raise ValueError("Specs müssen eine Liste oder ein Objekt sein.")
        result = []
        for item in data:
            if isinstance(item, dict) and item.get("key"):
                result.append({"key": str(item["key"]), "value": str(item.get("value", ""))})
        return result

    result = []
    for line in text.splitlines():
        if not line.strip():
            continue
        match = re.match(r"^\s*([^:=]{1,80})\s*[:=]\s*(.+)$", line)
        if not match:
            raise ValueError(
                f"Specs-Zeile unverständlich (erwartet 'Schlüssel: Wert'): {line.strip()!r}"
            )
        result.append({"key": match.group(1).strip(), "value": match.group(2).strip()})
    return result


def specs_as_text(specs: list | None) -> str:
    if not specs:
        return ""
    return "\n".join(f"{item.get('key', '')}: {item.get('value', '')}" for item in specs)


def require_enabled(settings: Settings) -> None:
    if not settings.wecatalog_enabled:
        raise WecatalogDisabledError(
            "Wecatalog-Import ist deaktiviert (WECATALOG_ENABLED=false). "
            "Siehe docs/WECATALOG.md für die offenen Punkte vor der Aktivierung."
        )


def _allow_fetch(settings: Settings) -> str:
    require_enabled(settings)
    if not settings.wecatalog_api_url:
        raise WecatalogFetchError(
            "Keine WECATALOG_API_URL konfiguriert. Ohne offiziellen, freigegebenen "
            "API-/Export-Endpunkt ist nur der manuelle Import vorgesehen."
        )
    return settings.wecatalog_api_url.rstrip("/") + "/"


def check_rate_limit(settings: Settings) -> None:
    """Sichtschutz: höchstens WECATALOG_MAX_FETCHES_PER_HOUR Abrufe pro Stunde."""
    now = monotonic()
    while _FETCH_TIMES and now - _FETCH_TIMES[0] > 3600:
        _FETCH_TIMES.popleft()
    if len(_FETCH_TIMES) >= settings.wecatalog_max_fetches_per_hour:
        raise WecatalogFetchError(
            f"Abruflimit erreicht (max. {settings.wecatalog_max_fetches_per_hour} "
            "Abrufe pro Stunde). Später erneut versuchen."
        )
    _FETCH_TIMES.append(now)


def fetch_catalog_entry(settings: Settings, url: str, *, transport=None) -> dict:  # type: ignore[no-untyped-def]
    """Lädt EINEN Katalogeintrag über einen freigegebenen Endpunkt (optional).

    - nur mit gesetztem Feature-Flag und konfigurierter API-URL
    - URL-Mustervorlage: die API-URL selbst oder darunter (Präfix-Allowlist)
    - Timeouts, Größenlimit, kein Redirect-Überspringen, Sichtschutz
    - Ergebnis wird strikt preisbereinigt
    """
    api_prefix = _allow_fetch(settings)
    target = (url or "").strip()
    if not target:
        raise WecatalogFetchError("Keine Quell-URL angegeben.")
    if not target.startswith(api_prefix) and target.rstrip("/") + "/" != api_prefix:
        raise WecatalogFetchError(
            "URL liegt außerhalb der konfigurierten freigegebenen API (WECATALOG_API_URL)."
        )
    if len(target) > MAX_SOURCE_URL_LEN:
        raise WecatalogFetchError("Quell-URL ist zu lang.")

    check_rate_limit(settings)

    headers = {"Accept": "application/json", "User-Agent": "watch-inventory/0.1 (manual import)"}
    if settings.wecatalog_api_token:
        headers["Authorization"] = f"Bearer {settings.wecatalog_api_token}"

    try:
        with httpx.Client(
            timeout=settings.wecatalog_timeout_seconds,
            follow_redirects=False,
            transport=transport,
        ) as client:
            response = client.get(target, headers=headers)
    except httpx.HTTPError as exc:
        raise WecatalogFetchError(
            f"Quelle nicht erreichbar: {type(exc).__name__} – später erneut versuchen."
        ) from exc

    if response.status_code >= 400:
        raise WecatalogFetchError(f"Quelle antwortet mit HTTP {response.status_code}.")
    if len(response.content) > MAX_PAYLOAD_BYTES:
        raise WecatalogFetchError("Quellantwort zu groß – Import abgelehnt.")
    try:
        data = response.json()
    except ValueError as exc:
        raise WecatalogFetchError("Quellantwort ist kein gültiges JSON.") from exc
    if not isinstance(data, dict):
        raise WecatalogFetchError("Quellantwort hat ein unerwartetes Format.")

    payload = sanitize_payload(data)
    if not payload:
        raise WecatalogFetchError("Keine übernehmbaren Inhalte (nur Preise/leere Felder?).")
    return payload
