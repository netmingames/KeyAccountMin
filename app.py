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
import json
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ValidationError

from core import ea_exporter, edit_ops, exporter, glossary as glossary_mod, labels, schema, steam_codes, storage, translator

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
    idir = _resolve_idir_with_meta(platform, item_id)
    # Lisbeth NT-549 14:44 (MEDIUM FUNCTIONAL): schema-invalide aber JSON-
    # decodierbare meta.json wuerde sonst als 500 sichtbar. ValidationError
    # in 422 ueberfuehren ("schema-mismatch / migration noetig").
    try:
        meta = schema.ItemMeta(**storage.read_json(storage.meta_path(idir)))
    except ValidationError as e:
        raise HTTPException(
            status_code=422,
            detail=f"meta.json fuer {item_id} hat ungueltiges Schema: {e.errors()}",
        )
    master_lang = meta.master_lang  # z.B. "german" -> Datei heisst master_de.json
    iso_short = steam_codes.get(master_lang).iso.split("-")[0]
    master_file = storage.master_path(idir, iso_short)
    if not master_file.exists():
        raise HTTPException(
            status_code=500,
            detail=f"Master-Datei {master_file.name} fehlt im Item-Ordner",
        )
    # Lisbeth NT-549 15:36 Pass 5: nicht nur ValidationError, sondern auch
    # JSONDecodeError und OSError (z.B. korrupte Datei, I/O-Fehler) als
    # 422 ueberfuehren — sonst bubbelt der Decode-Fehler als 500 raus.
    # Lisbeth NT-549 15:54 Pass 6: zusaetzlich UnicodeDecodeError schlucken
    # — kaputte UTF-8-Sequenzen in master_*.json sollen ebenfalls als
    # controlled 422 erscheinen, nicht als 500.
    try:
        master = schema.MasterDocument(**storage.read_json(master_file))
    except ValidationError as e:
        raise HTTPException(
            status_code=422,
            detail=f"master_*.json fuer {item_id} hat ungueltiges Schema: {e.errors()}",
        )
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        raise HTTPException(
            status_code=422,
            detail=f"master_*.json fuer {item_id} unlesbar: {e}",
        )

    translations: dict[str, dict] = {}
    for lang in steam_codes.CODES:
        if lang == master_lang:
            continue
        tpath = storage.translation_path(idir, lang)
        if not tpath.exists():
            continue
        try:
            t = schema.TranslationDocument(**storage.read_json(tpath))
        except (ValidationError, json.JSONDecodeError, OSError, UnicodeDecodeError):
            # Korrupte Translation-Datei -> ueberspringen (kein 500 wegen einer
            # einzelnen kaputten Sprache). Lisbeth NT-549 15:36 Pass 5: JSON-
            # Decode/OS-Fehler genauso wie Pydantic-ValidationError schlucken.
            # Pass 6 (15:54): zusaetzlich UnicodeDecodeError fangen — kaputte
            # UTF-8-Sequenzen sollen die Sprache skippen, nicht das Item killen.
            continue
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
    idir = _resolve_idir(platform, item_id)
    tpath = storage.translation_path(idir, lang)
    if not tpath.exists():
        raise HTTPException(status_code=404, detail=f"Keine Translation fuer {lang}")
    # Lisbeth NT-550 16:05 Pass 4 (MEDIUM FUNCTIONAL): kaputte Translation-
    # Datei (Schema/JSON/UTF-8/IO) -> 422 statt 500. Frontend rendert dann
    # einen Fehler-State im Editor.
    try:
        t = schema.TranslationDocument(**storage.read_json(tpath))
    except ValidationError as e:
        raise HTTPException(
            status_code=422,
            detail=f"Translation {lang} fuer {item_id} hat ungueltiges Schema: {e.errors()}",
        )
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        raise HTTPException(
            status_code=422,
            detail=f"Translation {lang} fuer {item_id} unlesbar: {e}",
        )
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


