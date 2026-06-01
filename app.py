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

import asyncio
import datetime as _dt
import json
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ValidationError

from core import assets, ea_exporter, edit_ops, exporter, glossary as glossary_mod, labels, schema, steam_codes, storage, translator

VERSION = "0.2.0"
NAME = "Sid / KeyAccountMin"
ROOT = Path(__file__).resolve().parent
DATA_ROOT = ROOT / "data"

app = FastAPI(title=NAME, version=VERSION)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
templates = Jinja2Templates(directory=ROOT / "templates")


@app.exception_handler(assets.ManifestCorruptError)
async def _on_manifest_corrupt(_req: Request, exc: assets.ManifestCorruptError) -> JSONResponse:
    """NT-564 Pass 2 (Lisbeth 17:17 LOW FUNCTIONAL): kaputte manifest.json soll
    keinen 500er Stacktrace produzieren, sondern eine kontrollierte 422-Antwort.

    Damit kann das Grafiken-UI eine klare Fehlermeldung anzeigen statt einem
    leeren Fehlertoast — der ganze Tab bleibt bedienbar, nur dieses eine Item
    zeigt einen Hinweis.
    """
    return JSONResponse(status_code=422, content={"detail": str(exc)})


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

    # Lisbeth NT-551 Pass 12 (09:56 MEDIUM FUNCTIONAL): translate_item_lang
    # liest master_*.json + translations/<lang>.json. Eine korrupte Quelle
    # darf nicht als 500 durchrutschen — konsistent mit translate-all/
    # translate-stream (422 statt 500). Reihenfolge wichtig: JSONDecodeError
    # ist eine Subclass von ValueError, daher MUSS sie vor ValueError stehen,
    # sonst greift weiter unten 400 statt 422.
    try:
        result = translator.translate_item_lang(
            idir, lang, fields=body.fields, translator=tx,
        )
    except translator.TranslationError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except ValidationError as e:
        raise HTTPException(
            status_code=422,
            detail=f"Quelle fuer '{lang}' hat ungueltiges Schema: {e.errors()}",
        )
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        raise HTTPException(
            status_code=422,
            detail=f"Quelle fuer '{lang}' unlesbar: {e}",
        )
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
        # Lisbeth NT-550 Pass 5 (08:57 MEDIUM FUNCTIONAL): translate_item_lang
        # liest master_*.json + translations/<lang>.json. Eine korrupte/schema-
        # invalide Datei kann ValidationError/JSONDecodeError/OSError/
        # UnicodeDecodeError werfen — frueher hat das den ganzen Batch mit
        # 500 abgebrochen. Jetzt wird die Sprache als failed gemeldet, der Rest
        # laeuft weiter.
        except (translator.TranslationError, ValueError, ValidationError,
                json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
            entry.update({"ok": False, "error": str(e)})
        results.append(entry)

    n_ok = sum(1 for e in results if e.get("ok"))
    return {
        "n_total": len(target_langs),
        "n_ok": n_ok,
        "n_failed": len(target_langs) - n_ok,
        "results": results,
    }


def _stale_or_empty_fields(idir: Path, lang: str) -> list[str]:
    """NT-568: Felder einer Sprache, die UEBERSETZT werden muessen — leer ODER
    veraltet (stale). Manuell editierte/aktuelle Felder bleiben aussen vor.

    Nur Master-Felder mit Inhalt sind Kandidaten. Existiert keine Translation,
    sind alle Content-Felder faellig.
    """
    master = edit_ops.read_master(idir)
    content = [f for f, v in master.fields.items() if v]
    tpath = storage.translation_path(idir, lang)
    if not tpath.exists():
        return content
    # NT-565 (Lisbeth 18:49 MEDIUM FUNCTIONAL): korrupte/schema-invalide
    # Translation darf hier nicht als 500 durchschlagen — als komplett faellig
    # behandeln; der eigentliche translate_item_lang-Aufruf meldet den Fehler
    # dann sauber als per-Sprache-Fehler im Stream.
    try:
        t = edit_ops.read_translation(idir, lang)
    except Exception:  # noqa: BLE001
        return content
    out: list[str] = []
    for f in content:
        tf = t.fields.get(f)
        if tf is None or not tf.value or tf.stale:
            out.append(f)
    return out


@app.get("/api/items/{platform}/{item_id}/translate-stream")
async def api_translate_stream(
    platform: str, item_id: str, request: Request, engine: str | None = None,
    langs: str | None = None, only_stale: bool = False,
) -> StreamingResponse:
    """SSE-Stream fuer Bulk-Uebersetzung (Lisbeth NT-549 Pass 9 MEDIUM FUNCTIONAL).

    Liefert pro Zielsprache zwei Events: ``lang_start`` (vor dem Claude-CLI-
    Aufruf) und ``lang_done`` (nach Erfolg/Fehler), plus ein abschliessendes
    ``done``-Event. Damit sieht das Frontend live wann jede Sprache anfaengt
    und kann den Spinner pro Reihe sauber umschalten — kein UI-Hang mehr,
    auch wenn ein einzelner Claude-Aufruf in den Timeout laeuft (lang_start
    ist schon raus, der User sieht welche Sprache gerade haengt).

    EventSource ist GET-only, daher Query-Parameter statt Body. Wir benutzen
    die Default-Felder (alle uebersetzbaren Felder) — das ist die einzige
    Variante die das Bulk-Translate-Modal anbietet.

    NT-550 Pass 13 (Lisbeth 10:23 MEDIUM FUNCTIONAL): zwischen den Sprachen
    wird ``request.is_disconnected()`` geprueft. Wenn der Client den
    EventSource bereits geschlossen hat (Modal-Close, ESC, Tab geschlossen),
    bricht die Batch ab und sendet ein ``cancelled``-Event statt ``done``.
    Die gerade laufende Sprache wird noch zu Ende uebersetzt (Claude-CLI
    ist nicht abbrechbar), aber keine weitere Sprache wird mehr gestartet.
    """
    idir = _resolve_idir_with_meta(platform, item_id)
    meta = edit_ops.read_meta(idir)
    target_langs = [l for l in meta.active_languages if l != meta.master_lang]

    # NT-568: optionale Sprachauswahl (Subset der aktiven Zielsprachen). Ohne
    # langs-Param bleibt es bei "alle aktiven" (rueckwaertskompatibel). Unbekannte
    # oder inaktive Codes werden ignoriert; bleibt nichts uebrig -> 400.
    if langs is not None:
        requested = [c.strip() for c in langs.split(",") if c.strip()]
        active_set = set(target_langs)
        target_langs = [c for c in requested if c in active_set]
        if not target_langs:
            raise HTTPException(
                status_code=400,
                detail="Keine gueltige Zielsprache in 'langs' (muss aktive Nicht-Master-Sprache sein).",
            )

    _VALID_ENGINES = {None, "mock", "claude", "claude-cli"}
    if engine not in _VALID_ENGINES:
        raise HTTPException(
            status_code=400,
            detail=f"Unbekannte engine '{engine}'. Erlaubt: mock | claude | claude-cli | (leer = Default).",
        )
    if engine == "mock":
        tx = translator.MockTranslator()
    elif engine in ("claude", "claude-cli"):
        tx = translator.ClaudeCliTranslator()
    else:
        tx = None

    async def _gen():
        def _evt(name: str, data: dict) -> str:
            return f"event: {name}\ndata: {json.dumps(data)}\n\n"

        yield _evt("start", {"n_total": len(target_langs), "langs": target_langs})
        n_ok = 0
        n_failed = 0
        cancelled = False
        for lang in target_langs:
            # NT-550 Pass 13: vor jeder Sprache pruefen ob Client noch da ist.
            # Falls schon disconnected (Modal-Close): break, kein weiterer
            # Claude-CLI-Aufruf. Die zuvor laufende Sprache ist bereits durch,
            # eine eventuell aktuell laufende Sprache (im vorigen Loop-Iter)
            # ebenfalls; aber alle nachfolgenden werden geschont.
            if await request.is_disconnected():
                cancelled = True
                break
            yield _evt("lang_start", {"lang": lang})
            entry: dict = {"lang": lang}
            try:
                # NT-550 Pass 14 (Lisbeth 10:38 MEDIUM FUNCTIONAL): translate_item_lang
                # ist synchron + ~30 s pro Sprache. Direkt aufgerufen blockiert es den
                # asyncio-event-loop und friert parallele Requests auf dem Worker ein
                # (plus is_disconnected feuert nicht mehr punktlich). Mit
                # asyncio.to_thread laeuft der Claude-CLI-Subprozess in einem
                # Worker-Thread, der Loop bleibt frei.
                # NT-568: bei only_stale nur leere/veraltete Felder uebersetzen
                # (schnelle Sync-Lesung, Millisekunden); sonst alle Felder.
                _fields = _stale_or_empty_fields(idir, lang) if only_stale else None
                r = await asyncio.to_thread(
                    translator.translate_item_lang,
                    idir, lang, fields=_fields, translator=tx,
                )
                entry.update({
                    "ok": True,
                    "engine": r.engine,
                    "duration_seconds": round(r.duration_seconds, 2),
                    "fields_translated": list(r.fields_translated.keys()),
                    "fields_skipped": r.fields_skipped,
                })
                n_ok += 1
            # Lisbeth NT-549 Pass 10 (09:24 MEDIUM FUNCTIONAL): gleicher Guard
            # wie translate-all — eine korrupte Datei darf nicht den Stream
            # mitten in der Batch abreissen lassen. Sprache als failed melden,
            # Loop laeuft weiter, abschliessendes done-Event wird emitted.
            except (translator.TranslationError, ValueError, ValidationError,
                    json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
                entry.update({"ok": False, "error": str(e)})
                n_failed += 1
            yield _evt("lang_done", entry)
        if cancelled:
            n_remaining = len(target_langs) - n_ok - n_failed
            yield _evt("cancelled", {
                "n_ok": n_ok, "n_failed": n_failed,
                "n_remaining": n_remaining, "n_total": len(target_langs),
            })
        else:
            yield _evt("done", {"n_ok": n_ok, "n_failed": n_failed, "n_total": len(target_langs)})

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",  # disable proxy buffering, falls vorgeschaltet
    }
    return StreamingResponse(_gen(), media_type="text/event-stream", headers=headers)


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
    # Lisbeth NT-549 16:12 Pass 7 (LOW FUNCTIONAL): das vorgerechnete Daten-
    # Dict (inkl. skipped_translations) wird sowohl beim Schreiben als auch
    # in der Summary wiederverwendet, damit /export-preview und /export
    # konsistente skipped_translations melden (frueher hat /export die
    # Datei zurueckgelesen, wo skipped_translations bereits gestrippt war).
    data = _safe_export_steam_loka(idir, item_id)
    try:
        out_path = exporter.export_to_file(idir, data=data)
    except OSError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Export-Datei fuer {item_id} konnte nicht geschrieben werden: {e}",
        )
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
    # Lisbeth NT-549 Pass 8 (16:33 MEDIUM FUNCTIONAL): meta-Validierung
    # via _resolve_idir_with_meta, sonst bubbeln korrupte/legacy meta.json
    # als 500 in ea_exporter.list_ea_languages.
    idir = _resolve_idir_with_meta(platform, item_id)
    return {"languages": ea_exporter.list_ea_languages(idir)}


