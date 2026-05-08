"""Edit-Operations auf den Sid-Daten (NT-548).

Kapselt das "richtige" Verhalten bei Master-Edits, Translation-Edits,
Sprachen-Aktivierung, Early-Access-Toggle. Wird von app.py konsumiert.

Invarianten (siehe schema.py):
- Master-Update: alle Translations re-stale-checken (master_hash != source_hash)
- Translation-Update via UI: manually_edited=True, source_hash=aktueller Master-Hash,
  stale=False, last_translated_at=now
- Active-Languages-Update: fuer neu aktivierte Sprache wird ein Stub-File angelegt
  (alle Master-Felder als TranslationField mit stale=True, value="")
- EA-Toggle on: Master + Translations werden um die EA-Felder erweitert
- EA-Toggle off: bestehende EA-Werte bleiben erhalten (kein Datenverlust)
"""
from __future__ import annotations

from pathlib import Path

from . import schema, steam_codes, storage


def read_meta(idir: Path) -> schema.ItemMeta:
    return schema.ItemMeta(**storage.read_json(storage.meta_path(idir)))


def write_meta(idir: Path, meta: schema.ItemMeta) -> None:
    meta.updated_at = storage.now_iso()
    storage.write_json_atomic(storage.meta_path(idir), meta.model_dump())


def read_master(idir: Path) -> schema.MasterDocument:
    """Liest den Master per Konvention master_de.json (ISO short der master_lang)."""
    meta = read_meta(idir)
    iso_short = steam_codes.get(meta.master_lang).iso.split("-")[0]
    return schema.MasterDocument(**storage.read_json(storage.master_path(idir, iso_short)))


def write_master(idir: Path, master: schema.MasterDocument) -> None:
    master.updated_at = storage.now_iso()
    storage.write_json_atomic(storage.master_path(idir, master.lang), master.model_dump())


def read_translation(idir: Path, lang: str) -> schema.TranslationDocument:
    return schema.TranslationDocument(**storage.read_json(storage.translation_path(idir, lang)))


def write_translation(idir: Path, t: schema.TranslationDocument) -> None:
    t.updated_at = storage.now_iso()
    storage.write_json_atomic(storage.translation_path(idir, t.lang), t.model_dump())


def _existing_translation_langs(idir: Path) -> list[str]:
    """Listet alle Sprachen, fuer die bereits eine translations/<lang>.json existiert.

    Why: Master-Edits und EA-Toggles muessen alle vorhandenen Translation-
    Files re-stalen / erweitern, nicht nur die in meta.active_languages.
    Sonst bleiben inaktive Files schema-incomplete oder zeigen veraltete
    Stale-Flags, sobald sie spaeter aktiviert werden.
    """
    tdir = idir / "translations"
    if not tdir.exists():
        return []
    return sorted(p.stem for p in tdir.glob("*.json") if steam_codes.is_valid(p.stem))


def update_master_field(idir: Path, field: str, new_value: str) -> dict:
    """Setzt einen Master-Feld-Wert und re-staled alle Translations.

    Returns: dict mit zusammenfassenden Counts fuer die UI.
    """
    master = read_master(idir)
    if field not in schema.STEAM_FIELDS_STANDARD and field not in schema.STEAM_FIELDS_EA:
        raise ValueError(f"Unbekanntes Feld: {field}")
    master.fields[field] = new_value
    write_master(idir, master)

    new_hash = storage.sha256_text(new_value) if new_value else ""
    meta = read_meta(idir)
    affected_stale = 0
    affected_manual = 0
    for lang in _existing_translation_langs(idir):
        if lang == meta.master_lang:
            continue
        t = read_translation(idir, lang)
        if field in t.fields:
            tf = t.fields[field]
            new_stale = (new_hash != tf.source_hash)
            if tf.stale != new_stale:
                tf.stale = new_stale
                if new_stale:
                    affected_stale += 1
            if tf.manually_edited:
                affected_manual += 1
            write_translation(idir, t)
    return {
        "field": field,
        "stale_after": affected_stale,
        "still_manual": affected_manual,
    }


def update_translation_field(
    idir: Path,
    lang: str,
    field: str,
    new_value: str,
    *,
    via_translation_engine: bool = False,
) -> dict:
    """Setzt einen Translation-Wert.

    via_translation_engine=False (Default = UI-Edit):
        - manually_edited=True (heilig — Re-Import oder Re-Translate ueberspringt)
        - source_hash = aktueller Master-Hash (in sync)
        - stale=False
    via_translation_engine=True (NT-549 Auto-Translate):
        - manuell editierte Felder werden NICHT ueberschrieben (skipped=True)
        - sonst: manually_edited bleibt False, source_hash = aktueller Master-Hash,
          stale=False
    """
    if not steam_codes.is_valid(lang):
        raise ValueError(f"Unbekannte Sprache: {lang}")
    if field not in schema.STEAM_FIELDS_STANDARD and field not in schema.STEAM_FIELDS_EA:
        raise ValueError(f"Unbekanntes Feld: {field}")
    master = read_master(idir)
    master_hash = storage.sha256_text(master.fields.get(field, "")) if master.fields.get(field) else ""
    tpath = storage.translation_path(idir, lang)
    if tpath.exists():
        t = read_translation(idir, lang)
    else:
        meta = read_meta(idir)
        t = schema.TranslationDocument(item_id=meta.item_id, lang=lang, fields={})

    tf = t.fields.get(field) or schema.TranslationField()
    if via_translation_engine and tf.manually_edited:
        # Manuelle Edits sind heilig — Auto-Translate darf sie nicht ueberschreiben.
        return {
            "field": field,
            "lang": lang,
            "manually_edited": True,
            "skipped": True,
        }
    tf.value = new_value
    tf.source_hash = master_hash
    tf.stale = False
    if not via_translation_engine:
        tf.manually_edited = True
    tf.last_translated_at = storage.now_iso()
    t.fields[field] = tf
    write_translation(idir, t)
    return {
        "field": field,
        "lang": lang,
        "manually_edited": tf.manually_edited,
    }