@app.get("/api/fields")
def api_fields() -> dict:
    """Liefert Feld-Metadaten fuer das UI: technischer Name, Label, multiline, hint."""
    out = []
    for f in schema.STEAM_FIELDS_STANDARD:
        out.append({
            "field": f,
            "label": labels.label(f),
            "multiline": labels.is_multiline(f),
            "hint": labels.hint(f),
            "block": "standard",
        })
    for f in schema.STEAM_FIELDS_EA:
        out.append({
            "field": f,
            "label": labels.label(f),
            "multiline": labels.is_multiline(f),
            "hint": labels.hint(f),
            "block": "early_access",
        })
    return {"fields": out}


# --- Edit Endpoints (NT-548) -------------------------------------------------

class _ValueBody(BaseModel):
    value: str


class _LanguagesBody(BaseModel):
    languages: list[str]


class _EarlyAccessBody(BaseModel):
    enabled: bool


class _CreateItemBody(BaseModel):
    platform: str
    item_id: str
    name: str
    master_lang: str = "german"
    early_access: bool = False


def _resolve_idir(platform: str, item_id: str) -> Path:
    try:
        return storage.item_dir(DATA_ROOT, platform, item_id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


def _resolve_idir_with_meta(platform: str, item_id: str) -> Path:
    """Wie _resolve_idir, prueft aber zusaetzlich ob meta.json lesbar ist.

    Lisbeth NT-548 14:39 (MEDIUM FUNCTIONAL): Routen, die Daten aus dem Item-
    Ordner lesen oder schreiben (translate, glossary, export, master/translation
    PUTs), brauchen eine valide meta.json. Wenn ``item_dir()`` einen Legacy-
    Folder ohne meta.json liefert, wuerden die nachgelagerten ``read_meta``/
    ``glossary.load``/``exporter.export``-Aufrufe alle in FileNotFoundError oder
    JSONDecodeError laufen — sichtbar als 500 Internal Server Error. Stattdessen
    hier kontrolliert auf 404 ueberfuehren.

    Lisbeth NT-549 15:10 (MEDIUM FUNCTIONAL): zusaetzlich auch Pydantic-
    Schema validieren. Sonst laufen translate/export/glossary in einen 500
    durch read_meta(), wenn meta.json zwar JSON-decodierbar aber schema-
    invalid ist. Schema-Fehler -> 422, konsistent mit api_get_item.
    """
    idir = _resolve_idir(platform, item_id)
    meta_file = storage.meta_path(idir)
    if not meta_file.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Item {item_id} unter {platform}: meta.json fehlt (Legacy-Ordner '{idir.name}' ohne Metadaten)",
        )
    try:
        meta_raw = storage.read_json(meta_file)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        # Lisbeth NT-549 15:54 Pass 6: zusaetzlich UnicodeDecodeError fangen
        # — kaputte UTF-8-Bytes in meta.json sollen ebenfalls als 404 ueber-
        # fuehrt werden, nicht als 500 bubbeln.
        raise HTTPException(
            status_code=404,
            detail=f"Item {item_id} unter {platform}: meta.json unlesbar ({e})",
        )
    try:
        schema.ItemMeta(**meta_raw)
    except ValidationError as e:
        raise HTTPException(
            status_code=422,
            detail=f"meta.json fuer {item_id} hat ungueltiges Schema: {e.errors()}",
        )
    return idir


@app.put("/api/items/{platform}/{item_id}/master/{field}")
def api_put_master_field(platform: str, item_id: str, field: str, body: _ValueBody) -> dict:
    idir = _resolve_idir_with_meta(platform, item_id)
    try:
        result = edit_ops.update_master_field(idir, field, body.value)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, **result}


@app.put("/api/items/{platform}/{item_id}/translation/{lang}/{field}")
def api_put_translation_field(platform: str, item_id: str, lang: str, field: str, body: _ValueBody) -> dict:
    if not steam_codes.is_valid(lang):
        raise HTTPException(status_code=400, detail=f"Unbekannter Steam-Sprachcode: {lang}")
    idir = _resolve_idir_with_meta(platform, item_id)
    try:
        result = edit_ops.update_translation_field(idir, lang, field, body.value)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, **result}


