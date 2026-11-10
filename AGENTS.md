# AGENTS.md – Watch-Inventory (pltzr2101)

## Projekt & Workflow
- **Python/FastAPI + SQLite/SQLAlchemy + Jinja2**, Templates in `app/templates/`,
  Dark-UI-CSS in `app/static/style.css`, Vanilla-JS in `app/static/app.js`.
- **Produktions-Workflow (Nutzer-Erlaubnis): nach grünen Gates DIREKT auf `main`
  committen und pushen** (kein PR, kein Feature-Branch). CI baut das Registry-Image.
- Gates (alle, immer): `python3 -m pytest tests/ -q` (rc=0) ·
  `python3 -m ruff check app/ tests/` · `python3 -m mypy app/` ·
  `node --check app/static/app.js`.
- Deployment: Portainer-Stack, Host-Port **3080**, Registry-Image, `.env.example`.
- Screenshots/Artefakte für den Nutzer nach `.demo/` (gitignored).

## Design-Baseline (PFLICHT – „eine Design-Sprache“, Stand 2026-11-10)
- **Daten-Tabellen IMMER `class="table"`** – das ist die Baseline (Fläche
  `--surface`, Rahmen/Radius, uppercase-Header, Hover, Borders). NUR als
  Mobile-Karten-Erweiterung zusätzlich `tbl-stack` (`class="table tbl-stack"`);
  die Mobile-Regeln stehen im `@media (max-width: 640px)`-Block AM ENDE von
  style.css (Reihenfolge entscheidet). Spalten mit `data-label` versehen.
- Geld/Datenwerte: Klasse `nowrap` (kein Umbruch), Tabellen-Kennzahlen
  `font-variant-numeric: tabular-nums` wo vorhanden (`.wd-*`).
- Karten: `.card` (H2-Überschrift), Formulare: `.row-form` (mobil automatisch
  gestapelt, full-width), Buttons: `.btn` / `.btn-primary` / `.btn-sm` / `.btn-xs`,
  Löschaktionen: `.btn-danger` mit `data-confirm="…"` (Dialog kommt aus app.js,
  CSP-konform).
- Status: `.badge badge-<status>` · Tags: `.tag` · Flashes: `.flash flash-error|success`.
- Dashboard-Widgets: `.wd-kpi`, `.wd-tbl`, `.wd-bars`, `.wd-seg` (eigene Sprache
  der Auswertung, Farben `#8d93a1/#262b35/#e8b84b`).
- **Keine Inline-`<style>`/`<script>`-Blöcke in Templates** (CSP: `script-src 'self'`,
  `style-src-attr 'unsafe-inline'` nur für Element-Attribute); Styles nach
  style.css, Verhalten nach app.js mit Seiten-Guards.
- Mobile-Prüfung: CDP-Emulation390×844, Viewport-Breite =
  `document.documentElement.clientWidth`; sauber = `scrollWidth == clientWidth`
  und0 Elemente über dem Rand.

## Auth & Umgebung
- Zwei-Feld-Login: Username `APP_USERNAME` (Env, Default `admin`, kein Secret) +
  `ADMIN_PASSWORD[_HASH]` + `SESSION_SECRET` (Pflicht-Secrets, sonst harter Stopp).
- Lokaler Dev-Server: `SESSION_SECRET=<32+> ADMIN_PASSWORD=<pw> DATA_DIR=<dir>
  setsid nohup python3 -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8107 &`

## Wissens-Ablage
- Projekt-Memory/Tagebücher: `.openhands/memory/` – **bewusst NICHT im Repo**
  (dieses Repo ist öffentlich; interne Details bleiben lokal/privat). Wichtiges,
  public-taugliches landet in DIESER Datei. User-weite Memory:
  `/home/openhands/.openhands/memory/`.
- Prompt-Bibliothek (Rahmen bei jeder Aufgabe): `~/.openhands/skills/prompt-bibliothek/`.
