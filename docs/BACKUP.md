# Daten, Backups, Updates, Restore

## Wo liegen die Daten?

Alles Nutzdaten-relevante liegt im Volume **`watch-inventory-data`**, im
Container als `/data` gemountet (`DATA_DIR=/data`):

```
/data/
├── watch-inventory.sqlite3   # Datenbank (Uhren, Status-Historie, Import-Vorschauen,
│                             #          Listing-Entwürfe, Bild-Metadaten)
├── images/                   # Originaldateien (unverändert) + export-fähige Derivate
├── tmp/                      # kurzlebige Ablagen (Konvertierung/ZIP-Vorbereitung)
└── listing_templates/        # (optional) eigene Vorlagen, falls LISTING_TEMPLATE_DIR genutzt
```

Der Container-Layer enthält **keine** Nutzdaten und kann jederzeit ersetzt
werden. Kein State in Umgebungsvariablen außer den Pfad-/Limit-Einstellungen.

## Backup

Empfohlen: **regelmäßiger Snapshot des benannten Volumes** (Datenbank ist klein,
Bilder wachsen langsam):

```bash
# 1. Konsistenz: Container kurz anhalten (Stack → Stop) oder SQLite online kopieren:
docker run --rm -v watch-inventory-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/watch-inventory-$(date +%F).tar.gz -C /data .

# Variante nur Datenbank (während laufendem Betrieb):
docker run --rm -v watch-inventory-data:/data -v "$PWD":/backup alpine \
  sh -c "apk add --no-cache sqlite >/dev/null 2>&1; \
         sqlite3 /data/watch-inventory.sqlite3 '.backup /backup/db.sqlite3' && \
         cp /backup/db.sqlite3 /backup/watch-inventory-db-$(date +%F).sqlite3"
```

Bilder gehören zwingend ins Backup (Originale sind nicht rekonstruierbar).
Backups verschlüsselt/außerhalb des LXC verwahren (Enthält interne Notizen
und Einkaufspreise – vertrauliche Daten!).

## Restore

```bash
# Stack stoppen, dann:
docker run --rm -v watch-inventory-data:/data -v "$PWD":/backup alpine \
  sh -c "rm -rf /data/* && tar xzf /backup/watch-inventory-YYYY-MM-DD.tar.gz -C /data"
# Rechte für UID 10001 prüfen (Image läuft nicht als root):
docker run --rm -v watch-inventory-data:/data alpine chown -R 10001:10001 /data
# Stack wieder starten.
```

Bei Bind-Mount statt Volume: Host-Pfad für UID 10001 beschreibbar machen
(`chown -R 10001:10001 /pfad`) – sonst scheitert SQLite-Zugriff.

## Updates

1. Neues Image-Tag in die Registry pushen (`docs/ENTWICKLUNG.md`).
2. Portainer-Stack bearbeiten → `TAG` erhöhen → Update.
3. Nach dem Start: `/healthz` und Login prüfen; bei Schemaänderungen genügt das
   Starten der neuen Version (Migration erfolgt beim Start).

Vor jedem Update ein Backup ziehen (s. o.).

## Datenmodell-Migration

Das Schema entsteht beim Start via `create_all`/Initialisierung. Für spätere
Änderungen: versionierte Migrationen ergänzen, bevor Spalten umbenannt oder
entfernt werden (Bestandsdaten sind produktiv).