@app.get("/api/items/{platform}/{item_id}/ea-export/{lang}.txt")
def api_ea_export_text(platform: str, item_id: str, lang: str):
    """Liefert die EA-Q&A-Texte einer Sprache als Plaintext-Download."""
    if not steam_codes.is_valid(lang):
        raise HTTPException(status_code=400, detail=f"Unbekannter Steam-Sprachcode: {lang}")
    # Lisbeth NT-549 Pass 8 (16:33 MEDIUM FUNCTIONAL): wie ea-status.
    idir = _resolve_idir_with_meta(platform, item_id)
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
    # Lisbeth NT-549 Pass 8 (16:33 MEDIUM FUNCTIONAL): wie ea-status.
    idir = _resolve_idir_with_meta(platform, item_id)
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


# --- Assets / Screenshots (NT-564) ------------------------------------------
#
# Route-Reihenfolge ist wichtig: literale Pfade (screenshots, export.zip)
# MUESSEN vor dem generischen /assets/{slot} stehen, sonst matched FastAPI
# "screenshots"/"export.zip" als slot-Parameter.


class _CaptionBody(BaseModel):
    text: str
    lang: str | None = None


class _ReorderBody(BaseModel):
    ordered_ids: list[int]


def _ext_of(filename: str | None) -> str:
    """Endung aus dem Upload-Dateinamen ziehen (ohne Punkt, lowercase)."""
    ext = Path(filename or "").suffix.lstrip(".").lower()
    if not ext:
        raise HTTPException(status_code=400, detail="Datei ohne Endung — Format nicht erkennbar")
    return ext