@app.put("/api/items/{platform}/{item_id}/active-languages")
def api_put_active_languages(platform: str, item_id: str, body: _LanguagesBody) -> dict:
    idir = _resolve_idir_with_meta(platform, item_id)
    try:
        result = edit_ops.set_active_languages(idir, body.languages)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, **result}


@app.put("/api/items/{platform}/{item_id}/early-access")
def api_put_early_access(platform: str, item_id: str, body: _EarlyAccessBody) -> dict:
    idir = _resolve_idir_with_meta(platform, item_id)
    result = edit_ops.set_early_access(idir, body.enabled)
    return {"ok": True, **result}


_SUPPORTED_PLATFORMS = {"steam"}
_SUPPORTED_MASTER_LANGS = {"german"}


class _TranslateBody(BaseModel):
    fields: list[str] | None = None
    engine: str | None = None  # "claude" | "mock" | None (= aus env SID_TRANSLATOR)


class _GlossaryEntryBody(BaseModel):
    term: str
    rule: str = "keep"  # "keep" | "translate"
    note: str = ""


class _GlossaryBody(BaseModel):
    entries: list[_GlossaryEntryBody]


@app.post("/api/items/{platform}/{item_id}/translate/{lang}")
def api_translate_lang(platform: str, item_id: str, lang: str, body: _TranslateBody) -> dict:
    if not steam_codes.is_valid(lang):
        raise HTTPException(status_code=400, detail=f"Unbekannter Steam-Sprachcode: {lang}")
    idir = _resolve_idir_with_meta(platform, item_id)

    # Engine-Auswahl ohne env-var-Mutation: explizite Translator-Instanz.
    # Lisbeth NT-549 14:44 (LOW FUNCTIONAL): unbekannter engine-Wert wurde
    # vorher silently auf den env-Default zurueckgefallen. Jetzt explizit
    # 400 — sonst glaubt der Caller, er habe den Translator gewaehlt, der
    # tatsaechlich aber gar nicht greift.
    _VALID_ENGINES = {None, "mock", "claude", "claude-cli"}
    if body.engine not in _VALID_ENGINES:
        raise HTTPException(
            status_code=400,
            detail=f"Unbekannte engine '{body.engine}'. Erlaubt: mock | claude | claude-cli | (leer = Default).",
        )
    tx = None
    if body.engine == "mock":
        tx = translator.MockTranslator()
    elif body.engine in ("claude", "claude-cli"):
        tx = translator.ClaudeCliTranslator()
    # else: body.engine is None -> get_translator() innerhalb translate_item_lang nimmt env

    try:
        result = translator.translate_item_lang(
            idir, lang, fields=body.fields, translator=tx,
        )
    except translator.TranslationError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "ok": True,
        "lang": result.lang,
        "engine": result.engine,
        "duration_seconds": round(result.duration_seconds, 2),
        "fields_translated": list(result.fields_translated.keys()),
        "fields_skipped": result.fields_skipped,
    }


@app.post("/api/items/{platform}/{item_id}/translate-all")
def api_translate_all(platform: str, item_id: str, body: _TranslateBody) -> dict:
    """Uebersetzt alle aktiven Zielsprachen (Master ausgenommen) sequenziell.

    Manuell editierte Felder bleiben geschuetzt (via_translation_engine=True
    in core/edit_ops.update_translation_field). Pro Sprache wird ok/error
    geliefert — eine fehlschlagende Sprache stoppt nicht die anderen, der
    Caller sieht in der Liste was geklappt hat.
    """
    idir = _resolve_idir_with_meta(platform, item_id)
    meta = edit_ops.read_meta(idir)
    target_langs = [l for l in meta.active_languages if l != meta.master_lang]

    _VALID_ENGINES = {None, "mock", "claude", "claude-cli"}
    if body.engine not in _VALID_ENGINES:
        raise HTTPException(
            status_code=400,
            detail=f"Unbekannte engine '{body.engine}'. Erlaubt: mock | claude | claude-cli | (leer = Default).",
        )
    tx = None
    if body.engine == "mock":
        tx = translator.MockTranslator()
    elif body.engine in ("claude", "claude-cli"):
        tx = translator.ClaudeCliTranslator()

    results: list[dict] = []
    for lang in target_langs:
        entry: dict = {"lang": lang}
        try:
            r = translator.translate_item_lang(
                idir, lang, fields=body.fields, translator=tx,
            )
            entry.update({
                "ok": True,
                "engine": r.engine,
                "duration_seconds": round(r.duration_seconds, 2),
                "fields_translated": list(r.fields_translated.keys()),
                "fields_skipped": r.fields_skipped,
            })
        except translator.TranslationError as e:
            entry.update({"ok": False, "error": str(e)})
        except ValueError as e:
            entry.update({"ok": False, "error": str(e)})
        results.append(entry)

    n_ok = sum(1 for e in results if e.get("ok"))
    return {
        "n_total": len(target_langs),
        "n_ok": n_ok,
        "n_failed": len(target_langs) - n_ok,
        "results": results,
    }


