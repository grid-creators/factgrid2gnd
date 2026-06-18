# FactGrid to GND Conversion Tool

Konvertiert Personendatensaetze aus [FactGrid](https://database.factgrid.de/) (Wikibase) in GND-Normdatensaetze im MARC 21 XML-Format (`.mrcx`) fuer die Deutsche Nationalbibliothek.

FactGrid ISIL: **DE-4218**

**Demo:** https://factgrid2gnd.grid-creators.com/

## Architektur

```
fg2marc21/
├── backend/              # Flask REST API (Python)
│   ├── app.py            # API-Endpunkte
│   ├── converter.py      # Kernlogik: FactGrid → MARC 21
│   ├── utils.py          # SPARQL-Abfragen, GND-Lookup (lokal + API)
│   ├── factgrid_local.py # Lokaler FactGrid-Zugriff via SQLite
│   ├── mappings_config.py # Feld-Mappings und Konstanten
│   └── lobid_cache.db    # Auto-generierter Cache (lobid.org)
├── frontend/             # Angular 21 Web-UI
│   └── src/app/
│       ├── conversion/   # Konvertierungs-Komponente (inkl. Datenquellen-Umschalter)
│       └── services/     # API-Service
├── scripts/              # Datenbank-Build-Skripte
│   ├── build_gnd_db.py              # GND-Personendatenbank erstellen
│   ├── build_gnd_sachbegriff_db.py  # GND-Sachbegriffe-Datenbank erstellen
│   ├── build_factgrid_db.py         # FactGrid-Datenbank erstellen (Offline-Modus)
│   ├── extract_persons_from_dump.py # Personen (P2=Q7) + Stubs (Labels + Claims P76/P48) aus FactGrid-Dump extrahieren (zwei Durchgaenge)
│   ├── refresh_factgrid_db.sh       # Pipeline: Dump laden → Personen + Labels extrahieren → DB bauen
│   └── ...                          # Weitere Extraktions-/Vergleichsskripte
├── specs/                # Spezifikationen und Referenzdokumente
│   ├── Anforderungen GND_FG_neu.xlsx  # Anforderungsspezifikation
│   ├── GND_MARC_vollst_*.xlsx         # DNB PICA-zu-MARC Tabelle
│   └── *.pdf                          # MARC 21 Feldbeschreibungen
├── data/                 # Grosse Datendateien (nicht in Git)
├── gnd_persons.db        # Lokale GND-Personendatenbank (~470MB)
├── gnd_sachbegriffe.db   # Lokale GND-Sachbegriffe-Datenbank
└── factgrid.db           # FactGrid-Entitaeten fuer Offline-Modus
```

## Voraussetzungen

- Python 3.13+
- Node.js 20+
- Angular CLI 21+

## Installation

### Backend

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

pip install flask flask-cors requests lxml
```

### GND-Datenbanken (optional, empfohlen)

Fuer schnelle lokale GND-Abfragen statt langsamer API-Aufrufe:

```bash
cd scripts
python build_gnd_sachbegriff_db.py  # Sachbegriffe (~207.000 Datensaetze)
python build_gnd_db.py              # Personen (~5 Mio. Datensaetze)
```

- **Sachbegriffe**: Erfordert `data/authorities-gnd-sachbegriff_dnbmarc_20260217.mrc.xml`. Erstellt `gnd_sachbegriffe.db`.
- **Personen**: Erfordert `data/authorities-gnd-person_dnbmarc_20260217.mrc.xml` (~26GB). Erstellt `gnd_persons.db` (~470MB).

Ohne diese Datenbanken werden GND-Daten ueber die lobid.org- und d-nb.info-APIs abgefragt (langsamer).

Zusaetzlich wird `backend/lobid_cache.db` automatisch beim ersten Konvertierungslauf erstellt und speichert GND-Vorzugsbenennungen fuer Entitaeten, die in keiner lokalen Datenbank gefunden wurden.

### FactGrid-Datenbank (fuer Offline-Modus)

Fuer die lokale Datenquelle (ohne FactGrid-Server) gibt es zwei Wege:

**Empfohlen — Komplettpipeline (Download + Extraktion + DB-Build):**

```bash
bash scripts/refresh_factgrid_db.sh
```

Das Skript holt den aktuellsten Dump (`YYYY-MM-DD.json.gz`) von `https://database.factgrid.de/dumps/`, speichert ihn nach `data/dump.json.gz` (mit Integritaetscheck via `gzip -t`), extrahiert in zwei Durchgaengen alle Personen (P2=Q7) nach `data/subset_P2_Q7.json` und **minimale Stubs** (Labels + nur die Claims `P76` (GND-ID) und `P48` (Koordinaten)) fuer die referenzierten Familien-/Vornamen-, Orts- und Berufs-Items nach `data/subset_referenced_labels.json`, und baut `factgrid.db` aus beiden Dateien neu. Ohne diese Stubs blieben Feld 100 `$a` und andere Namens-/Orts-Felder im Local-Modus rohe QIDs (`Q23861, Q38602` statt `Berger auf Siebenbrunn, Franz von Paula`); ohne die P76/P48-Claims fehlten ausserdem GND-IDs in 551 und der Laendercode-Fallback (Nominatim) wuerde nicht greifen. Laufzeit insgesamt ~25 Minuten.

Der Job ist als Cron eingerichtet (`/etc/cron.d/factgrid-refresh`) und laeuft taeglich um **03:00 UTC**. Logs: `/var/log/factgrid-refresh.log`.

**Manuell (einzelne Schritte):**

```bash
cd scripts
python extract_persons_from_dump.py ../data/dump.json.gz   # → data/subset_P2_Q7.json + data/subset_referenced_labels.json
python build_factgrid_db.py ../data/subset_P2_Q7.json ../data/subset_referenced_labels.json  # → factgrid.db
```

`extract_persons_from_dump.py` streamt den Dump zweimal: Pass 1 schreibt alle Personen-Items (P2=Q7) und sammelt referenzierte QIDs (P247/P248/P82/P168/P83/P1372/P165 inkl. Qualifier). Pass 2 schreibt Stubs der Form `{id, type, labels, claims}` fuer genau diese QIDs, wobei `claims` nur die Properties `P76` und `P48` enthaelt (damit Orte/Berufe im Offline-Modus ihre GND-ID und ggf. Koordinaten behalten).

`build_factgrid_db.py` akzeptiert eine oder mehrere JSON-Dateien als CLI-Parameter und streamt sie der Reihe nach in dieselbe DB; ohne Parameter wird `data/2026-04-16.json` verwendet. Erstellt `factgrid.db` mit Tabellen fuer Entitaeten, Labels und GND-IDs.

### Frontend

```bash
cd frontend
npm install
```

## Starten

**Backend (Port 5000):**

```bash
cd backend
python app.py
```

**Frontend (Port 4200, Proxy auf Backend):**

```bash
cd frontend
ng serve
```

Anschliessend im Browser oeffnen: http://localhost:4200

Das Frontend leitet `/api`-Anfragen automatisch an das Backend weiter (konfiguriert in `frontend/proxy.conf.json`).

## Benutzung

1. **Datenquelle waehlen**: FactGrid Server (Live-Abfragen) oder Lokale Datenbank (Standard, Offline, erfordert `factgrid.db`).
2. **Konvertierungsoptionen einstellen** (vor dem Konvertieren):
   - **079 $q Teilbestandskennzeichen**: Eine oder mehrere Auswahlen — mit `+ Hinzufuegen` weiteren Eintrag anhaengen, `−` entfernt einen Eintrag (mindestens einer bleibt). Im erzeugten 079-Feld wird pro Eintrag ein eigenes `$q`-Subfeld emittiert.
   - **667 $a Redaktionelle Bemerkung**: Freitextfeld (Standard: "Historisches Datenzentrum Sachsen-Anhalt").
3. Eine oder mehrere FactGrid Q-IDs eingeben (z.B. `Q409`, `Q11298`), getrennt durch Komma, Leerzeichen oder Zeilenumbruch.
4. **Konvertieren** klicken.
5. In der Seitenleiste werden die konvertierten Datensaetze mit Statusanzeige aufgelistet:
   - Gruen = OK
   - Gelb = Warnungen (z.B. fehlende Individualisierungsmerkmale)
   - Rot = Fehler (Pflichtfelder fehlen)
   - Orange "Dublette"-Badge = gleiche QID oder GND-ID erscheint mehrfach in der Ergebnisliste
   - Gruenes ✓ / rotes ✗ als **Export-Indikator** rechts neben den Status-Infos (Tooltip nennt die konkreten Blockierungsgruende, siehe Abschnitt "Export-Sperren" unten).
   - Linke **Checkbox** pro Eintrag plus "Alle"-Master-Checkbox im Sidebar-Header zur Auswahl fuer selektiven Export.
   - **×-Button** rechts in jeder Zeile entfernt die Person aus der Ergebnisliste (Auswahl- und Export-Markierung werden mit aufgeraeumt).
6. Datensatz auswaehlen, um MARC-Felder zu bearbeiten, hinzuzufuegen oder zu entfernen. Subfeld-Codes sind frei editierbar.
7. Felder 550/551 ohne GND-Referenz ($0) werden dauerhaft gelb markiert ("ohne GND"-Badge), bis ein $0-Subfeld ergaenzt wird. **551-Felder ohne GND-ID werden beim Export ausgelassen** (im Editor bleiben sie sichtbar).
8. Bei mehreren GND-Alternativen pro Feld: Eintrag im Dropdown auswaehlen und **Speichern** klicken -- erst dann werden die Daten uebernommen und die Validierung aktualisiert.
9. Bei mehreren Geburts-/Sterbedaten in FactGrid: Gelb hervorgehobene Auswahl-Box **"Lebensdaten (548) — Auswahl"** direkt nach dem letzten 548 datl-Block im Datenfelder-Bereich.
   - **`$4=datl`** (nicht wiederholbar) und **`$4=datw`** (Wirkungsdaten-Fallback) nutzen **Radio-Buttons** — es kann nur **ein** Wert gewaehlt werden. Vorausgewaehlt ist der aus dem bevorzugten Rang abgeleitete Wert; er laesst sich aber jederzeit auf eine andere Datumskombination umstellen.
   - **`$4=datx`** (wiederholbar) nutzt **Checkboxen** (Mehrfachauswahl); jeder Haken erzeugt einen 548 datx-Block, jede Abwahl entfernt ihn.
10. Export als MARC 21 XML (`.mrcx`) — drei Buttons:
    - **Diesen Record exportieren** — den aktuell ausgewaehlten,
    - **Ausgewaehlte exportieren (N)** — alle in der Sidebar angekreuzten (`gnd_export_auswahl.mrcx`),
    - **Alle exportieren** — alle Records der Ergebnisliste.

    Buttons sind gesperrt, wenn ein Export-Kriterium fuer den betreffenden Record nicht erfuellt ist; ein gelber Hinweisblock unter den Buttons listet die Gruende. Felder mit leerem `$a` werden nicht exportiert, 551-Felder ohne GND-ID ebenfalls nicht.

### Export-Sperren

Die Export-Buttons sind nur aktiv, wenn fuer den/die betroffenen Records alle vier Kriterien erfuellt sind:

1. **Individualisierung 3/3** — mindestens drei Individualisierungsmerkmale vorhanden.
2. **Person hat noch keine GND-ID** — Feld 035 `$a` ist `(DE-588)null` (sonst existiert bereits ein GND-Datensatz und ein erneuter Import wird verhindert).
3. **Feld 043 `$c` enthaelt einen Wert** — Laendercode ist gesetzt.
4. **Wenn ein 550-Feld mit `$4=berc` existiert, muss es ein `$0=(DE-588)…`-Subfeld tragen** — der charakteristische Beruf benoetigt eine GND-Referenz.

## API-Endpunkte

| Methode | Pfad | Beschreibung |
|---------|------|--------------|
| `GET` | `/api/convert/<qid>?source=server` | Einzelne Q-ID konvertieren |
| `POST` | `/api/convert` | Mehrere Q-IDs konvertieren (`{"qids": [...], "source": "server"\|"local"}`) |
| `GET` | `/api/convert/stream?qids=Q1,Q2&source=local&field079q=d,s,f&field667a=...` | SSE-Stream fuer Konvertierung mit Fortschritt |
| `POST` | `/api/convert/validate` | MARC-Record validieren |
| `POST` | `/api/convert/export` | Records als MARC 21 XML exportieren |

Parameter: `source` (`"server"` oder `"local"`, Standard: `"local"`), `field079q` (Teilbestandskennzeichen — kommagetrennte Liste, Standard: `"d"`; jeder Wert wird zu einem eigenen `$q`-Subfeld im 079-Feld), `field667a` (Redaktionelle Bemerkung).

## Konvertierungslogik

### Verwendete FactGrid-Properties

| Property | Bedeutung |
|----------|-----------|
| P76 | GND-ID |
| P77 | Geburtsdatum |
| P38 | Sterbedatum |
| P82 | Geburtsort |
| P168 | Sterbeort |
| P83 | Ort der Adresse |
| P1372 | Wirkungsort |
| P165 | Beruf/Taetigkeit |
| P34 | Namensvariante (String) |
| P247 | Familienname |
| P248 | Vorname |
| P1504 | Wirkungsbeginn (548 $4=datw, nur ohne Lebensdaten) |
| P1505 | Wirkungsende (548 $4=datw, nur ohne Lebensdaten) |
| P392 | Datum der Disputation (548 $4=datw, nur ohne Lebensdaten) |

### Erzeugte MARC-Felder

| Feld | Inhalt |
|------|--------|
| 001 | FactGrid Q-Nummer |
| 003 | ISIL (DE-4218) |
| 005 | Zeitstempel |
| 008 | Feste Datenelemente |
| 035 | GND-Systemnummer |
| 040 | Katalogisierungsquelle |
| 042 | Authentifizierungscode |
| 043 | Laendercode (ISO 3166 / GND) |
| 075 | Entitaetentyp (piz) |
| 079 | Teilbestandskennzeichnung ($q als wiederholbares Subfeld; je Eintrag in der UI ein eigenes $q) |
| 100 | Bevorzugter Personenname (Nachname, Vorname &#152;Namenszusatz&#156;) |
| 400 | Abweichende Namensformen (aus P34, auf "Nachname, Vorname &#152;Namenszusatz&#156;" umformatiert) |
| 548 | Lebensdaten ($4=datl Jahresangaben, $4=datx exakte Daten mit Praezisionsabbildung); ersatzweise Wirkungsdaten ($4=datw) wenn keine Lebensdaten vorhanden |
| 550 | Beruf/Taetigkeit ($4 berc fuer hochgerankten Beruf vor $4 beru) |
| 551 | Geografische Bezuege (nur exportiert, wenn ein `$0=(DE-588)…` GND-ID-Subfeld vorhanden ist) |
| 667 | Redaktionelle Bemerkung ($a frei eingebbar) |
| 670 | Quellenangabe (FactGrid) |

### Bevorzugter Name (Feld 100)

Format: `Nachname, Vorname Namenszusatz`

Namenszusaetze (von, van, de, zu, della usw.) werden automatisch aus dem Familiennamen-Label (P247) extrahiert, hinter die Vornamen (P248) gestellt und mit MARC-Sortierzeichen (`&#152;`/`&#156;`) umschlossen. Falls P247/P248 fehlen, wird das deutsche Label ueber `reformat_name_to_preferred()` umformatiert.

| Familienname (P247) | Vornamen (P248) | Ergebnis (100 $a) |
|---|---|---|
| von Goethe | Johann Wolfgang | Goethe, Johann Wolfgang &#152;von&#156; |
| van der Waals | Johannes Diderik | Waals, Johannes Diderik &#152;van der&#156; |
| Mueller | Thomas | Mueller, Thomas |

### Abweichende Namensformen (Feld 400)

Feld 400 wird ausschliesslich aus der Property **P34** (Namensvariante, String) generiert. Jeder Wert wird nach dem gleichen Schema wie Feld 100 umformatiert (`Nachname, Vorname Namenszusatz`) -- das letzte Token gilt als Familienname, bekannte Praefixe (von, van, de, zu, de la, van der usw.) werden hinter die Vornamen gestellt:

| P34-Wert | Ergebnis (400 $a) |
|---|---|
| Johann Wolfgang von Goethe | Goethe, Johann Wolfgang &#152;von&#156; |
| Ludwig van Beethoven | Beethoven, Ludwig &#152;van&#156; |
| Goethe, Johann Wolfgang (bereits formatiert) | unveraendert |

Duplikate und der bevorzugte Name (Feld 100) werden herausgefiltert. Lebensdaten ($d) werden angehaengt, sofern vorhanden.

### Lebensdaten (Feld 548)

Feld 548 wird in drei Varianten erzeugt:

- **`$4=datl`** (Lebensdaten): Jahresangaben, z.B. `1749-1832`. Pflichtfeld, **nicht wiederholbar**.
- **`$4=datx`** (Exakte Lebensdaten): Tagesgenaue Angaben, z.B. `28.08.1749-22.03.1832`. Optional, wiederholbar.
- **`$4=datw`** (Wirkungsdaten): **Fallback nur, wenn weder Geburts- (P77) noch Sterbedatum (P38) vorhanden ist.** Quelle: Wirkungsbeginn/-ende (P1504/P1505) als Bereich plus datierte Einzelereignisse (z.B. P392 Datum der Disputation). Nicht wiederholbar.

**Praezisionsabbildung** (fuer beide Varianten):

| Wikibase-Praezision | Beispiel | datl | datx |
|---|---|---|---|
| Tag (11) | 28.08.1749 | 1749 | 28.08.1749 |
| Monat (10) | 06.1810 | 1810 | 06.1810 |
| Jahr (9) | 1810 | 1810 | 1810 |
| Jahrzehnt (8) | 1810er | 181X | 181X |
| Jahrhundert (7) | 19. Jh. | 18XX | 18XX |

**Datumsformate**: Unbekanntes Geburtsjahr → leer (z.B. `-1880`). Unbekanntes Sterbejahr → `XXXX` (z.B. `1892-XXXX`).

**Mehrere Datumsangaben in FactGrid**:

- `$4=datl` wird **immer** mit dem aus dem **preferred rank** abgeleiteten Bereich vorbefuellt (nie leer).
- Hat die Person mehrere Geburts-/Sterbedaten, werden alle Kombinationen (inkl. "unbekannt") als `date_alternatives` in einer gemeinsamen gelben Auswahl-Box **"Lebensdaten (548) — Auswahl"** angeboten, die inline im Datenfelder-Bereich direkt nach dem letzten 548 datl-Block (Fallback: letzter 548-Block) platziert wird.
  - **datl** und **datw** sind **Single-Select (Radio-Buttons)**, da beide Felder nicht wiederholbar sind. Die Vorauswahl ist der preferred-rank-Wert; ein anderer Wert ersetzt ihn ohne zusaetzlichen 548-Block.
  - **datx** nutzt **Checkboxen (Mehrfachauswahl)**. Pro Haken entsteht ein neuer 548 datx-Block; Abwahl entfernt den Block, der `date_alternatives`-tragende letzte Block bleibt mit leerem `$a` erhalten.
  - Die Warnung `Feld 548 (datx): bitte zutreffende Werte waehlen` verschwindet, sobald ein datx-Wert ausgewaehlt ist. Fuer datl gibt es keine Auswahlwarnung mehr (immer vorbefuellt).
- Felder mit leerem `$a` werden nicht exportiert.

### Beruf/Taetigkeit (Feld 550)

Pro Datensatz erhaelt **genau ein** Beruf den nicht wiederholbaren Code `$4 berc` ("charakteristischer Beruf"):

- Der in FactGrid als **preferred rank** markierte Beruf (sofern genau einer so markiert ist).
- Alternativ der **einzige** Beruf, falls nur einer vorhanden ist.

Der `berc`-Beruf wird im Export vor den `beru`-Berufen sortiert. Alle anderen Berufe erhalten `$4 beru`. Felder 550/551 ohne GND-Referenz ($0) werden im UI dauerhaft gelb markiert.

### Dublettenerkennung

- **Innerhalb eines Datensatzes**: Gleiche GND-ID ($0) oder gleicher Name ($a) in den Feldern 400, 550, 551, 670 loest eine Dubletten-Warnung aus.
- **Ueber Datensaetze hinweg**: Gleiche FactGrid-QID (Feld 001) oder gleiche GND-ID (Feld 035) in der Ergebnisliste werden in der Seitenleiste mit einem orangefarbenen "Dublette"-Badge gekennzeichnet.

### Laendercode-Ermittlung (Feld 043)

Der Laendercode wird ueber eine Prioritaetskette bestimmt:

1. Wirkungsort (P1372)
2. Ort der Adresse (P83)
3. Sterbeort (P168)
4. Geburtsort (P82)

Fuer jeden Ort wird zuerst versucht, den Code aus dem verknuepften GND-Datensatz zu extrahieren. Falls kein GND-Eintrag vorhanden ist, werden Geokoordinaten ueber Nominatim aufgeloest.

### GND-Anzeigenamen

Die Felder 550 (Beruf) und 551 (Orte) verwenden bevorzugte Namen aus der GND. Lookup-Reihenfolge:

1. Lokale GND-Sachbegriffe-Datenbank (`gnd_sachbegriffe.db`)
2. Lokale GND-Personendatenbank (`gnd_persons.db`)
3. Lobid-Cache (`backend/lobid_cache.db`)
4. lobid.org API (Fallback, Ergebnis wird im Cache gespeichert)

### Validierung

- Pflichtfelder: 001, 003, 005, 008, 035, 040, 043, 075, 079, 100, 548
- Individualisierung Level 1: mindestens 3 Merkmale, davon min. 1 aus Gruppe 1. Die Zaehlung ist **merkmal-basiert**: Feld 548 wird nach `$4`-Subtyp aufgeschluesselt, sodass nicht-exakte Lebensdaten (`datl`), exakte Lebensdaten (`datx`) und Wirkungsdaten (`datw`) jeweils als **eigenes** Merkmal zaehlen.
  - **Gruppe 1**: `548-datl` (Lebensdaten), `548-datw` (Wirkungsdaten), 550 (Beruf)
  - **Gruppe 2**: `548-datx` (exakte Lebensdaten), 551 (geografischer Bezug)
  - Beispiel: Eine Person mit tagesgenauem Geburts- und Sterbedatum erzeugt 548 datl **und** 548 datx und erhaelt dafuer 2 Merkmale (datl in Gruppe 1, datx in Gruppe 2).

## Externe APIs

- **FactGrid SPARQL**: `https://database.factgrid.de/sparql` -- Entity-Daten
- **lobid.org**: `https://lobid.org/gnd/{id}.json` -- GND bevorzugte Namen
- **d-nb.info**: `https://d-nb.info/gnd/{id}/about/marcxml` -- GND MARC-Records
- **Nominatim**: Reverse Geocoding fuer Laendercode-Fallback