@app.get("/api/assets/catalog")
def api_assets_catalog() -> dict:
    """Statischer Slot-Katalog (Sollmaße, localizable-Flag) fuers UI."""
    return {"slots": assets.catalog()}


@app.get("/api/items/{platform}/{item_id}/assets")
def api_assets_status(platform: str, item_id: str) -> dict:
    """Fuellstand-Uebersicht: pro Slot was vorhanden ist + Per-Sprache-Aufloesung."""
    idir = _resolve_idir_with_meta(platform, item_id)
    meta = edit_ops.read_meta(idir)
    return assets.status(idir, meta.active_languages)


@app.get("/api/items/{platform}/{item_id}/assets/export.zip")
def api_assets_export_zip(platform: str, item_id: str, lang: str):
    """ZIP mit allen fuer `lang` gueltigen Assets + Screenshots (Override sonst default)."""
    idir = _resolve_idir_with_meta(platform, item_id)
    try:
        data = assets.export_zip(idir, lang)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    meta = edit_ops.read_meta(idir)
    slug = storage._slugify(meta.name)
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{slug}_assets_{lang}.zip"'},
    )


@app.post("/api/items/{platform}/{item_id}/assets/screenshots")
async def api_add_screenshot(
    platform: str, item_id: str,
    file: UploadFile = File(...), master_caption: str = Form(""),
) -> dict:
    idir = _resolve_idir_with_meta(platform, item_id)
    data = await file.read()
    ext = _ext_of(file.filename)
    try:
        shot = assets.add_screenshot(idir, data, ext=ext, master_caption=master_caption)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "screenshot": shot.model_dump()}


