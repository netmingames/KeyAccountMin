"""Steam-Loka-JSON Exporter (NT-550).

Erzeugt eine JSON-Datei im exakten Format des Steam-Partner-Backend-Downloads
(`storepage_<appid>_all.json`), die hochgeladen werden kann ueber den
Lokalisierungs-Tab.

Fehler-Semantik (NT-550 Pass 2 / Lisbeth 15:14 MEDIUM FUNCTIONAL):
- meta.json: wird VOR dem Aufruf in app.py via _resolve_idir_with_meta
  validiert -> 422 wenn schema-invalid. Hier nehmen wir an dass meta valide ist.
- master_*.json: Schema-Fehler propagiert (ValidationError) — das ist ein
  echter Bug, der dem User als 422 sichtbar werden soll (app.py catched).
- translations/<lang>.json: einzelne kaputte Sprache wird uebersprungen,
  in den Output kommt fuer diese Sprache nur ein leerer Block. Das verhindert
  dass eine korrupte Translation-Datei den ganzen Export killt.

Aufbau Steam-JSON:
{
  "itemid": "1141975",
  "languages": {
    "english": {
      "app[content][short_description]": "...",
      "app[content][about]": "...",
      "app[content][sysreqs][windows][min][osversion]": "...",
      "app[content][sysreqs][windows][min][processor]": "..."
    },
    "german": { ... },
    "french": { ... },          // leere Sprachen mit leeren Strings
    ...
  }
}

Konvention: Es werden NUR Felder exportiert, fuer die mindestens eine Sprache
(Master oder Translation) einen non-empty Wert hat. Damit ist das Output
strukturell identisch zum Steam-Sample bei einem Roundtrip (import + export).

Early-Access-Felder (ea_*) werden NICHT exportiert — Steam pflegt die in
einem separaten Backend-Bereich.
"""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

from pydantic import ValidationError

from . import edit_ops, schema, steam_codes, steam_mapping, storage


def export_steam_loka(idir: Path) -> dict:
    """Baut das Steam-Loka-JSON aus den Sid-Daten.

    Reihenfolge der Sprachen: wie in steam_codes.CODES (matches Sample).
    Reihenfolge der Felder pro Sprache: wie in steam_mapping.STEAM_TO_FIELD.
    """
    meta = edit_ops.read_meta(idir)
    master = edit_ops.read_master(idir)

    # Bestimme welche Standard-Felder exportiert werden.
    # NT-550 Pass 3 (Lisbeth 15:50 MEDIUM FUNCTIONAL): leere Master-Felder
    # werden EBENFALLS exportiert (mit ""), nicht weggelassen — das ist
    # explizite Akzeptanz im Ticket. Vorher hat `master.fields.get(f)`
    # bei "" falsy zurueckgegeben und das Feld komplett gedroppt.
    # Logik jetzt: ein Standard-Feld kommt rein, wenn es im Master-Dict
    # auftaucht (auch leer) ODER wenn irgendeine Translation Content hat.
    fields_to_export: set[str] = set()
    for f in schema.STEAM_FIELDS_STANDARD:
        if f in master.fields:
            fields_to_export.add(f)

    # Translation-Files cachen, damit wir sie nicht zweimal lesen.
    # Lisbeth NT-550 15:14 (MEDIUM FUNCTIONAL): einzelne kaputte
    # Translation-Datei darf nicht den ganzen Export killen — schluckend
    # skippen statt 500. Liste der geskippten Sprachen wird angehaengt.
    translation_cache: dict[str, schema.TranslationDocument] = {}
    skipped_translations: list[str] = []
    for lang_code in steam_codes.CODES:
        if lang_code == meta.master_lang:
            continue
        tpath = storage.translation_path(idir, lang_code)
        if not tpath.exists():
            continue
        try:
            t = schema.TranslationDocument(**storage.read_json(tpath))
        except (ValidationError, json.JSONDecodeError, OSError):
            skipped_translations.append(lang_code)
            continue
        translation_cache[lang_code] = t
        for f, tf in t.fields.items():
            if tf.value and f in schema.STEAM_FIELDS_STANDARD:
                fields_to_export.add(f)

    # Reihenfolge wie im Mapping (= wie im Steam-Sample)
    ordered_fields = [f for f in steam_mapping.FIELD_TO_STEAM.keys() if f in fields_to_export]

    languages: dict[str, dict[str, str]] = {}
    for lang_code in steam_codes.CODES:
        block: dict[str, str] = {}
        for f in ordered_fields:
            steam_key = steam_mapping.FIELD_TO_STEAM[f]
            if lang_code == meta.master_lang:
                value = master.fields.get(f, "")
            else:
                t = translation_cache.get(lang_code)
                if t and f in t.fields:
                    value = t.fields[f].value or ""
                else:
                    value = ""
            block[steam_key] = value
        languages[lang_code] = block

    return {
        "itemid": meta.item_id,
        "languages": languages,
        "skipped_translations": skipped_translations,
    }


def export_to_file(idir: Path) -> Path:
    """Schreibt den Export nach exports/<timestamp>.json und gibt den Pfad zurueck.

    Lisbeth NT-549/NT-550 (MEDIUM FUNCTIONAL): Filename hatte Sekunden-
    Praezision, zwei Exports in derselben Sekunde haetten die gleiche Datei
    ueberschrieben. Jetzt mit Microsekunden — und zur Sicherheit nochmal
    ein Kollisions-Check (sehr unwahrscheinlich, aber unueberschreibbar):
    falls der Pfad doch existiert, haengen wir einen 4-stelligen Counter an.
    """
    data = export_steam_loka(idir)
    exports_dir = idir / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)
    timestamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    base_name = f"storepage_{data['itemid']}_{timestamp}"
    out_path = exports_dir / f"{base_name}.json"
    counter = 0
    while out_path.exists():
        counter += 1
        out_path = exports_dir / f"{base_name}_{counter:04d}.json"
        if counter > 9999:
            raise RuntimeError(f"Export-Filename-Kollision unueberbrueckbar: {base_name}")
    # Steam-kompatibles JSON: nur itemid + languages. Diagnose-Felder
    # (skipped_translations) bleiben in der API-Response, nicht in der Datei.
    file_data = {"itemid": data["itemid"], "languages": data["languages"]}
    storage.write_json_atomic(out_path, file_data)
    return out_path


def list_exports(idir: Path) -> list[dict]:
    """Liste der bisherigen Export-Dateien (neueste zuerst)."""
    exports_dir = idir / "exports"
    if not exports_dir.exists():
        return []
    out = []
    for p in sorted(exports_dir.glob("*.json"), reverse=True):
        if p.name.startswith("."):
            continue
        try:
            stat = p.stat()
        except OSError:
            continue
        out.append({
            "filename": p.name,
            "size_bytes": stat.st_size,
            "modified_iso": _dt.datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        })
    return out


def export_summary(data: dict) -> dict:
    """Liefert kompakte Stats fuer eine Export-Vorschau im UI."""
    languages = data.get("languages", {})
    total_langs = len(languages)
    filled_langs = sum(1 for block in languages.values() if any(v for v in block.values()))
    n_fields_per_lang = len(next(iter(languages.values()))) if languages else 0
    total_chars = sum(
        len(v) for block in languages.values() for v in block.values() if isinstance(v, str)
    )
    return {
        "item_id": data.get("itemid"),
        "n_languages": total_langs,
        "n_languages_with_content": filled_langs,
        "n_fields_per_language": n_fields_per_lang,
        "total_chars": total_chars,
        "skipped_translations": list(data.get("skipped_translations") or []),
    }