@app.get("/api/items/{platform}/{item_id}/glossary")
def api_get_glossary(platform: str, item_id: str) -> dict:
    idir = _resolve_idir_with_meta(platform, item_id)
    return glossary_mod.load(idir)


@app.put("/api/items/{platform}/{item_id}/glossary")
def api_put_glossary(platform: str, item_id: str, body: _GlossaryBody) -> dict:
    idir = _resolve_idir_with_meta(platform, item_id)
    g = {"entries": [e.model_dump() for e in body.entries]}
    try:
        glossary_mod.save(idir, g)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "n_entries": len(g["entries"])}


def _safe_export_steam_loka(idir: Path, item_id: str) -> dict:
    """Wrapper um exporter.export_steam_loka mit kontrollierten Fehlern.

    Lisbeth NT-550 16:05 Pass 4 (MEDIUM FUNCTIONAL): /export-preview und
    /export liessen exceptions aus exporter.export_steam_loka() (kommen aus
    edit_ops.read_master() bei korrupter master_*.json) als 500 durch. Hier
    ueberfuehren wir Validation-/JSON-/IO-/UTF-8-Fehler in 422, konsistent
    mit api_get_item().
    """
    try:
        return exporter.export_steam_loka(idir)
    except ValidationError as e:
        raise HTTPException(
            status_code=422,
            detail=f"Export fuer {item_id} nicht moeglich: master_*.json hat ungueltiges Schema: {e.errors()}",
        )
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        raise HTTPException(
            status_code=422,
            detail=f"Export fuer {item_id} nicht moeglich: master_*.json unlesbar: {e}",
        )


@app.get("/api/items/{platform}/{item_id}/export-preview")
def api_export_preview(platform: str, item_id: str) -> dict:
    """Liefert das Export-JSON inline ohne die Datei zu schreiben."""
    idir = _resolve_idir_with_meta(platform, item_id)
    data = _safe_export_steam_loka(idir, item_id)
    return {"summary": exporter.export_summary(data), "data": data}


@app.post("/api/items/{platform}/{item_id}/export")
def api_export_to_file(platform: str, item_id: str) -> dict:
    """Schreibt den Export nach exports/<ts>.json und gibt Pfad + Summary zurueck."""
    idir = _resolve_idir_with_meta(platform, item_id)
    # Lisbeth NT-550 16:05 Pass 4: vor dem Schreiben prueft _safe_export... ob
    # der Master ueberhaupt valide ist - sonst 422 statt halb geschriebener Datei.
    _safe_export_steam_loka(idir, item_id)
    try:
        out_path = exporter.export_to_file(idir)
    except ValidationError as e:
        raise HTTPException(
            status_code=422,
            detail=f"Export fuer {item_id} nicht moeglich: master_*.json hat ungueltiges Schema: {e.errors()}",
        )
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        raise HTTPException(
            status_code=422,
            detail=f"Export fuer {item_id} nicht moeglich: master_*.json unlesbar: {e}",
        )
    data = storage.read_json(out_path)
    return {
        "ok": True,
        "filename": out_path.name,
        "summary": exporter.export_summary(data),
        "download_url": f"/api/items/{platform}/{item_id}/exports/{out_path.name}",
    }