@app.put("/api/items/{platform}/{item_id}/assets/screenshots/reorder")
def api_reorder_screenshots(platform: str, item_id: str, body: _ReorderBody) -> dict:
    idir = _resolve_idir_with_meta(platform, item_id)
    try:
        shots = assets.reorder_screenshots(idir, body.ordered_ids)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "screenshots": [s.model_dump() for s in shots]}


@app.post("/api/items/{platform}/{item_id}/assets/screenshots/{shot_id}/override")
async def api_screenshot_override(
    platform: str, item_id: str, shot_id: int,
    file: UploadFile = File(...), lang: str = Form(...),
) -> dict:
    idir = _resolve_idir_with_meta(platform, item_id)
    data = await file.read()
    ext = _ext_of(file.filename)
    try:
        shot = assets.set_screenshot_override(idir, shot_id, lang, data, ext=ext)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "screenshot": shot.model_dump()}


@app.delete("/api/items/{platform}/{item_id}/assets/screenshots/{shot_id}/override")
def api_delete_screenshot_override(
    platform: str, item_id: str, shot_id: int, lang: str,
) -> dict:
    """Loescht einen Per-Sprache-Override eines Screenshots (Default bleibt)."""
    idir = _resolve_idir_with_meta(platform, item_id)
    try:
        removed = assets.delete_screenshot_override(idir, shot_id, lang)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not removed:
        raise HTTPException(status_code=404, detail=f"Kein Override fuer {lang} bei Screenshot {shot_id}")
    return {"ok": True}


