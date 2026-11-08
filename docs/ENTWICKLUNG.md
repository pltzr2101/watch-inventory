# Entwicklung, Qualitätssicherung, Image-Build & Registry-Push

## Lokale Entwicklung

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

export SESSION_SECRET="$(openssl rand -hex 32)"
export ADMIN_PASSWORD='dev-passwort'    # (oder ADMIN_PASSWORD_HASH=…)
export DATA_DIR=./data
uvicorn --factory app.main:create_app --host 127.0.0.1 --port 8080
```

Daten (SQLite, Bilder) liegen unter `$DATA_DIR` – vor dem Produktivbetrieb nicht
dieselben Verzeichnisse verwenden.

## Qualitätssicherung (Baseline vor jedem Push)

```bash
pytest                  # vollständige Testsuite
ruff check app tests    # muss leer sein
mypy app tests          # muss sauber sein
```

Die Tests decken u. a. ab: Geldbetrags-Präzision, Rechte-/Auth-Grenzen,
CSRF, Anzeigengenerierung inkl. Ausschluss interner Daten, Upload-Validierung
(Datei-Signatur, Pixel-Limit, Ablehnung nicht-Bilder), Korrektur-Rendering,
ZIP-Erzeugung (Reihenfolge, Dateinamen, keine Pfad-Traversale), Wecatalog-Import
(Preisseperation, Vorschau/Bestätigung, kein Überschreiben interner Felder,
sichere Fehler bei inaktiver/ungültiger Quelle).

## Produktions-Build (Python-Paket)

```bash
pip install build
python -m build          # bunt/sdist nach dist/
```

## Docker-Image bauen, versionieren, publizieren

Der Build läuft **hier** (Dev/CI), nicht im Portainer-Stack – der Stack zieht
ausschließlich das fertige Registry-Image.

```bash
# 1. Version festlegen (semantisch; gleiche Version wie in pyproject.toml):
TAG=0.1.0
IMAGE=ghcr.io/<github-user>/watch-inventory   # eigener Pfad

# 2. Login in die Registry:
echo $GITHUB_TOKEN | docker login ghcr.io -u <github-user> --password-stdin

# 3. Build + Push (getestet: linux/amd64; s. Architekturhinweis unten):
docker build -t "$IMAGE:$TAG" .
docker push "$IMAGE:$TAG"

# Optional: zusätzliches Arch-Suffix-Tag oder Multi-Arch-Build:
docker buildx build --platform linux/amd64,linux/arm64 \
  -t "$IMAGE:$TAG" --push .
