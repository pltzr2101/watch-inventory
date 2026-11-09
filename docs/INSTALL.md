# Installation & Portainer-Deployment

Das Deployment folgt dem Portainer-Muster: **Stack aus einer Container-Registry,
kein Build im Stack.**

## Voraussetzungen (Docker-in-LXC)

- Docker Engine im LXC-Container (LXC mit `nesting=1`; Privileged oder mit
  passenden AppArmor/Fuse-Features, sonst schlagen Storage- oder Netzwerk-
  Treiber fehl).
- Portainer mit aktivierter Compose-Unterstützung.
- Ein freier Host-Port – **Standard `3080`** (`3000` ist auf dem Ziel-LXC
  belegt). Über `HOST_PORT` änderbar.
- Registry, die das Image aufnimmt (GHCR, Docker Hub oder selbst gehostet z. B.
  Harbor/Registry:2).

Bekannte Einschränkungen in LXC: OverlayFS-Performance und Netzwerk-Throughput
sind geringer als auf Bare-Metal; Healthcheck und Watchtower funktionieren
üblicherweise unauffällig. Bei Problemen mit Overlay Storage in LXC ggf.
`storage-opt` bzw. einen anderen Storage-Treiber prüfen.

## 1. Image vorbereiten

Image bauen, versionieren und in die Registry pushen: **`docs/ENTWICKLUNG.md`**.
Ergebnis ist ein Registry-Pfad wie z. B.
`ghcr.io/pltzr2101/watch-inventory:latest` (Stack-Standard) bzw.
versioniert `ghcr.io/pltzr2101/watch-inventory:0.1.0`.

## 2. Secrets erzeugen (lokal, nie im Repo)

```bash
# Session-Secret:
openssl rand -hex 32
```

**Admin-Login – Hauptweg (empfohlen): Klartext-Passwort.** Als
`ADMIN_PASSWORD` in die Stack-Variablen eintragen (Schritt 3); die App
hasht es beim Start selbst (Argon2). Kein Hash, kein Kopieren von
`$`-Zeichen – so wie bei Grafana, Portainer & Co.

**Alternative (Härtung): Argon2-Hash** statt Klartext, erzeugen auf dem
Server direkt aus dem Image:

```bash
docker run --rm ghcr.io/pltzr2101/watch-inventory:latest \
  python -m app.security 'mein-starkes-passwort'
```

> **Wichtig:** Der Hash darf **nur als Stack-Variable** eingetragen
> werden, **nie direkt in die compose-YAML** – `$`-Zeichen werden dort
> als Variablen interpretiert und der Wert erreicht verstümmelt die App
> (`ist kein Argon2-Hash`). In die YAML eingeschrieben muss jedes `$`
> als `$$` geschrieben werden. Führendes Leerzeichen vor dem `docker
> run`-Befehl schützt das Passwort vor der Shell-History.

Beide Werte werden in Portainer als Stack-Umgebungsvariablen hinterlegt –
**nicht** in `docker-compose.yml`, **nicht** ins Repository.

## 3. Stack in Portainer anlegen