@app.get("/api/items/{platform}/{item_id}/assets/screenshots/{shot_id}/details")
def api_screenshot_details(platform: str, item_id: str, shot_id: int) -> dict:
    """Detailansicht eines Screenshots fuer das Per-Sprache-Modal."""
    idir = _resolve_idir_with_meta(platform, item_id)
    try:
        return assets.get_screenshot_details(idir, shot_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.put("/api/items/{platform}/{item_id}/assets/screenshots/{shot_id}/caption")
def api_screenshot_caption(platform: str, item_id: str, shot_id: int, body: _CaptionBody) -> dict:
    idir = _resolve_idir_with_meta(platform, item_id)
    try:
        shot = assets.set_screenshot_caption(idir, shot_id, body.text, lang=body.lang)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "screenshot": shot.model_dump()}


class _CaptionTranslateBody(BaseModel):
    # leer = alle aktiven Zielsprachen (außer master_lang)
    target_langs: list[str] | None = None
    overwrite_existing: bool = False
    engine: str | None = None


@app.post("/api/items/{platform}/{item_id}/assets/screenshots/{shot_id}/translate-captions")
def api_translate_screenshot_captions(
    platform: str, item_id: str, shot_id: int, body: _CaptionTranslateBody,
) -> dict:
    """Uebersetzt master_caption eines Screenshots in alle aktiven Zielsprachen.

    NT-564 Pass 2 (Lisbeth 17:17 MEDIUM FUNCTIONAL): captions waren nicht
    ans Translation-Pipeline angebunden — wird hier ueber das gleiche
    translator-Modul nachgereicht, das auch die Content-Felder uebersetzt.
    Standard ueberschreibt keine vorhandenen Captions (manuelle Edits
    bleiben). overwrite_existing=true zwingt das.
    """
    idir = _resolve_idir_with_meta(platform, item_id)
    meta = edit_ops.read_meta(idir)
    if body.target_langs is not None:
        target_langs = [l for l in body.target_langs if l != meta.master_lang]
    else:
        target_langs = [l for l in meta.active_languages if l != meta.master_lang]

    _VALID_ENGINES = {None, "mock", "claude", "claude-cli"}
    if body.engine not in _VALID_ENGINES:
        raise HTTPException(
            status_code=400,
            detail=f"Unbekannte engine '{body.engine}'. Erlaubt: mock | claude | claude-cli | (leer = Default).",
        )
    if body.engine == "mock":
        tx = translator.MockTranslator()
    elif body.engine in ("claude", "claude-cli"):
        tx = translator.ClaudeCliTranslator()
    else:
        tx = translator.get_translator()

    # NT-563 (Lisbeth 18:03 LOW FUNCTIONAL): glossary.json kontrolliert laden —
    # ein korruptes Glossar darf nicht als 500 durchschlagen, sondern als 422.
    # NT-563 (Lisbeth 18:03/18:39 LOW FUNCTIONAL): Glossar kontrolliert laden UND
    # formatieren. to_prompt_block() lief vorher ausserhalb des try -> ein
    # JSON-valides aber schema-kaputtes Glossar (z.B. entries mit Nicht-Dicts)
    # schlug als 500 durch. Beides im try, breiter Catch -> 422.
    try:
        g = glossary_mod.load(idir)
        glossary_block = glossary_mod.to_prompt_block(g)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError, ValidationError,
            ValueError, TypeError, KeyError, AttributeError) as e:
        raise HTTPException(status_code=422, detail=f"glossary.json unlesbar/ungueltig: {e}")

    def translate_one(lang: str, text: str) -> str | None:
        """Einzel-Caption durch den Translator schicken — Engine-API ist
        feld-orientiert, also packen wir die caption als pseudo-feld 'caption'."""
        if not text.strip():
            return None
        try:
            out = tx.translate_lang(
                master_fields={"caption": text},
                lang_code=lang,
                lang_display=steam_codes.get(lang).display,
                glossary_block=glossary_block,
                style_block="",
            )
        except translator.TranslationError as e:
            raise HTTPException(status_code=502, detail=f"{lang}: {e}")
        return out.get("caption")

    try:
        written, errors = assets.translate_screenshot_captions(
            idir, shot_id,
            target_langs=target_langs,
            translate_fn=translate_one,
            overwrite_existing=body.overwrite_existing,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "ok": True,
        "shot_id": shot_id,
        "written": written,
        "errors": errors,
        "engine": tx.name,
    }


