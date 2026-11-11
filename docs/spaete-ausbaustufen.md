# Spätere Ausbaustufen (bewusst nicht Teil des MVP)

## 4. Öffentliche, schreibgeschützte Bestandsseite

**Vorbereitungen, die jetzt schon getroffen wurden:**

- `public_approved` (Boolean) pro Uhr ist die spätere Freigabe-Schwelle;
  aktuell wertet **keine** Route es aus, es existiert keine öffentliche Route
  und kein öffentlicher API-Endpunkt.
- Vertrauliche Felder (`purchase_price_cents`, `additional_costs_cents`,
  `target_price_cents`, `internal_notes`, `source_content`, Quellpreise) liegen
  in eigenen Spalten und werden von internen Anzeigen-/Exportpfaden bereits
  explizit nie ausgegeben. Eine öffentliche Ansicht darf nur eine eigene,
  explizite Whitelist-Vorlage (`public_*.html`) mit freigegebenen Feldern
  verwenden – internationale Routen (`/watches/...`) dürfen dafür nicht
  wiederverwendet werden.
- Statusstruktur (`in_china` vs. `in_germany` etc.) erlaubt die geforderte
  Trennung „In Deutschland" / „In China" über Filter auf demselben Feld.
- `box_option` (`none`/`box`/`optional`, NULL = nicht angegeben) steht als
  fachlich öffentlich darstellbares Feld zur Verfügung; die
  Anzeigengenerator-Vorlagen nutzen es bereits intern, eine öffentliche
  Whitelist kann es später 1:1 übernehmen („Box optional (+50 €)“ ist das
  statische Label aus dem Setup-Bestand).
- Import-Kennzeichnung (`provenance`, `source_fields`, `source_content`,
  `import_confirmed_at`, `ImageSource.WECATALOG`): öffentlich ausgeliefert
  werden nur Felder/Bilder, die *sowohl* `public_approved` *als auch*
  (bei Importinhalten) ausdrücklich bestätigt sind *und* vom konfigurierten
  Nutzungserlaubnis-Umfang gedeckt sind. Einkaufspreise, Notizen, Quellpreise
  und nicht freigegebene Quelldaten bleiben strukturell ausgeschlossen.

**Offene Arbeitsschritte:** Freigabe-UI (Checkbox je Uhr/Feld), öffentliche
read-only Route + Vorlagen mit fester Whitelist, optionale Cache-/ETag-Logik,
ggf. Pooling der freigegebenen Fotos in eigene Derivate.

## 5. Verkaufs-, Deckungsbeitrags- und Lagerdauer-Analysen

**Vorbereitungen, die jetzt schon getroffen wurden:**

- `created_at`, `imported_at`, `status_history` (JSON-Log mit Statuswechseln),
  `sold_at`, `sale_price_cents` sowie die Differenz `sale - (Einkauf +
  Zusatzkosten)` speichern alle Zeitpunkte und Beträge bereits vollständig
  (Beträge als Integer-Cent, keine Fließkommas).
- Statuswechsel werden beim Ändern protokolliert, sobald die Architektur es
  kostengängig zulässt (`status_history`), sodass Lagerdauer je Status
  rechenbar ist.

**Offene Arbeitsschritte:** aggregierende read-only Queries (Deckungsbeitrag je
Uhr/Zeitraum, Lagerdauer bis Verkauf, Bestandsverteilung), Berichts-UI
ausschließlich hinter der Authentifizierung, ggf. Export als CSV. Keine
externen Dienste nötig.

## Grenzen, die eingehalten werden müssen

- Keine öffentliche Bestands-API, solange nicht Feature 4 bewusst gestartet wird.
- Keine Shop-, Warenkorb-, Checkout- oder Zahlungsfunktionen.
- Import-Synchronisation bleibt manuell (Vorschau + Bestätigung), nie
  automatisch-destruktiv.

## Umsetzungsstand (2026-11-09)

- [x] **Funktion5 – Analyse-Dashboard „Endausstattung“** umgesetzt:
  - Custom-Zeitraum `?von=…&bis=…` (Chip „Eigener Zeitraum“, serverseitige
    Datumsvalidierung mit Klartext-Fehler)
  - KPI „Deckungsbeitrag (Zeitraum)“ (= Summe `margin_eur`, keine Zweit-Definition)
  - Verkaufsliste (`.table`, clientseitig sortierbar: Datum, VK, EK, DB, DB %, Haltedauer)
  - CSV-Export `GET /auswertung/verkaeufe.csv?…` – nur mit Session-Auth,
    CSV-Injection-Schutz (`=+-@` → Apostroph), keine internen Notizen/Spalten
  - Bestandsalter-Buckets aktiver Uhren (0–30 / 31–90 / 91–180 / ab181 Tage)
  - Top-Marken (Verkäufe, Umsatz, Ø DB je Marke)
  - KPI-/Panel-Auswahl „Ansicht anpassen“ (localStorage, „Ansicht zurücksetzen“)
- [ ] **Funktion4 – Öffentliche, schreibgeschützte Bestandsseite** (in DE / in CN):
  nur `public_approved`-Felder; niemals Einkaufspreise, interne Notizen,
  Quell-Daten oder nicht freigegebene Katalogbilder; eigene read-only-Route.