def set_active_languages(idir: Path, langs: list[str]) -> dict:
    """Aendert die Liste der aktiven Sprachen.

    Neu aktivierte Sprachen bekommen ein Translation-Stub-File mit allen
    Master-Feldern auf stale=True. Bereits existierende Files werden nicht
    angetastet (auch nicht wenn Sprache deaktiviert wird — Daten bleiben).
    """
    meta = read_meta(idir)
    master = read_master(idir)
    # Validierung
    invalid = [l for l in langs if not steam_codes.is_valid(l)]
    if invalid:
        raise ValueError(f"Unbekannte Sprachen: {invalid}")
    if meta.master_lang not in langs:
        # Master muss immer drin sein
        langs = [meta.master_lang] + [l for l in langs if l != meta.master_lang]

    new_added: list[str] = []
    for lang in langs:
        if lang == meta.master_lang:
            continue
        tpath = storage.translation_path(idir, lang)
        if not tpath.exists():
            stub = schema.TranslationDocument(
                item_id=meta.item_id,
                lang=lang,
                fields={
                    f: schema.TranslationField(stale=True) for f in master.fields.keys()
                },
            )
            write_translation(idir, stub)
            new_added.append(lang)

    meta.active_languages = langs
    write_meta(idir, meta)
    return {"active_languages": meta.active_languages, "new_stubs": new_added}


def set_early_access(idir: Path, enabled: bool) -> dict:
    """Schaltet Early-Access-Felder fuer ein Item frei oder aus.

    On: fuegt EA-Felder zu Master + allen Translations hinzu (leer, stale).
    Off: bestehende Felder bleiben in den Files. Nur das Flag wechselt.
    """
    meta = read_meta(idir)
    meta.early_access = enabled
    write_meta(idir, meta)

    if not enabled:
        return {"early_access": False}

    master = read_master(idir)
    added_master_fields = []
    for ea in schema.STEAM_FIELDS_EA:
        if ea not in master.fields:
            master.fields[ea] = ""
            added_master_fields.append(ea)
    if added_master_fields:
        write_master(idir, master)

    added_translation_fields = 0
    # Scope: alle existierenden Translation-Files, nicht nur active_languages.
    # Sonst bleiben spaeter aktivierte Sprachen schema-incomplete.
    for lang in _existing_translation_langs(idir):
        if lang == meta.master_lang:
            continue
        t = read_translation(idir, lang)
        for ea in schema.STEAM_FIELDS_EA:
            if ea not in t.fields:
                t.fields[ea] = schema.TranslationField(stale=True)
                added_translation_fields += 1
        write_translation(idir, t)

    return {
        "early_access": True,
        "master_fields_added": added_master_fields,
        "translation_fields_added": added_translation_fields,
    }


def create_item(
    data_root: Path,
    platform: str,
    item_id: str,
    name: str,
    master_lang: str = "german",
    early_access: bool = False,
) -> Path:
    """Legt ein neues Item an mit leerem Master + ohne Translations.

    Returns: der angelegte Item-Ordner.
    """
    if not steam_codes.is_valid(master_lang):
        raise ValueError(f"Unbekannte Master-Sprache: {master_lang}")
    # Konflikt-Pruefung via storage.item_dir (validiert platform/item_id und
    # findet Existing per Praefix). FileNotFoundError = ok, neu anlegbar.
    try:
        existing = storage.item_dir(data_root, platform, item_id)
        raise FileExistsError(f"Item {item_id} existiert bereits: {existing}")
    except FileNotFoundError:
        pass

    idir = storage.item_dir(data_root, platform, item_id, name)
    idir.mkdir(parents=True, exist_ok=True)
    (idir / "translations").mkdir(exist_ok=True)
    (idir / "assets").mkdir(exist_ok=True)
    (idir / "exports").mkdir(exist_ok=True)

    # Master mit leeren Feldern
    fields = {f: "" for f in schema.STEAM_FIELDS_STANDARD}
    if early_access:
        fields.update({f: "" for f in schema.STEAM_FIELDS_EA})
    iso_short = steam_codes.get(master_lang).iso.split("-")[0]
    master = schema.MasterDocument(
        item_id=item_id,
        lang=iso_short,
        fields=fields,
        updated_at=storage.now_iso(),
    )
    write_master(idir, master)

    meta = schema.ItemMeta(
        item_id=item_id,
        platform=platform,
        name=name,
        master_lang=master_lang,
        active_languages=[master_lang],
        early_access=early_access,
        created_at=storage.now_iso(),
        updated_at=storage.now_iso(),
    )
    write_meta(idir, meta)
    return idir
