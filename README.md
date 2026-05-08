# Sid / KeyAccountMin

Werkbank fuer Listings & Lokalisierung der netmin-games-Spiele — multiplattform
(Steam, Microsoft Store, XBox, Google Play, ...), multi-Sprache.

> Architektur, Datenmodell, Tech-Stack: [`CLAUDE.md`](CLAUDE.md)
> Entwicklungs-Phasen: [`ROADMAP.md`](ROADMAP.md)

## Schnellstart (Dev)

```bash
python -m venv .venv
.venv\Scripts\activate         # Windows
pip install -r requirements.txt
python app.py
# -> http://localhost:5003
```

## Im Betrieb

- Devastator-Service auf Port 5003 via Scheduled Task `Sid_KeyAccountMin`
- Center-Tile: http://devastator:5000 -> Karte "💼 Sid"
- Direkt-Link: http://devastator:5003

## Health

`GET /api/health` → `{status: ok, name, version, phase, timestamp}`