@app.get("/api/items/{platform}/{item_id}/exports")
def api_list_exports(platform: str, item_id: str) -> dict:
    idir = _resolve_idir(platform, item_id)
    return {"exports": exporter.list_exports(idir)}


@app.get("/api/items/{platform}/{item_id}/exports/{filename}")
def api_download_export(platform: str, item_id: str, filename: str):
    """Liefert eine bestehende Export-Datei zum Download."""
    from fastapi.responses import FileResponse
    if "/" in filename or "\\" in filename or filename.startswith(".") or not filename.endswith(".json"):
        raise HTTPException(status_code=400, detail="Ungueltiger Dateiname")
    idir = _resolve_idir(platform, item_id)
    p = idir / "exports" / filename
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"Datei nicht gefunden: {filename}")
    # Sicherheitscheck: Pfad muss innerhalb des Item-exports-Ordners liegen
    try:
        p.resolve().relative_to((idir / "exports").resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Pfad ausserhalb erlaubtem Bereich")
    return FileResponse(p, media_type="application/json", filename=filename)


# --- Early-Access-Export (NT-551) -------------------------------------------

@app.get("/api/items/{platform}/{item_id}/ea-status")
def api_ea_status(platform: str, item_id: str) -> dict:
    """Liefert Liste der aktiven Sprachen mit EA-Feld-Fuellstand."""
    idir = _resolve_idir(platform, item_id)
    return {"languages": ea_exporter.list_ea_languages(idir)}


@app.get("/api/items/{platform}/{item_id}/ea-export/{lang}.txt")
def api_ea_export_text(platform: str, item_id: str, lang: str):
    """Liefert die EA-Q&A-Texte einer Sprache als Plaintext-Download."""
    if not steam_codes.is_valid(lang):
        raise HTTPException(status_code=400, detail=f"Unbekannter Steam-Sprachcode: {lang}")
    idir = _resolve_idir(platform, item_id)
    text = ea_exporter.render_ea_text(idir, lang)
    filename = ea_exporter.filename_for_lang(idir, lang)
    from fastapi.responses import Response
    return Response(
        content=text,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/items/{platform}/{item_id}/ea-export.zip")
def api_ea_export_zip(platform: str, item_id: str):
    """Sammel-ZIP mit allen aktiven Sprachen als .txt-Dateien + README."""
    idir = _resolve_idir(platform, item_id)
    try:
        data = ea_exporter.export_ea_bundle_zip(idir)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    meta = edit_ops.read_meta(idir)
    slug = storage._slugify(meta.name)
    from fastapi.responses import Response
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{slug}_ea_bundle.zip"'},
    )


@app.post("/api/items")
def api_post_item(body: _CreateItemBody) -> dict:
    # Phase 1 ist bewusst auf Steam + German-Master beschraenkt. Master_de.json
    # ist der vereinbarte Speicherort, andere Plattformen/Sprachen haben noch
    # keinen geprueften Datenfluss. Direkte API-Aufrufe duerfen das nicht
    # umgehen — die UI exponiert die Optionen ohnehin nicht.
    if body.platform not in _SUPPORTED_PLATFORMS:
        raise HTTPException(
            status_code=400,
            detail=f"Plattform nicht unterstuetzt: {body.platform!r} (erlaubt: {sorted(_SUPPORTED_PLATFORMS)})",
        )
    if body.master_lang not in _SUPPORTED_MASTER_LANGS:
        raise HTTPException(
            status_code=400,
            detail=f"Master-Sprache nicht unterstuetzt: {body.master_lang!r} (erlaubt: {sorted(_SUPPORTED_MASTER_LANGS)})",
        )
    try:
        idir = edit_ops.create_item(
            DATA_ROOT,
            platform=body.platform,
            item_id=body.item_id,
            name=body.name,
            master_lang=body.master_lang,
            early_access=body.early_access,
        )
    except FileExistsError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "dir": idir.name, "platform": body.platform, "item_id": body.item_id}


if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=5003,
        log_level="info",
        reload=False,
    )
