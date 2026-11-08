# Wecatalog.cn-Integration (standardmäßig deaktiviert)

## Status

Die Integration ist hinter dem Feature-Flag **`WECATALOG_ENABLED`**
(Standard: **`false`**) eingebaut. Die App ist ohne sie vollständig nutzbar:
manuelle Pflege, Fotos, Anzeigen und ZIP-Export laufen unabhängig davon.

Sie dient im MVP ausschließlich dazu, **einen autorisiert freigegebenen
Katalogeintrag manuell** einem internen Datensatz zuzuordnen oder daraus einen
neuen internen Datensatz anzulegen – mit Vorschau und ausdrücklicher
Bestätigung.

## Was technisch umgesetzt ist

- **Importweg**: Eingabe der Katalog-URL bzw. eines Eintrags im Import-Formular;
  optionaler Abruf über einen konfigurierbaren Endpunkt (`WECATALOG_API_URL`)
  mit Timeout (`WECATALOG_TIMEOUT_SECONDS`), stufenweisem Rate-Limit
  (`WECATALOG_MAX_FETCHES_PER_HOUR`, pro Session/datenbankgeprüft) und
  manuellem Auslösen (kein Hintergrund-Polling).
- **Preisseperation**: `strip_prices()` entfernt Preis- und Währungsangaben
  vor dem Speichern – `price`, `prices`, `amount`, `currency` u. ä. landen nie
  in einem Payload (eigene Einkaufs-/Verkaufspreise werden nie aus der Quelle
  befüllt oder überschrieben).
- **Vorschau statt Überschreiben**: Jeder Abruf erzeugt einen
  `ImportPreview`-Datensatz (Diff Bestand ↔ Quelle). Übernahme nur über
  `/imports/{id}/confirm`. Titel/Marke/Modell werden **nie** automatisch
  überschrieben; eigene Beschreibungen bleiben erhalten, solange der Nutzer sie
  bearbeitet hat (Snapshot-Diff-Logik).
- **Kennzeichnung**: `provenance`, `source_url`, `imported_at`,
  `source_fields`, `source_content` (+ gestaffelte Bilder mit
  `ImageSource.WECATALOG`). Importierte Inhalte sind intern sichtbar, aber erst
  nach Prüfung/Bestätigung für Anzeigen verwendbar (`import_confirmed_at`).
- **Bilder**: Originale bleiben unverändert. Bearbeitungen (Helligkeit,
  Kontrast, Zuschnitt, Rotation) sind für Katalogbilder gesperrt, solange
  `WECATALOG_ALLOW_IMAGE_EDIT=false` (Standard) – der Erlaubnisumfang für
  Bildveränderungen gilt damit als **nicht geklärt** und wird nicht stillschweigend
  angenommen.
- **Sicherheit**: Nur autorisierter Zugriff, keine Umgehung von Anmeldung,
  CAPTCHA, Rate-Limits o. ä.; keine Zugangsdaten im Repo (Token nur über
  `WECATALOG_API_TOKEN`); Quellinhalte überschreiben nie Einkaufspreise,
  Verkaufspreise oder Notizen; Quelldaten werden vor Verwendung bereinigt.

## Was vor der Aktivierung noch zu klären ist

Die Einverständniserklärung des Betreibers wird mitgeliefert, ist aus der
Integration aber nicht in vollem Wortlaut technisch ableitbar. Vor
`WECATALOG_ENABLED=true` müssen schriftlich/aus der Erklärung bestätigt werden:

1. **Welche Inhalte genau** übernommen werden dürfen (Titel, Beschreibungen,
   technische Daten, Bilder – einzeln abgegrenzt; Standardannahme im MVP ist
   der enge Rahmen: Beschreibung + Daten + Bilder, **keine Preise**).
2. **Der vorgesehene Zugang**: offizielle API (Endpunkt, Auth-Verfahren,
   Nutzungsbedingungen) oder Export-Funktion des Betreibers. Ohne bestätigten
   Endpunkt bleibt `WECATALOG_API_URL` leer und der Abruf meldet einen
   verständlichen Fehler (Flag bleibt deaktiviert).
3. **Rate-/Nutzungsgrenzen** des Betreibers (z. B. Abrufe pro Stunde/Tag);
   bis dahin gilt der konservative eigene Limitwert.
4. **Bildbearbeitung**: ob Zuschneiden/Konvertieren/Komprimieren der
   Katalogbilder erlaubt ist. Bis dahin `WECATALOG_ALLOW_IMAGE_EDIT=false`
   belassen.
5. **Aufbewahrung/Weiterverwendung**: Dauer der internen Speicherung und ob
   übernommene Bilder später öffentlich ausgeliefert werden dürfen (die
   öffentliche Auslieferung ist ohnehin Teil späterer Ausbaustufen und hier
   nicht aktiv).
6. **Urheber-/Markenhinweise**, die bei Übernahme mitgeführt werden müssen.

Ist eines davon ungeklärt, bleibt das Flag aus – die Integration ist dann
bewusst nicht nutzbar (sichere, verständliche Fehlermeldung statt heuristischem
Scraper).

## Konfiguration

| Variable | Standard | Bedeutung |
|---|---|---|
| `WECATALOG_ENABLED` | `false` | Feature-Flag gesamt |
| `WECATALOG_BASE_URL` | `https://www.wecatalog.cn` | Basis-URL (Validierung der Eingaben) |
| `WECATALOG_API_URL` | leer | Autorisierter API-/Export-Endpunkt (Pflicht bei Aktivierung) |
| `WECATALOG_API_TOKEN` | leer | Nur falls der Betreiber Zugang bereitstellt |
| `WECATALOG_TIMEOUT_SECONDS` | `10` | Abruf-Timeout |
| `WECATALOG_MAX_FETCHES_PER_HOUR` | `6` | Eigenes Abruflimit |
| `WECATALOG_ALLOW_IMAGE_EDIT` | `false` | Katalogbilder bearbeitbar? |

Es gibt bewusst **keine** automatische Synchronisation: Quelländerungen
erscheinen ausschließlich als erneute Importvorschau; entfernte Quelldaten
löschen nichts, geänderte überschreiben nichts ohne Bestätigung.

## Tests

`tests/test_wecatalog.py` sichert u. a.: Preisseperation, Vorschau/Bestätigung,
kein Überschreiben interner Felder, gesperrte Katalogbild-Bearbeitung, Feature-
Flag (abgeschaltet = keine Abrufe/kein Router), sichere Fehler bei ungültigen
oder unerreichbaren Quellen, keine Veröffentlichung ungeprüfter Inhalte in
Anzeigen. `tests/test_listing.py` ergänzt: unbestätigter Katalogtitel wird
für Anzeigen blockiert.