1. Portainer → **Stacks** → **Add stack**.
2. Name z. B. `watch-inventory`, Method **Web editor**.
3. Den Inhalt von `docker-compose.yml` (Repo-Wurzel) komplett einfügen.
4. Reiter **Environment variables** (bzw. „Environment settings"):
   - `IMAGE` = eigener Registry-Pfad, z. B. `ghcr.io/me/watch-inventory`
   - `TAG` = `latest` (Standard, wie beim Poker-Session-Manager: Watchtower
     zieht neue Builds automatisch). Für einen festen reproduzierbaren
     Stand stattdessen z. B. `0.1.0` eintragen.
   - `HOST_PORT` = `3080`
   - `SESSION_SECRET` = Wert aus Schritt 2
   - `ADMIN_PASSWORD` = dein Login-Passwort (einfachste Variante) –
     stattdessen auch `ADMIN_PASSWORD_HASH` = Hash aus Schritt 2 möglich
   - optional weitere Werte aus `.env.example` (dort Erklärung je Variable)
   - Alternative: **Load variables from file** mit einer lokal erstellten
     `.env` (Vorlage `.env.example` kopieren, Werte eintragen, Datei danach
     löschen oder außerhalb des Repos verwahren).
5. **Registry authentication**: Falls die Registry private ist, hier die
   Registry-Zugangsdaten (User/Token) eintragen – das sind die einzigen
   Registry-Credentials, und sie liegen nur in Portainer, nicht in der
   Compose-Datei. Bei öffentlichen Images weglassen.
6. **Deploy the stack**.

Das Design ist absichtlich *fail-fast*: Fehlt `SESSION_SECRET`, bricht
`docker compose` bereits beim Deploy mit einem Hinweis ab. Fehlen **beide**
Admin-Varianten (`ADMIN_PASSWORD` und `ADMIN_PASSWORD_HASH`), beendet sich der
Container sofort mit klarer Fehlermeldung (im Portainer-Log sichtbar; kein
unsicherer Start).

## 4. Prüfen

- Stack → Container: Status `healthy` (Healthcheck ruft `/healthz`).
- Browser: `http://<lxc-ip>:3080/` → Login-Seite.
- Log (Container → Logs): keine Secrets sichtbar.

## 5. Watchtower / Updates

Das Stack-Label

```yaml
com.centurylinklabs.watchtower.enable=true
```

markiert den Container für Watchtower (sofern Watchtower im Host-Betrieb mit
Label-Filter läuft, z. B. `--label-only`). Ablauf neuer Releases:

1. Der Workflow baut bei jedem `main`-Push automatisch ein neues
   `latest`-Image (Gates + Versionstage, siehe `docs/ENTWICKLUNG.md`).
2. In Portainer den Stack neu deployen („Update“) – bei eingetragenem
   `TAG=latest` wird das frische Image gezogen. Watchtower erledigt das
   automatisch, sofern aktiv.
3. Bei fest eingetragenem Versionstag den Wert erhöhen (z. B. `TAG=0.1.1`)
   und das neue Tag in die Registry pushen (`git tag v0.1.1`).

Der Standard ist bewusst `latest` (Muster wie Poker-Session-Manager);
der Tag bleibt konfigurierbar für reproduzierbare Stände und Rollbacks.

## 6. Erreichbarkeit / Reverse Proxy / VPN

- **Standard-Empfehlung:** Port nur im internen Netz bzw. per Firewall/VPN
  erreichbar machen. Keine Portfreigabe ins öffentliche Internet.
- Mit Reverse Proxy (z. B. Traefik/nginx) HTTPS terminieren und
  `COOKIE_SECURE=true` setzen; den Proxy so konfigurieren, dass er
  `http://watch-inventory:8080` anspricht (Service-Netz: internes
  Compose-Standardnetz).
- Die App kennt bewusst **keine** öffentlichen Routen: Alle Seiten, Uploads,
  Bildzugriffe und Exporte sind authentifiziert.

### Passwort-Manager (Google, Proton, iCloud-Schlüsselbund)

Das Login-Formular ist für Manager ausgelegt (`form` + `type="password"` +
`autocomplete="current-password"`), aber Speicherung und Erkennung hängen an
einer **sicheren Origin**: Über reinem `http://` (z. B. `192.168.x.x:3080`)
speichern iOS-Schlüsselbund und die gängigen Manager keine Zugangsdaten –
das Login wird dort schlicht nicht als solches angeboten. Vorgehen:

1. **HTTPS davor schalten**, z. B. mit Caddy auf dem LXC-Host (Port `3080`
   ist bereits für localhost erreichbar):
   `caddy reverse-proxy --from watch.example.de --to 127.0.0.1:3080`
   – Caddy holt automatisch ein Let's-Encrypt-Zertifikat (DNS-Eintrag muss
   auf den Host zeigen).
2. In den Stack-Variablen **`COOKIE_SECURE=true`** setzen (Session-Cookie
   wird dann nur noch über HTTPS mitgeführt).
3. Ohne öffentlichen DNS z. B. **Tailscale** (`.ts.net`-Domain inkl.
   Zertifikat) oder eine interne CA verwenden; ohne TLS bleiben
   Passwort-Manager-Integrationen eingeschränkt.

## Persistenz

Siehe `docs/BACKUP.md`. Kurzfassung: Das Volume `watch-inventory-data`
(`/data` im Container) enthält SQLite-Datenbank, Originale, Exporte und tmp –
ausschließlich dort liegen Nutzdaten; der Container-Layer ist entbehrlich
und jederzeit neu erzeugbar.

## Registry-Zugangsdaten sicher hinterlegen

- In Portainer beim Stack: „Registry authentication" (Credential-Helper /
  Docker-Config-Import). Zugangsdaten erscheinen weder in der Compose-Datei
  noch im Repository.
- Alternativ auf dem Host: `docker login <registry>` – gilt dann für den
  Host-Docker-Daemon (weiter reichend, Portainer-Credentials sind präziser).
- Tokens mit minimalem Rechtescope (nur `write:packages`/`push`).
