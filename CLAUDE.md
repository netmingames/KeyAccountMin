# KeyAccountMin (Sid)

**Spitzname:** Sid
**Sektion:** AI Administration
**Icon:** 💼
**Beschreibung im Center:** Steam-Seiten-Pflege bei neuen Spielen — Store-Texte, Assets, Releases.

## Was Sid ist (und was nicht)

Sid ist die zentrale **Werkbank** zur Pflege von Marketing- und Listing-Inhalten
fuer alle Plattformen, auf denen unsere Spiele liegen: Steam, Microsoft Store,
XBox, Google Play (Android) — und perspektivisch alles, was als "Kunde" oder
"Plattform" hinzukommt.

Konkret: ein Web-Tool mit Tab-Hierarchie, in dem pro Plattform pro Spiel
mehrsprachige Texte gepflegt, per Claude-CLI uebersetzt und als
plattformkompatible Datei (z.B. Steam-JSON) exportiert werden.

**Sid ist KEIN Auto-Polling-Agent** wie Serverin oder Lisbeth. Sid ist ein
**interaktives Tool**, das Thomas oeffnet, wenn er Listings pflegen will. Der
Status im Center bleibt stabil "Online", ohne Heartbeat-Pflicht.

## Tab-Hierarchie

```
Plattform-Tabs    Steam | Windows | XBox | Android | (+) [neuer Reiter]
   Item-Tabs      Passage 5 | (+) [neues Spiel/Kunde]
       Sub-Tabs   Inhalt | Grafiken | (kuenftig: Achievements, News, ...)
           Inhalt: Sprachenwahl + Felder (DE = Master) + Uebersetzungs-Button
                   + Export als Steam-JSON / Plattform-spezifisch
           Grafiken: Asset-Verwaltung mit Vorschau (Header, Capsule, ...)
```

Die oberste Ebene ist bewusst **plattformneutral** — perspektivisch koennen
hier auch Direkt-"Kunden" liegen (Publisher, Distributor), nicht nur
Storefronts.

## Datenmodell

Daten liegen als JSON-Files unter `data/` — keine DB. Versionierbar via Git,
direkt diffbar.

```
data/
  steam/
    1141975_passage5/
      meta.json              # AppID, Name, Plattform, Schema-Version
      master_de.json         # Deutscher Master (Single Source of Truth)
      translations/
        en.json
        fr.json
        ja.json
        ...
      assets/                # Header, Capsule, Library, Screenshots
      exports/               # generierte Steam-Upload-JSONs
  windows/
  xbox/
  android/
  _samples/
    storepage_1141975_all.json   # echter Steam-Download als Referenz
```

## Steam — Was wir wissen

- **Offizieller Upload-Mechanismus:** Im Steam-Partner-Backend gibt es unter
  "Lokalisierung" einen JSON-Download/-Upload. Das ist der einzige offizielle
  Massen-Update-Weg.
- **Format:** siehe `data/_samples/storepage_1141975_all.json` — Top-Level
  `itemid` + `languages.<langcode>.app[content][...]` mit Feldern wie
  `about`, `short_description`, `sysreqs.windows.min.osversion`, ...
- **Sprachcodes (Steam-spezifisch):** `english`, `french`, `italian`, `german`,
  `spanish`, `brazilian`, `bulgarian`, `tchinese`, `schinese`, `danish`,
  `finnish`, `greek`, `indonesian`, `japanese`, `koreana`, `dutch`, `norwegian`,
  `polish`, `portuguese`, `romanian`, `russian`, `swedish`, `latam`, `thai`,
  `czech`, `turkish`, `ukrainian`, `hungarian`, `vietnamese`.
- **Early-Access-Felder:** sind im Lokalisierungs-JSON NICHT enthalten. Steam
  hat dafuer einen eigenen Backend-Bereich (Q&A). Wir verwalten sie in Sid
  als separaten Block, Export ist ein separates Textbundle pro Sprache zum
  Copy/Paste.
- **Public API zum Schreiben:** existiert nicht. Phase 3: Browser-Automation
  via Playwright gegen partner.steamgames.com (Login + 2FA noetig).

## Uebersetzung

- **Engine:** Claude CLI im Headless-Modus (`claude -p "..."`) — laeuft ueber
  das Claude-Code-Abo, **keine separate Anthropic-API-Rechnung**.
- **Glossar:** pro Spiel pflegbar (`data/<plattform>/<item>/glossary.json`)
  mit Begriffen, die nicht uebersetzt werden duerfen ("netmin", "Passage 5",
  Spielmechanik-Namen wie "Streak").
- **Stale-Detection:** Aenderung am DE-Master setzt fuer alle abgeleiteten
  Sprachen einen `stale=true`-Flag. UI markiert betroffene Sprachen rot.
- **Style-Hinweise:** Neutraler Marketing-Ton, du-Form bei DE, "you" bei EN.
  Anpassbar pro Sprache via `style.json`.

## Tech-Stack

- **Backend:** Python + FastAPI (passt zum Center-Stack)
- **Frontend:** HTMX + vanilla JS, kein React
- **Storage:** JSON-Files
- **Uebersetzung:** subprocess auf `claude` CLI
- **Port:** 5003 (5000=Center, 5001=Lisbeth, 5002 frei gehalten)
- **Service:** Scheduled Task `Sid_KeyAccountMin`, Watchdog-Eintrag
- **Repo:** TBD (entweder eigenes `KeyAccountMin` auf netmingames, oder
  zunaechst Subordner im Center-Repo bis Phase 1 stabil)

## Workflow Phase 1 (Passage 5 Steam)

1. **Import:** `data/_samples/storepage_1141975_all.json` einlesen, DE-Texte
   nach `master_de.json`, EN nach `translations/en.json`, restliche Sprachen
   leer angelegt.
2. **DE pflegen:** Thomas editiert deutsche Texte in der UI. Aenderungen
   markieren Sprachen als stale.
3. **Sprachen auswaehlen:** Pro Spiel einstellbar, welche Sprachen Sid
   pflegen soll (29 Steam-Sprachen verfuegbar).
4. **Uebersetzen:** Pro stale-Sprache Button "Uebersetzen". Claude CLI wird
   pro Feld einmal aufgerufen, Glossar + Style fliessen in den Prompt.
5. **Pruefen:** UI zeigt DE | Zielsprache nebeneinander, Thomas kann
   nachjustieren. Edits sind permanent (ueberschreiben Auto-Translation).
6. **Exportieren:** Steam-kompatible JSON in `exports/<datum>.json`. Manueller
   Upload im Partner-Backend (bis Phase 3).
7. **Early-Access-Bundle:** Separater Export als Textdatei mit allen
   Q&A-Antworten pro Sprache zum Copy/Paste.

## Wichtige Pfade

- **Quelldatei (Sample):** `data/_samples/storepage_1141975_all.json`
- **Roadmap:** `ROADMAP.md`
- **Polling:** keins (Sid pollt nichts)
- **Heartbeat:** kein automatischer (interaktives Tool)

## Status (08.05.2026)

Phase 1 in Setup. Epic: NT-328. Subtasks werden parallel angelegt.
