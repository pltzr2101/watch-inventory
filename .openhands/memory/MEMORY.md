# Watch-Inventory (pltzr2101) – Projekt-Memory

## Projekt & Workflow
- FastAPI + SQLite/SQLAlchemy + Jinja2 (Dark-UI, Vanilla-JS); Repo `pltzr2101/watch-inventory`.
- **Produktions-Workflow (Nutzer-Erlaubnis): nach grünen Gates DIREKT auf main committen+pushen**
  (kein PR); CI baut das Registry-Image. Portainer-Stack: Host-Port **3080**, `docker-compose.yml`
  + `.env.example` im Repo (kein `build:` im Stack).
- Gates immer alle: `python3 -m pytest tests/ -q` (rc=0) · `python3 -m ruff check app/ tests/` ·
  `python3 -m mypy app/` (35 Dateien) · `node --check app/static/app.js`.
- Lokal-Server zur Verifikation: `SESSION_SECRET=<32+> ADMIN_PASSWORD=<pw> DATA_DIR=/tmp/wi-v3
  setsid nohup python3 -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8107 &`
  (nohup ohne `setsid` stirbt am Call-Ende). Chromium-CDP-Checks: Port 9333; mobile Emulation:
  **`document.documentElement.clientWidth` als Viewport-Breite** (innerWidth lügt bei Overflow).

## Auth
- Zwei-Feld-Login: Username `admin` über Env **`APP_USERNAME`** (Default admin, KEIN Secret),
  Passwort `ADMIN_PASSWORD[_HASH]`. Fehlerkontrakt: falscher Login → **401 mit gerenderter
  Fehlerseite** (`flash-error`-div), kein Session-Cookie. Tests: `TEST_USERNAME`/`TEST_PASSWORD`
  in `tests/conftest.py`; `login(client)`-Helfer pflegt beide Felder.

## Datenmodell (V8-Stand, main=725e435)
- `BoxPurchase`: Edit `POST /boxes/purchases/{id}`, Delete `…/{id}/delete`. **Dict aus
  `_recent_purchases` braucht `"id"`** – sonst leere Action-URLs → FastAPI-404 (Regressionstest
  greift die Page-URLs: `r'action="/boxes/purchases/(\d+)"'`).
- `BoxSale` (Box-Einzelverkauf): `stock = eingekauft − Uhren-SOLD − Σ box_sales`;
  `/boxes/sales` (lehnt Über-Bestand ab), `/boxes/sales/{id}/delete`; KPIs
  `box_sold_count`/`box_revenue` in `services/analytics.py`, Karte „Boxen verkauft (Zeitraum)“.
- Analytics: `_since(period)` = **None bei „Alle“** → nie `>= None` bauen (SQLAlchemy-ArgumentError).
- Mobile: `.tbl-stack`-Karten ≤640px, `data-label`-tds,16px-Inputs (iOS-Zoom-Schutz),
  `data-confirm`-Dialoge (CSP-konform, delegiert in `app.js`).
- `.demo/` ist gitignored (Screenshots für den Nutzer landen dort).

## Ablage & Tools
- Tagebücher `.openhands/memory/YYYY-MM-DD.md`: **gitignored (Z.16) – überlebt Workspace-Verlust
  nicht**; Wichtiges zusätzlich in die User-Memory spiegeln (`/home/openhands/.openhands/memory/`).
- **Prompt-Bibliothek**: `~/.openhands/skills/prompt-bibliothek/references/*.md` (bugfix.md,
  feature-umsetzung.md, audit.md …) – bei Aufgaben als Rahmen nutzen (Nutzer-Pflicht).
