# Sid / KeyAccountMin — Roadmap

Lebende Datei. Reihenfolge ist Vorschlag, jede Phase wird einzeln abgenommen
bevor die naechste startet. Wenn Thomas in einer Phase mehr will, packen wir
es rein. Wenn etwas wegfaellt, streichen wir's.

---

## Phase 0 — Setup (heute, 08.05.2026)

- [ ] Verzeichnisstruktur und CLAUDE.md (✓ angelegt)
- [ ] Jira-Epic NT-328 wiederverwenden + neuer Scope (in Arbeit)
- [ ] Sample-JSON eingefroren unter `data/_samples/storepage_1141975_all.json`
- [ ] Glossar-Format spezifiziert
- [ ] FastAPI-Skelett + Health-Endpoint auf Port 5003
- [ ] Sid-Tile im Center mit URL versehen + Polling auf `enabled: false`

## Phase 1 — MVP Passage 5 Steam

Ziel: Thomas kann fuer Passage 5 alle Sprachen pflegen, uebersetzen lassen
und ein Steam-konformes JSON exportieren.

- [ ] Import-Adapter fuer `storepage_<appid>_all.json`
- [ ] Datenmodell `master_de.json` + `translations/<lang>.json` mit Stale-Flag
- [ ] Drei-Ebenen-Tab-UI (Plattform | Item | Sub-Tab)
- [ ] Feld-Editor fuer Inhalt (Kurzbeschreibung, About, Sysreqs)
- [ ] Sprachauswahl-Modal (29 Steam-Sprachen, persistiert pro Item)
- [ ] Glossar-Editor pro Spiel
- [ ] Uebersetzungs-Engine: Claude-CLI-Wrapper mit Prompt-Template
- [ ] "Uebersetzen"-Button pro Sprache, Fortschritts-Anzeige
- [ ] DE/Sprache-Diff-View
- [ ] Export Steam-JSON
- [ ] Early-Access-Block (Felder + Text-Export pro Sprache)
- [ ] Item anlegen / loeschen / umbenennen
- [ ] Plattform-Reiter anlegen (vorerst nur "Steam" aktiv)

## Phase 2 — Passage 5 Grafiken

- [ ] Asset-Verwaltung pro Spiel: Header (460x215), Capsule klein/mittel/gross,
  Library (600x900, 1920x620), Hintergrund, Logo, Screenshots
- [ ] Vorschau in der UI mit Sollgroessen-Hinweis
- [ ] Asset-Versionierung (alte Versionen bleiben aufrufbar)
- [ ] Trailer-Verwaltung (URL/Embed)

## Phase 3 — Achievements

Thomas hat es schon erwaehnt: Achievements sind ein wichtiger Block der
parallel zum Listing gepflegt werden muss.

- [ ] Achievement-Datenmodell (ID, Name, Beschreibung, Icon, Hidden-Flag)
- [ ] Multi-Sprach-Pflege analog zu Listings
- [ ] Icon-Verwaltung (locked/unlocked, 256x256)
- [ ] Export im Steam-Achievements-Format
- [ ] (spaeter) Direktupload via Playwright

## Phase 4 — Steam-Direktupload

- [ ] Playwright-Setup gegen partner.steamgames.com
- [ ] Login + 2FA-Codeflow (interaktiv beim ersten Mal, danach Cookie-Reuse)
- [ ] Lokalisierung-Tab automatisch fuellen + JSON-Upload klicken
- [ ] Asset-Upload-Automat (Header, Capsule, ...)
- [ ] "Vorschau" automatisch oeffnen und Screenshot des Resultats
- [ ] Audit-Log: was wurde wann fuer welches Spiel hochgeladen

## Phase 5 — Weitere Plattformen

Reihenfolge nach Bedarf:

- [ ] Microsoft Store (Partner Center API existiert teilweise — pruefen)
- [ ] XBox (haengt im Microsoft-Stack mit drin)
- [ ] Google Play (Android) — Play Console API ist offiziell und gut dokumentiert
- [ ] Itch.io (vielleicht — falls relevant)

Datenmodell sollte schon in Phase 1 plattform-agnostisch genug sein, damit
hier nur ein neuer Adapter pro Plattform dazukommt.

## Phase 6 — Erweiterte Steam-Inhalte

- [ ] Kurzmeldungen / News-Posts
- [ ] Sale-Termine / Events
- [ ] Steam Demos
- [ ] DLC-Listings
- [ ] Reviews-Monitoring (read-only)

## Phase 7 — Multi-Game-Komfort

- [ ] Globales Glossar netmin-weit (markenuebergreifend)
- [ ] Cross-Game-Suche ("welche Spiele haben den Begriff XY?")
- [ ] Bulk-Operationen ueber mehrere Spiele
- [ ] Templates (z.B. "Standard-Ueberschriften" fuer neue Spiele)

## Phase 8 — Direkt-"Kunden"

Falls eines Tages nicht-Storefront-Beziehungen reinkommen (Publisher,
Distributor, Lokalisierungsdienste), werden die als oberste-Ebene-Reiter
neben den Plattformen stehen — gleicher Tab-Mechanismus, anderer Inhalt.

---

## Was bewusst NICHT in dieser Roadmap steht

- **Steamworks-SDK-Integration im Spiel selbst** — das gehoert in das jeweilige
  Spiel-Repo (z.B. Goalgetter), nicht zu Sid.
- **Marketing-Texte fuer Social Media** — gehoert zu Max (MarketingMin).
- **Ingame-Lokalisierung** — TK-Loka liegt unter Goalgetter, andere Spiele
  haben eigene Werkbaenke. Sid ist nur fuer Storefronts und Marketing-Listings.