```

Regeln für Tags:

- Immer **versionierten Tag** verwenden (`0.1.0`), nie als dokumentierten
  Standard `latest`.
- `latest` optional **zusätzlich** setzen, wenn gewünscht – der Stack referenziert
  ihn trotzdem nicht.
- Jede Änderung am Image = neuer Tag; alte Tags nie überschreiben.

### Architekturunterstützung

- Basisbild `python:3.12-slim` existiert für `linux/amd64` und `linux/arm64`.
- **Getestet wurde `linux/amd64`.** Für arm64 (z. B. Raspberry Pi / ARM-LXC)
  steht der `buildx`-Befehl oben bereit, die Laufzeit wurde dort nicht
  verifiziert – vor produktiver Nutzung dort bitte einmaligen Funktionstest
  (Login, Upload, ZIP-Export) durchführen.

### CI-Variante (empfohlen)

Der Repo-Workflow **`.github/workflows/docker-publish.yml`** (gleiche Struktur
wie beim Poker-Session-Manager) übernimmt das vollständig:

- **Trigger:** Push auf `main`, git-Tags `v*`, oder manuell
  (`workflow_dispatch`).
- **Gates vor dem Build:** `ruff` → `mypy` → `pytest` – ein rotes Gate
  veröffentlicht kein Image.
- **Registry-Push:** `ghcr.io/<user>/<repo>` mit den Tags `latest` (nur
  `main`), `sha-…`, `build-<run>` sowie semver `X.Y.Z` und `X.Y` aus dem
  git-Tag. Authentifizierung über das repo-interne `GITHUB_TOKEN`
  (`permissions: packages: write`) – **kein zusätzliches CI-Secret**,
  Zugangsdaten liegen nirgends im Repo.
- **Plattform:** `linux/amd64` (getestet; s. Architekturhinweis unten).

**Release-Ablauf:**

```bash
# 1. Version in pyproject.toml anheben (semantisch), auf main committen
git tag v0.2.0
git push origin v0.2.0      # löst Gates + Build + Tag-Push nach ghcr aus
```

Ohne Tag wird bei jedem `main`-Push ebenfalls gebaut (`latest` +
`sha-…` + `build-…`). Der Portainer-Stack nutzt standardmäßig
`latest` (Watchtower-Updates); Versionstage bleiben für
reproduzierbare Stände und Rollbacks verfügbar.

## Anzeigen-Vorlagen anpassen

Vier Plattformformate, je aus Text- und Titelvorlage plus Hinweistexten:

| Format | Textvorlage | Titelvorlage |
| --- | --- | --- |
| Reddit (r/Reptime, WatchExchange …) | `reddit.txt.j2` | `reddit.title.txt.j2` |
| Reddit – Code-/Werks-Code-Format | `reddit_code.txt.j2` | `reddit_code.title.txt.j2` |
| Facebook (DE/EN/China/Vietnam-Gruppen) | `facebook.txt.j2` | `facebook.title.txt.j2` |
| Telegram | `telegram.txt.j2` | `telegram.title.txt.j2` |

Zusätzlich `hints.json` (Plattform-Hinweistexte, Default aus
`PLATFORM_HINTS`).

**Verwaltung in der App:** `Vorlagen` in der Navigation – Liste, Editor,
Vorschau mit Beispielkontext, Speichern als **Override** und Löschen
(stellt die mitgelieferte Vorlage wieder her). Die Whitelist erlaubt nur
diese Dateinamen (kein Pfadzugriff). Jede Vorlage wird vor dem Speichern
syntaktisch geprüft und mit `StrictUndefined` gegen einen Beispielkontext
gerendert – Tippfehler in Variablen fallen sofort auf. Alle Aktionen sind
authentifiziert und CSRF-geschützt.

**Speicherorte:** Override-Dateien liegen in `LISTING_TEMPLATE_DIR`
(Datenordner/Volume), mitgelieferte Dateien unter `app/listing_templates/`
(gelten als Fallback; ein externer Ordner muss alle Dateien selbst
bereitstellen).

**Statische Muster in den Vorlagen (Standort/PLZ, Zahlung, Versand,
„Seller Code: 1949“, „Box optional (+50 €)“, „GELIEFERT AUS CHINA“):**
Diese Zeilen sind Teil der Vorlagen selbst und lassen sich dort direkt
anpassen. **Vor dem ersten produktiven Einsatz gegen die eigenen
Originalmuster abgleichen** – nicht alle Zeilen sind aus einer geprüften
Quelle im Projekt dokumentiert (nachvollziehbar sind: Reddit-Seller-Code
1949 aus den Produktvorgaben, „Box optional (+50 €)“ aus dem
Setup-Bestand von `BOX_OPTIONS`).

**Titellogik:** Plattformspezifisch im Generator
(`listing_generator.generate_title`) – u. a. Werks-Code
(`specs`-Schlüssel „factory“) im Reddit-Titel, Seller-Code im
Reddit-Code-Format, deutsche Vorzeichen bei Facebook.

**Neue Plattform hinzufügen** (Codeänderung, bewusst nicht UI):
`ListingPlatform`-Enum, `PLATFORM_LABELS`, `PLATFORM_HINTS`
(`listing_generator`), Titel-/Textvariante in `generate_title()` bzw. den
Vorlagendateien, Plausibilitätsregel in `parse_watch_form()` sowie
`TEMPLATE_CONTEXT_KEYS` unverändert prüfen (Whitelist schützt vor
vertraulichen Kontextdaten – Defense in Depth, durch Tests abgesichert).

## Pump&Dump (Schnellanlage)

`+ Dump` → ein Formular (Pflicht: nur Titel; optional Factory, Movement,
Zustand, Box, Verkaufspreis, Status, Beschreibung, Fotos) → sofort
Review-Seite: Anzeigentext für alle vier Plattformen ansehen/speichern,
Bilder auto-sortieren, Status „Bereit/Gepostet“ pflegen, ZIP exportieren.
Fotos werden **vor** dem Anlegen des Datensatzes vollständig geprüft
(echter Typ, Größe, Pixelmaße); bei Speicherproblemen werden abgelegte
Dateien wieder entfernt. Nicht gepflegte Felder erscheinen als
„nicht gepflegt“ – es wird nie geraten.

## Bildrollen & automatische Reihenfolge

`app/services/image_classify.py` ordnet über Filename-Keywords
(DE/EN) Rollen zu (dial → front → case → side → back → clasp → bracelet →
papers → box → wrist) und füllt ausschließlich **leere** Labels;
manuelle Labels und Pfeil-Sortierung haben immer Vorrang. Auswirkung:
Exportdateien `01-…`, `02-…` in dieser Reihenfolge (Button
„Reihenfolge automatisch optimieren“ auf Dump-Review und Uhrenseite).

## iPhone-Fotos (HEIC/HEIF)

Uploads akzeptieren zusätzlich HEIC/HEIF vom iPhone (`pillow-heif`),
einschließlich 48-MP-Aufnahmen (`MAX_IMAGE_PIXELS` standardmäßig
80 MP; `MAX_UPLOAD_MB` begrenzt die Dateigröße). Für die Anzeige
legt die App neben dem unveränderten Original ein JPEG-Derivat an
(`…preview.jpg`, ≤2048 px); Thumbs holen es über `?display=1`,
das Löschen entfernt beide Dateien. ZIP-Exporte, Wasserzeichen und
Korrekturen arbeiten auf optimierten JPEG-Kopien (`01-…jpg`) – die
Originale bleiben byte-identisch unverändert. Nicht darstellbare
Originalformate werden nirgends öffentlich ausgeliefert (alle
Bildrouten liegen hinter Auth).

## Wasserzeichen (optional, standardmäßig aus)

`WATERMARK_ENABLED=0` (Default) / `WATERMARK_TEXT` (Pflicht bei aktiv);
zusätzlich pro Export per Checkbox mit eigenem Text überschreibbar.
Wasserzeichen werden ausschließlich auf **Exportkopien** im ZIP
aufgebracht, nie auf gespeicherte Originale. Kein Fremdlogo, keine
KI-Retusche; Katalogbilder bleiben bei deaktivierter
`WEBCATALOG_ALLOW_IMAGE_EDIT` unverändert.

## Veröffentlichungs-Tracking (kein Auto-Post)

`Publication` je Uhr und Plattform: Status `draft → ready → posted`
(optional mit Beitrag-URL, http(s)-validiert, `posted_at`-Zeitstempel für
spätere Analysen). Bedienung auf Listing-/Dump-Review-Seite. Die App
veröffentlicht niemals automatisch – Text und Bilder werden kopiert bzw.
heruntergeladen und manuell eingefügt.
