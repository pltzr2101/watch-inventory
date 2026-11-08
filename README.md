# Watch-Inventory

Selbst gehostetes internes Werkzeug zur Verwaltung eines Uhrenbestands und zur
Erstellung von Verkaufsanzeigen (Single-User, kein Shop).

**Umfang (MVP)**

1. Interne Uhren- und Bestandsverwaltung (authentifiziert)
2. Generator für Verkaufsanzeigen (Reddit- und Facebook-Vorlagen)
3. Bilderverwaltung, nicht-destruktive Korrekturen und ZIP-Export pro Anzeige

Nicht enthalten (bewusst, siehe `docs/spaete-ausbaustufen.md`): öffentliche
Bestandsseite, öffentliche APIs, Analytics-Dashboards, Shop-/Checkout-Funktionen,
automatisierte Veröffentlichung auf Plattformen.

## Features (aktueller Stand)

- **Bestandsverwaltung:** Uhren anlegen/bearbeiten/löschen, fünf
  Bestandsstatus (Bestand in DE, Bestand in China, Unterwegs, Reserviert,
  Verkauft) mit Statushistorie, Volltextsuche, Geldbeträge als Cents,
  Statushistorie, Upload-Fotoverwaltung.
- **Box-Option** (`box_option`: Ohne Box / Box inklusive / Box optional),
  Pflichtfelder werden nie geraten – leere Angaben erscheinen als
  „nicht gepflegt“.
- **Pump&Dump:** Einbildschirm-Anlage (Grunddaten + Fotos) mit direkt
  anschließender Review-Seite für Text, Bilder, Export und Tracking.
- **Anzeigengenerator:** vier Plattformformate (Reddit, Reddit-Code,
  Facebook, Telegram) mit eigenen Text- und Titelvorlagen, editierbaren
  Entwürfen, Kopieren, TXT-/Markdown-Export, ZIP-Export inklusive
  passendem Anzeigentext, optionalem Wasserzeichen.
- **Vorlagenverwaltung** unter `/templates`: Editor, Vorschau, Validierung,
  Override-Roundtrip gegen die mitgelieferten Vorlagen.
- **Veröffentlichungs-Tracking:** Status Draft/Bereit/Gepostet je
  Plattform (manuell, kein Auto-Posten).
- **Wecatalog.cn-Import:** standardmäßig deaktiviertes Feature-Flag
  (siehe unten).
- **Nicht enthalten (bewusst):** öffentliche Bestandsseite, Analytics,
  Shop-/Checkout-Funktionen – Vorbereitungen dazu in
  `docs/spaete-ausbaustufen.md`.

## Schnellstart (lokal)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Pflicht-Secrets erzeugen:
export SESSION_SECRET="$(openssl rand -hex 32)"
export ADMIN_PASSWORD_HASH="$(python -m app.security 'mein-passwort')"
export DATA_DIR=./data

uvicorn --factory app.main:create_app --host 127.0.0.1 --port 8080
```

Anmeldedaten sind die Auswahl aus `ADMIN_PASSWORD_HASH` (kein Default-Passwort).
Ohne `SESSION_SECRET`/`ADMIN_PASSWORD_HASH` startet die App absichtlich **nicht**.

## Tests, Lint, Typecheck

```bash
pytest                 # 75 Tests (Geschäftslogik, Rechte, Export, Upload, ZIP, Import)
ruff check app tests   # muss leer sein
mypy app tests         # muss sauber sein
```

## Produktion / Portainer

Vollständige Anleitung: **`docs/INSTALL.md`** (Stack, Secrets, Registry-Login,
Port 3080, Watchtower). Image-Build und Registry-Push: `docs/ENTWICKLUNG.md` –
der Repo-Workflow `.github/workflows/docker-publish.yml` führt Gates aus und
pusht bei jedem `main`-Push bzw. `v*`-Tag automatisch nach `ghcr.io`.
Daten, Backups, Updates, Restore: `docs/BACKUP.md`.

## Wecatalog.cn-Integration

Standardmäßig **deaktiviert** (`WECATALOG_ENABLED=false`). Umfang, Grenzen der
Einverständniserklärung und was vor der Aktivierung zu klären ist:
`docs/WEBCATALOG.md`. Die App ist ohne die Integration vollständig nutzbar.

## Architektur (kurz)

- **FastAPI + Jinja2-Server-Templates + SQLAlchemy/SQLite** – eine Codebasis,
  geschützte HTML-Oberfläche statt separater SPA/API.
- **Vertrauliche Daten** (Einkaufspreise, Zusatzkosten, interne Notizen,
  Quell-Snapshots) liegen in eigenen Spalten/Tabellen. Anzeigen- und
  Exportpfade lesen nur whitelisted Felder; interne Daten tauchen weder in
  Anzeigentexten, ZIPs noch in ungeschützten Routen auf.
- **`public_approved`** ist die spätere Freigabe-Schwelle für die öffentliche
  Bestandsansicht – aktuell wertet keine Route sie aus.
- **Statuswerte** (`in_china`, `in_transit`, `in_germany`, `reserved`, `sold`),
  `created_at`, `sold_at`, `status_history`, `imported_at` legen die Basis für
  spätere Lagerdauer-/Verkaufsanalysen.
- **Import- und Eigendaten** bleiben getrennt: `provenance`, `source_fields`,
  `source_content` (Snapshot), importierte Bilder mit `ImageSource.WECATALOG`;
  Übernahmen laufen ausschließlich über Vorschau + ausdrückliche Bestätigung.

## Betrieb & Sicherheit

- Alle Routen (inkl. Uploads, Bildzugriff, Anzeigen-/ZIP-Export) sind über die
  Session authentifiziert; Schreibroute zusätzlich per CSRF-Token.
- Geheimnisse ausschließlich über Umgebungsvariablen; keine Secrets im Repo.
- **Netzwerk-Exposition**: Die App ist für den internen Betrieb gedacht.
  Empfehlung: nur im internen Netz/VPN erreichbar lassen (Port nicht nach
  außen freigeben) oder hinter einen Reverse Proxy mit HTTPS legen
  (`COOKIE_SECURE=true` dann auf `true`). Ein direkter öffentlicher
  Internetzugriff ohne TLS wird nicht empfohlen.
- Login-Rate-Limit, Argon2-Password-Hashing, sichere Session-Cookies
  (HttpOnly/SameSite), XSS-Schutz durch Template-Escaping, parametrisierte
  SQL-Abfragen, Upload-Prüfung per Datei-Signatur + Pixel-Limit, ZIP-Erzeugung
  ohne Nutzernamen-Pfade (kein ZIP-Slip).

## Weiteres

- Umgebungsvariablen: `.env.example`
- Grenzen der Katalog-Integration: `docs/WEBCATALOG.md`
- Spätere Ausbaustufen (Bestandsseite öffentlich, Analytics):
  `docs/spaete-ausbaustufen.md`
