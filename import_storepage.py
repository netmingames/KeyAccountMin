"""CLI-Importer fuer Steam-Loka-JSONs (NT-547).

Liest eine `storepage_<appid>_all.json` aus dem Steam-Partner-Backend,
erzeugt:
- meta.json     (Item-ID, Plattform, aktive Sprachen, Master = "german")
- master_de.json (Master mit allen DE-Werten)
- translations/<lang>.json fuer jede der 28 weiteren Steam-Sprachen,
  pro Feld mit value, stale-Flag, source_hash, last_translated_at.

Verwendung:
    python import_storepage.py <pfad-zur-json> [--name "Anzeigename"] [--force]
    python import_storepage.py <pfad-zur-json> --inspect

Re-Import-Strategie:
- Erst-Import: alles wird geschrieben.
- Re-Import in einen bestehenden Ordner:
  * Master-Werte: nicht-leere DE-Werte aus dem JSON werden mit dem aktuellen
    Master verglichen. Bei Konflikten wird abgebrochen, es sei denn --force.
    --force ueberschreibt den Master.
  * Translations: nicht-leere Werte aus dem JSON werden importiert. Felder die
    in Sid `manually_edited=True` haben, werden NICHT ueberschrieben (auch
    nicht mit --force) — Thomas-Edits bleiben heilig.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from core import schema, steam_codes, steam_mapping, storage

DEFAULT_DATA_ROOT = Path(__file__).resolve().parent / "data"


def _load_steam_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _extract_field_values(lang_block: dict) -> dict[str, str]:
    """Steam-Block (eine Sprache) -> {sid_field_name: value}.

    Unbekannte Steam-Keys werden ignoriert (mit Hinweis).
    """
    out: dict[str, str] = {}
    unknown: list[str] = []
    for steam_key, value in lang_block.items():
        if steam_key in steam_mapping.STEAM_TO_FIELD:
            field = steam_mapping.STEAM_TO_FIELD[steam_key]
            out[field] = (value or "").strip()
        else:
            unknown.append(steam_key)
    if unknown:
        print(f"   Unbekannte Steam-Keys ignoriert: {unknown}", file=sys.stderr)
    return out


def cmd_inspect(steam_json: Path) -> int:
    data = _load_steam_json(steam_json)
    item_id = data.get("itemid", "<unbekannt>")
    langs = list(data.get("languages", {}).keys())
    de = data.get("languages", {}).get("german", {})
    en = data.get("languages", {}).get("english", {})
    print(f"Item-ID:        {item_id}")
    print(f"Sprachen:       {len(langs)} ({', '.join(langs[:8])}{'...' if len(langs) > 8 else ''})")
    print(f"DE-Felder:      {len([v for v in de.values() if v])} befuellt / {len(de)} gesamt")
    print(f"EN-Felder:      {len([v for v in en.values() if v])} befuellt / {len(en)} gesamt")
    leer = [c for c in steam_codes.CODES if not any(data.get("languages", {}).get(c, {}).values())]
    print(f"Leere Sprachen: {len(leer)} ({', '.join(leer[:5])}{'...' if len(leer) > 5 else ''})")
    return 0


def cmd_import(steam_json: Path, name: str | None, force: bool, data_root: Path, platform: str = "steam") -> int:
    data = _load_steam_json(steam_json)
    item_id = str(data.get("itemid", "")).strip()
    if not item_id:
        print("Fehler: itemid fehlt im JSON", file=sys.stderr)
        return 2

    languages = data.get("languages", {})
    if not languages:
        print("Fehler: 'languages' Block fehlt", file=sys.stderr)
        return 2

    # Item-Ordner ermitteln (vorhanden oder neu)
    try:
        idir = storage.item_dir(data_root, platform, item_id)
        is_new = False
    except FileNotFoundError:
        if name is None:
            print(
                f"Fehler: kein bestehender Ordner fuer item_id={item_id} gefunden — "
                "beim Erst-Import bitte --name <Anzeigename> mitgeben",
                file=sys.stderr,
            )
            return 2
        idir = storage.item_dir(data_root, platform, item_id, name)
        idir.mkdir(parents=True, exist_ok=True)
        (idir / "translations").mkdir(exist_ok=True)
        (idir / "assets").mkdir(exist_ok=True)
        (idir / "exports").mkdir(exist_ok=True)
        is_new = True

    print(f"Item-Ordner:   {idir} ({'NEU' if is_new else 'bestehend'})")
    print(f"Item-ID:       {item_id}")

    # --- Master (DE) ----------------------------------------------------------
    master_block = languages.get(steam_codes.MASTER_CODE, {})
    new_master_fields = _extract_field_values(master_block)

    # Vollstaendiges Schema-Skelett als Basis: ALLE Felder aus schema.all_fields()
    # werden angelegt — auch wenn Steam sie nicht liefert (z.B. sysreqs_rec_*,
    # ea_*). So ist das Master-File schemakomplett, Translations koennen alle
    # Felder tracken, und Thomas kann fehlende Felder im UI pflegen.
    schema_skeleton: dict[str, str] = {f: "" for f in schema.all_fields(early_access=True)}

    master_file = storage.master_path(idir, "de")
    # Nur non-empty Werte aus dem Steam-JSON werden in den Merge uebernommen.
    # Steam liefert fuer fehlende/leere Felder einen leeren String — wuerden
    # die mit-uebernommen, koennten sie kuratierte Master-Texte beim Re-Import
    # ueberschreiben (Lisbeth NT-548 12:37, MEDIUM FUNCTIONAL).
    new_master_nonempty = {f: v for f, v in new_master_fields.items() if v}
    if master_file.exists() and not is_new:
        existing_master = schema.MasterDocument(**storage.read_json(master_file))
        conflicts = []
        for field, new_val in new_master_nonempty.items():
            old_val = existing_master.fields.get(field, "")
            if old_val and old_val != new_val:
                conflicts.append((field, old_val, new_val))
        if conflicts and not force:
            print(f"\nKONFLIKT: Master-DE hat {len(conflicts)} Feld(er), die im JSON anders sind.")
            print("Verwende --force, um den Master zu ueberschreiben. Liste:")
            for f, old, new in conflicts[:5]:
                print(f"  - {f}:  alt={_short(old)}  neu={_short(new)}")
            if len(conflicts) > 5:
                print(f"  ... ({len(conflicts) - 5} weitere)")
            return 3
        if conflicts and force:
            print(f"--force aktiv: {len(conflicts)} Master-Felder werden ueberschrieben")
        # Merge-Reihenfolge: Skelett (alle Schema-Felder leer) -> bestehende
        # Werte -> neue NICHT-LEERE Werte aus JSON. So bleiben fehlende Felder,
        # die das Schema kennt aber Steam nicht liefert, als leere Strings
        # erhalten, ohne dass bestehende Master-Eintraege ueberschrieben werden.
        merged_master = dict(schema_skeleton)
        merged_master.update(existing_master.fields)
        merged_master.update(new_master_nonempty)
    else:
        merged_master = dict(schema_skeleton)
        merged_master.update(new_master_nonempty)

    master_doc = schema.MasterDocument(
        item_id=item_id,
        lang="de",
        fields=merged_master,
        updated_at=storage.now_iso(),
    )

    storage.write_json_atomic(master_file, master_doc.model_dump())
    print(f"Master-DE:     {master_file.name} ({len([v for v in master_doc.fields.values() if v])} Felder befuellt)")

    # --- Translations ---------------------------------------------------------
    written_translations = 0
    skipped_protected_total = 0
    for lang in steam_codes.CODES:
        if lang == steam_codes.MASTER_CODE:
            continue
        new_field_values = _extract_field_values(languages.get(lang, {}))
        tpath = storage.translation_path(idir, lang)
        if tpath.exists():
            existing = schema.TranslationDocument(**storage.read_json(tpath))
            existing_fields = existing.fields
        else:
            existing_fields = {}

        merged_fields: dict[str, schema.TranslationField] = {}
        skipped_protected = 0

        # alle bekannten Master-Felder aufnehmen, auch leere -> stale=True
        for field in master_doc.fields.keys():
            new_val = new_field_values.get(field, "")
            existing_field = existing_fields.get(field)
            master_val = master_doc.fields.get(field, "")
            master_hash = storage.sha256_text(master_val) if master_val else ""

            if existing_field and existing_field.manually_edited:
                # Manuelle Edits sind heilig — auch mit --force nicht ueberschreiben
                merged_fields[field] = existing_field
                # stale neu berechnen (Master koennte sich gegenueber dem manual edit
                # geaendert haben)
                merged_fields[field].stale = (master_hash != existing_field.source_hash)
                skipped_protected += 1
                continue

            if new_val:
                merged_fields[field] = schema.TranslationField(
                    value=new_val,
                    source_hash=master_hash,  # frisch importiert -> in sync
                    stale=False,
                    manually_edited=False,
                    last_translated_at=storage.now_iso(),
                )
            else:
                # Leerer Wert -> stale, wartet auf Auto-Translate
                merged_fields[field] = schema.TranslationField(
                    value=existing_field.value if existing_field else "",
                    source_hash=existing_field.source_hash if existing_field else "",
                    stale=True,
                    manually_edited=False,
                    last_translated_at=existing_field.last_translated_at if existing_field else "",
                )

        doc = schema.TranslationDocument(
            item_id=item_id,
            lang=lang,
            fields=merged_fields,
            updated_at=storage.now_iso(),
        )
        # serialisieren: TranslationField -> dict
        out = doc.model_dump()
        storage.write_json_atomic(tpath, out)
        written_translations += 1
        skipped_protected_total += skipped_protected

    print(f"Translations:  {written_translations} Sprachen geschrieben "
          f"(manuelle Edits geschuetzt: {skipped_protected_total})")

    # --- Meta -----------------------------------------------------------------
    # active_languages = persistente UI-Auswahl des Users (welche Sprachen er
    # in Sid pflegen will). Beim Re-Import bleibt die existierende Liste
    # unangetastet — sonst wuerden manuell aktivierte Sprachen verschwinden,
    # wenn die naechste Steam-JSON-Lieferung sie nicht enthaelt
    # (Lisbeth NT-548 12:37, MEDIUM FUNCTIONAL — bewusste Revert-Bewegung
    # gegenueber NT-547 LOW-Finding).
    # Beim Erst-Import dient die Steam-JSON als Default-Auswahl: alle Sprachen,
    # die ueberhaupt befuellt sind, werden vorausgewaehlt.
    active_from_steam = [
        lang for lang in steam_codes.CODES
        if any(_extract_field_values(languages.get(lang, {})).values())
    ]
    meta_file = storage.meta_path(idir)
    if meta_file.exists():
        meta = schema.ItemMeta(**storage.read_json(meta_file))
        meta.updated_at = storage.now_iso()
    else:
        meta = schema.ItemMeta(
            item_id=item_id,
            platform=platform,
            name=name or f"Item {item_id}",
            master_lang=steam_codes.MASTER_CODE,
            active_languages=active_from_steam,
            early_access=False,
            created_at=storage.now_iso(),
            updated_at=storage.now_iso(),
        )
    storage.write_json_atomic(meta_file, meta.model_dump())
    print(f"Meta:          {meta_file.name} ({len(meta.active_languages)} aktive Sprachen)")

    return 0


def _short(s: str, n: int = 50) -> str:
    s = s.replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "..."


def main() -> int:
    parser = argparse.ArgumentParser(description="Sid Steam-Loka-JSON Importer")
    parser.add_argument("steam_json", type=Path, help="Pfad zu storepage_<appid>_all.json")
    parser.add_argument("--name", default=None, help="Anzeigename des Items (nur bei Erst-Import)")
    parser.add_argument("--force", action="store_true", help="ueberschreibt Master-Konflikte")
    parser.add_argument("--inspect", action="store_true", help="Nur Inhalt anzeigen, nichts schreiben")
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT, help="Sid data-Verzeichnis")
    parser.add_argument("--platform", default="steam", help="Plattform-Schluessel (default: steam)")
    args = parser.parse_args()

    if not args.steam_json.exists():
        print(f"Datei nicht gefunden: {args.steam_json}", file=sys.stderr)
        return 1

    if args.inspect:
        return cmd_inspect(args.steam_json)
    return cmd_import(args.steam_json, args.name, args.force, args.data_root, args.platform)


if __name__ == "__main__":
    sys.exit(main())
