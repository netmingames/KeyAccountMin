"""Sid / KeyAccountMin — FastAPI Service.

Phase 0 (NT-546): Healthcheck + Begruessungsseite.
Phase 1 (NT-547): Read-only Items-API ueber das Datenmodell aus core/.
              Voll-Editor folgt mit NT-548 (UI).

Start (lokal/dev):
    python app.py

Im Betrieb laeuft der Service via Scheduled Task `AI_Mitarbeiter_KeyAccountMin`
auf devastator:5003. Center-Tile verlinkt direkt auf http://devastator:5003.
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from core import schema, steam_codes, storage

VERSION = "0.2.0"
NAME = "Sid / KeyAccountMin"
ROOT = Path(__file__).resolve().parent
DATA_ROOT = ROOT / "data"

app = FastAPI(title=NAME, version=VERSION)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
templates = Jinja2Templates(directory=ROOT / "templates")


@app.get("/api/health")
def health() -> JSONResponse:
    return JSONResponse(
        {
            "status": "ok",
            "name": NAME,
            "version": VERSION,
            "phase": "1 - Datenmodell + Importer",
            "timestamp": _dt.datetime.now().isoformat(timespec="seconds"),
        }
    )


@app.get("/api/version")
def version() -> dict:
    return {"name": NAME, "version": VERSION}


@app.get("/")
def index(request: Request):
    items = _list_all_items()
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "name": NAME, "version": VERSION, "items": items},
    )


# --- Items API ---------------------------------------------------------------

def _list_all_items() -> list[dict]:
    """Findet alle Items in data/<plattform>/<id>_<slug>/ und liest deren meta.json."""
    out: list[dict] = []
    if not DATA_ROOT.exists():
        return out
    for platform_dir in sorted(DATA_ROOT.iterdir()):
        if not platform_dir.is_dir() or platform_dir.name.startswith("_"):
            continue
        for item_dir in sorted(platform_dir.iterdir()):
            if not item_dir.is_dir():
                continue
            meta_file = item_dir / "meta.json"
            if not meta_file.exists():
                continue
            try:
                m = schema.ItemMeta(**storage.read_json(meta_file))
            except Exception:
                continue
            out.append(
                {
                    "platform": m.platform,
                    "item_id": m.item_id,
                    "name": m.name,
                    "active_languages": m.active_languages,
                    "early_access": m.early_access,
                    "updated_at": m.updated_at,
                    "dir": item_dir.name,
                }
            )
    return out


@app.get("/api/items")
def api_list_items() -> dict:
    return {"items": _list_all_items()}


@app.get("/api/items/{platform}/{item_id}")
def api_get_item(platform: str, item_id: str) -> dict:
    try:
        idir = storage.item_dir(DATA_ROOT, platform, item_id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    meta = schema.ItemMeta(**storage.read_json(storage.meta_path(idir)))
    master_lang = meta.master_lang  # z.B. "german" -> Datei heisst master_de.json
    iso_short = steam_codes.get(master_lang).iso.split("-")[0]
    master_file = storage.master_path(idir, iso_short)
    if not master_file.exists():
        raise HTTPException(
            status_code=500,
            detail=f"Master-Datei {master_file.name} fehlt im Item-Ordner",
        )
    master = schema.MasterDocument(**storage.read_json(master_file))

    translations: dict[str, dict] = {}
    for lang in steam_codes.CODES:
        if lang == master_lang:
            continue
        tpath = storage.translation_path(idir, lang)
        if not tpath.exists():
            continue
        t = schema.TranslationDocument(**storage.read_json(tpath))
        n_filled = sum(1 for f in t.fields.values() if f.value)
        n_stale = sum(1 for f in t.fields.values() if f.stale)
        n_manual = sum(1 for f in t.fields.values() if f.manually_edited)
        translations[lang] = {
            "filled": n_filled,
            "total": len(t.fields),
            "stale": n_stale,
            "manually_edited": n_manual,
            "updated_at": t.updated_at,
        }

    return {
        "meta": meta.model_dump(),
        "master": master.model_dump(),
        "translations": translations,
    }


@app.get("/api/items/{platform}/{item_id}/translation/{lang}")
def api_get_translation(platform: str, item_id: str, lang: str) -> dict:
    if not steam_codes.is_valid(lang):
        raise HTTPException(status_code=400, detail=f"Unbekannter Steam-Sprachcode: {lang}")
    try:
        idir = storage.item_dir(DATA_ROOT, platform, item_id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    tpath = storage.translation_path(idir, lang)
    if not tpath.exists():
        raise HTTPException(status_code=404, detail=f"Keine Translation fuer {lang}")
    t = schema.TranslationDocument(**storage.read_json(tpath))
    return t.model_dump()


@app.get("/api/languages")
def api_languages() -> dict:
    """Liste aller Steam-Sprachen mit Display- und Native-Namen."""
    return {
        "master": steam_codes.MASTER_CODE,
        "languages": [
            {"code": l.code, "iso": l.iso, "display": l.display, "native": l.native}
            for l in steam_codes.STEAM_LANGS
        ],
    }


if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=5003,
        log_level="info",
        reload=False,
    )
