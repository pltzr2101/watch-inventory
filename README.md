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
  Verkauft), Volltextsuche, Geldbeträge als Cents, Upload-Fotoverwaltung
  (JPEG/PNG/WebP und HEIC/HEIF vom iPhone, bis 48 MP) mit automatischer
  JPEG-Ansicht – Originale bleiben unverändert.
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
- **UI „Horological Atelier":** dunkles Atelier-Design mit lokal gehosteten
  Schriften (Fraunces/Jost, OFL-Lizenzen unter `app/static/fonts/`), Messing-
  Akzente, Status-Badges, subtile Lad-Animationen, responsive; reines CSS,
  kein JS-Framework, keine externen Requests.
- **Nicht enthalten (bewusst):** öffentliche Bestandsseite, Analytics,
  Shop-/Checkout-Funktionen – Vorbereitungen dazu in
  `docs/spaete-ausbaustufen.md`.

## Schnellstart (lokal)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Pflicht-Secrets erzeugen:
export SESSION_SECRET="$(openssl rand -hex 32)"
export ADMIN_PASSWORD='mein-passwort'   # (oder ADMIN_PASSWORD_HASH=…)
export DATA_DIR=./data

uvicorn --factory app.main:create_app --host 127.0.0.1 --port 8080
```

Anmeldedaten sind die Auswahl aus `ADMIN_PASSWORD_HASH` (kein Default-Passwort).
Ohne `SESSION_SECRET`/`ADMIN_PASSWORD_HASH` startet die App absichtlich **nicht**.

## Tests, Lint, Typecheck

```bash
pytest                 # 84 Tests (Geschäftslogik, Rechte, Export, Upload/HEIC, ZIP, Import)
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

## V2-Formularwork (Bestandsverwaltung)

- **Anlage nur noch über „Bestand → + Uhr anlegen“** (ein Formular inkl. direktem
  Foto-Upload). Die alten `/dump`-URLs leiten weiter; die Nav zeigt nur noch
  Übersicht · Bestand · Importe · Vorlagen.
- **Titel** setzt sich aus Marke, Factory, Version, Modell, Band und
  Farbe/Besonderheit zusammen (`Marke Factory – Version – Modell, Band, Highlight`),
  live im Formular und serverseitig als Rückfallebene; manuelle Eingaben gewinnen.
- **Einkauf in USD** + historischer Kurs (`FX_RATE_URL` liefert nur einen Vorschlag);
  EUR-EK wird exakt in Cent gerechnet und ist die Marge-Grundlage.
- **Zusatzkosten** als Einzelpositionen mit Kategorie (China-Logistik, DHL, Wise,
  Box, Sonstiges), dynamisch erweiterbar.
- **Technische Daten** als Checkliste (Kaliber, Gehäusedurchmesser, Werk):
  Haken = Pflichtwert.
- **Statuswechsel auf „Verkauft“** erzwingt den endgültigen Verkaufspreis
  (Dialog, serverseitig durchgesetzt); die Detailseite zeigt die Marge je Uhr.
- Entfallen: Zustandsbeschreibung, Lieferumfang und eigene Beschreibungstexte
  (Beschreibung bleibt ausschließlich als Wecatalog-Übernahmefeld erhalten).

### Kostenpositionen V3 (Währung, Prozent, eigene Kategorien)

- Jede Zusatzkosten-Position: **Kategorie (Dropdown) – Währung (USD/EUR) –
  Wert ODER Basis × %**. USD-Beträge werden mit dem **einen** historischen
  Kurs der Uhr (Button „Aktuellen Kurs holen") umgerechnet – der Kurs ist
  auch dann Pflicht, wenn nur USD-Positionen ohne USD-Einkauf existieren.
- **Prozent-Modus** (z. B. Versicherung 6 %): Basis × % wird serverseitig in
  Ganzzahl-Cents gerechnet und in der Detailansicht vollständig hergeleitet
  (`6 % von 110,00 USD = 6,60 USD × 1,092 = 7,21 €`); Knopf
  „＋ Versicherung (6 %)" legt eine solche Zeile vor.
- **Kategorien sind Daten** (Tabelle `cost_categories`, Startbestand inkl.
  Versand China, Versicherung, Wise-Gebühren, …): unter
  **„Kostenpositionen → ✎ Kategorien verwalten"** (`/cost-categories`) eigene
  Kategorien anlegen/umbenennen/archivieren – ohne Codeänderung. Umbenennen
  wirkt über alle Positionen (saubere Dashboard-Gruppierung später),
  Archivieren verhindert nur die Neunutzung.
- Detailseite zeigt je Position Herleitung + EUR-Umrechnung, Summe Zusatz,
  EK gesamt und Marge auf Basis der EUR-Summe (Maximale Transparenz).

### Paket A+B: Sprachfeste Zustands-/Versandwerte & Box-Inventar (V4)

- **Zustand/Versand sind Schlüssel** (`app/texts.py`): Formular-Selects,
  Speicherung als Key, **Übersetzung je Plattformsprache** im Listing-Generator
  (`PLATFORM_LANG`: facebook/telegram=de, reddit/reddit_code=en). Alte Freitexte
  werden beim Start normalisiert („new, fully stickered" → `neu_foliert`);
  Unklares wandert intern in `condition_note`/`internal_notes` – nichts geht
  verloren, Anzeigen erfinden nichts. Zusätzliches internes
  `condition_note`-Feld (nie in Anzeigen).
- **Box-Inventar** (`/boxes`, Nav „Boxen"): Box-Typen + Bulk-Einkäufe
  (Menge/Gesamtpreis), **Ø-EK = gewichteter Durchschnitt aller Einkäufe**
  (bewusst kein FIFO – begründet in `app/services/boxes.py`), Bestand
  abgeleitet (Einkäufe − verkaufte Uhren mit Box-Typ, Statuswechsel gibt frei).
- **Verkaufs-Dialog** fragt Verkaufsumfang (mit/ohne Box), Box-Typ (mit
  Bestandsanzeige) und optionalen **VK-Anteil Box**; beim Setzen auf „Verkauft"
  wird der Ø-EK als **Snapshot** gespeichert. Detailseite trennt dann
  **Marge Uhr / Marge Box / Marge gesamt**; ohne Aufteilung Hinweis +
  Gesamtmarge inkl. Box-EK.
- Box-EK und Snapshots erscheinen **nie** in Anzeigen- oder ZIP-Kontexten
  (eigener Test).