@app.get("/api/items/{platform}/{item_id}/assets/screenshots/{shot_id}/file")
def api_screenshot_file(platform: str, item_id: str, shot_id: int, lang: str | None = None):
    # NT-563 Pass 3 (Lisbeth 17:25 LOW FUNCTIONAL): vorher _resolve_idir()
    # ohne meta-Pruefung — legacy/malformed items konnten so Binaerdaten
    # ausliefern, obwohl die uebrigen Asset-Routen den Item-Status bereits
    # ablehnen. Jetzt konsistent _resolve_idir_with_meta() wie die anderen
    # asset/screenshot-Endpoints.
    idir = _resolve_idir_with_meta(platform, item_id)
    manifest = assets.load_manifest(idir)
    shot = next((s for s in manifest.screenshots if s.id == shot_id), None)
    if shot is None:
        raise HTTPException(status_code=404, detail=f"Screenshot {shot_id} nicht gefunden")
    # NT-564/565 (Lisbeth 18:16/18:22 LOW FUNCTIONAL): gleiche stale-Override-
    # Fallback-Semantik wie resolve_asset()/export_zip(). Ein verwaister
    # Per-Sprache-Override (Manifest-Eintrag da, Datei weg) faellt auf das
    # Default-Bild zurueck statt 404 — sonst bricht die Vorschau fuer Items,
    # die der Rest der Asset-Logik als wiederherstellbar behandelt.
    sdir = idir / "assets" / "screenshots"
    entry = None
    if lang and lang != assets.DEFAULT_KEY:
        ov = shot.localized.get(lang)
        if ov is not None and (sdir / ov.filename).exists():
            entry = ov
    if entry is None and shot.default is not None and (sdir / shot.default.filename).exists():
        entry = shot.default
    if entry is None:
        raise HTTPException(status_code=404, detail="Kein Bild fuer diesen Screenshot")
    return FileResponse(sdir / entry.filename)


@app.delete("/api/items/{platform}/{item_id}/assets/screenshots/{shot_id}")
def api_delete_screenshot(platform: str, item_id: str, shot_id: int) -> dict:
    idir = _resolve_idir_with_meta(platform, item_id)
    removed = assets.delete_screenshot(idir, shot_id)
    if not removed:
        raise HTTPException(status_code=404, detail=f"Screenshot {shot_id} nicht gefunden")
    return {"ok": True}


@app.post("/api/items/{platform}/{item_id}/assets/{slot}")
async def api_upload_asset(
    platform: str, item_id: str, slot: str,
    file: UploadFile = File(...), lang: str | None = Form(None),
) -> dict:
    idir = _resolve_idir_with_meta(platform, item_id)
    data = await file.read()
    ext = _ext_of(file.filename)
    try:
        entry = assets.store_asset(idir, slot, data, lang=lang, ext=ext)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "entry": entry.model_dump()}


@app.get("/api/items/{platform}/{item_id}/assets/{slot}/file")
def api_asset_file(platform: str, item_id: str, slot: str, lang: str | None = None):
    # NT-563 Pass 4 (Lisbeth 17:45 LOW FUNCTIONAL): vorher _resolve_idir() ohne
    # meta-Pruefung — legacy/malformed items konnten Asset-Binaerdaten ueber
    # /assets/{slot}/file ausliefern, obwohl die uebrigen Asset-Routen den
    # Item-Status bereits ablehnen. Konsistent mit upload/delete/status nun
    # _resolve_idir_with_meta().
    idir = _resolve_idir_with_meta(platform, item_id)
    try:
        p = assets.resolve_asset(idir, slot, lang)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if p is None:
        raise HTTPException(status_code=404, detail="Kein Asset fuer diesen Slot/diese Sprache")
    return FileResponse(p)


@app.delete("/api/items/{platform}/{item_id}/assets/{slot}")
def api_delete_asset(platform: str, item_id: str, slot: str, lang: str | None = None) -> dict:
    idir = _resolve_idir_with_meta(platform, item_id)
    try:
        removed = assets.delete_asset(idir, slot, lang)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "removed": removed}


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
