# Entwicklung, Qualitätssicherung, Image-Build & Registry-Push

## Lokale Entwicklung

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

export SESSION_SECRET="$(openssl rand -hex 32)"
export ADMIN_PASSWORD_HASH="$(python -m app.security 'dev-passwort')"
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

Ein GitHub-Actions-Workflow (oder vergleichbar) kann bei jedem Tag `v*`:
`pytest` + `ruff` + `mypy` ausführen, `docker buildx build --push` für die
konfigurierten Plattformen und den Tag aus der PyPI-/git-Version ableiten.
Registry-Zugangsdaten (GHCR-Token) liegen als CI-Secret, niemals im Repo.

## Anzeigen-Vorlagen anpassen

Plattform-Vorlagen: `app/listing_templates/` (`reddit.txt.j2`,
`facebook.txt.j2`) inkl. Hinweisen/Plattform-Notizen. Alternativ per
`LISTING_TEMPLATE_DIR` einen externen Ordner angeben (siehe `.env.example`) –
dann müssen die Dateien dort vollständig bereitstehen. Vorlagen dürfen keine
vertraulichen Kontextschlüssel ausgeben; der Generator filtert diese
zusätzlich ab (Defense in Depth), Tests sichern das.
