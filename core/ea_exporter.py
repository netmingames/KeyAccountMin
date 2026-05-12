"""Early-Access-Text-Export pro Sprache (NT-551).

Steam pflegt die EA-Q&A-Felder im Backend in einem separaten Bereich, der
NICHT ueber das Lokalisierungs-JSON gefuettert wird. Sid exportiert die
EA-Felder daher als Plaintext-Bundle, das Thomas pro Sprache 1:1 ins
Steam-EA-Tab kopiert.

Output-Format pro Sprache (.txt):
    # <Item-Name> — Early Access Q&A — <Sprachname>

    ## Warum Early Access?
    <Antwort>

    ## Wie lange wird das Spiel im Early Access bleiben?
    <Antwort>

    ...

Plus ein Sammel-ZIP mit allen aktivierten Sprachen.
"""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from pydantic import ValidationError

from . import edit_ops, labels, schema, steam_codes, storage


def _values_for_lang(idir: Path, lang: str) -> tuple[dict[str, str], bool]:
    """Liefert (values, corrupt) fuer eine Sprache.

    Master-Sprache: direkt aus master.fields.
    Andere Sprachen: aus translation.fields[..].value, leer wenn fehlt.

    Lisbeth NT-549 Pass 8 (16:33 MEDIUM FUNCTIONAL): per-lang Error-Isolation
    wie im Haupt-Exporter (core/exporter.py) — eine kaputte translations/<lang>.json
    darf nicht den ganzen EA-Flow killen. Bei JSON-/UTF-8-/IO-/Schema-Fehlern
    wird die Sprache als leer behandelt und corrupt=True gemeldet, damit Caller
    den Zustand im UI/ZIP-README sichtbar machen koennen.
    """
    meta = edit_ops.read_meta(idir)
    empty = {f: "" for f in schema.STEAM_FIELDS_EA}
    if lang == meta.master_lang:
        master = edit_ops.read_master(idir)
        return {f: master.fields.get(f, "") for f in schema.STEAM_FIELDS_EA}, False
    tpath = storage.translation_path(idir, lang)
    if not tpath.exists():
        return empty, False
    try:
        t = schema.TranslationDocument(**storage.read_json(tpath))
    except (ValidationError, json.JSONDecodeError, OSError, UnicodeDecodeError):
        return empty, True
    return {f: (t.fields.get(f).value if t.fields.get(f) else "") for f in schema.STEAM_FIELDS_EA}, False


def render_ea_text(idir: Path, lang: str) -> str:
    """Erzeugt den Plaintext-Block fuer eine Sprache.

    Eine korrupte translations/<lang>.json fuehrt zu einem leeren Block mit
    "(noch nicht ausgefuellt)" + Warn-Hinweis im Header, nicht zu einem 500.
    """
    if not steam_codes.is_valid(lang):
        raise ValueError(f"Unbekannter Steam-Sprachcode: {lang}")
    meta = edit_ops.read_meta(idir)
    lang_obj = steam_codes.get(lang)
    values, corrupt = _values_for_lang(idir, lang)

    lines: list[str] = []
    lines.append(f"# {meta.name} — Early Access Q&A — {lang_obj.display}")
    if corrupt:
        lines.append(
            f"# WARNUNG: translations/{lang}.json war unlesbar (kaputtes JSON/UTF-8) "
            f"— Texte werden leer ausgegeben, bitte Quelldatei pruefen."
        )
    lines.append("")
    for f in schema.STEAM_FIELDS_EA:
        question = labels.label(f)
        answer = (values.get(f, "") or "").strip()
        lines.append(f"## {question}")
        if answer:
            lines.append(answer)
        else:
            lines.append("(noch nicht ausgefuellt)")
        lines.append("")
    return "\n".join(lines)


def filename_for_lang(idir: Path, lang: str) -> str:
    """Schlaegt einen sprechenden Dateinamen vor: <itemslug>_ea_<lang>.txt."""
    meta = edit_ops.read_meta(idir)
    slug = storage._slugify(meta.name)
    return f"{slug}_ea_{lang}.txt"


def export_ea_bundle_zip(idir: Path) -> bytes:
    """Erzeugt eine ZIP mit allen aktiven Sprachen als .txt + ein README.

    Returns: bytes (in-memory ZIP, kann direkt als Response zurueckgehen).

    Lisbeth NT-549 Pass 8 (16:33): eine kaputte translations/<lang>.json
    fuehrt nicht zum Abbruch der ZIP — diese Sprache wird mit leerem Inhalt
    geschrieben und im README als 'uebersprungen' aufgelistet.
    """
    meta = edit_ops.read_meta(idir)
    if not meta.early_access:
        raise ValueError("Early Access ist fuer dieses Item nicht aktiviert")

    buf = io.BytesIO()
    skipped: list[str] = []
    lang_texts: list[tuple[str, str]] = []
    for lang in meta.active_languages:
        _, corrupt = _values_for_lang(idir, lang)
        if corrupt:
            skipped.append(lang)
        # render_ea_text ist ebenfalls error-tolerant (gleiches Helper),
        # liefert bei corrupt einen Warn-Block — also immer schreibbar.
        lang_texts.append((filename_for_lang(idir, lang), render_ea_text(idir, lang)))

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        readme = (
            f"# {meta.name} — Early Access Q&A Bundle\n"
            f"\n"
            f"Diese Texte gehoeren in den Steam-Partner-Backend Bereich:\n"
            f"  Edit Steamworks Settings → Early Access\n"
            f"\n"
            f"NICHT in den Lokalisierungs-JSON-Upload — die EA-Felder werden\n"
            f"von Steam in einem separaten Bereich verwaltet.\n"
            f"\n"
            f"Eine .txt pro Sprache. Pro Sprache: 6 Q&A-Bloecke,\n"
            f"jeweils Frage als ## Heading, Antwort als naechste Zeile.\n"
            f"Copy/Paste vom Antwort-Block in das passende Feld in Steamworks.\n"
        )
        if skipped:
            readme += (
                f"\n"
                f"WARNUNG: Bei folgenden Sprachen war translations/<lang>.json\n"
                f"unlesbar (kaputtes JSON/UTF-8); ihre .txt-Datei enthaelt nur\n"
                f"Platzhalter. Bitte Quelldateien pruefen:\n"
                f"  {', '.join(skipped)}\n"
            )
        zf.writestr("README.md", readme)
        for name, text in lang_texts:
            zf.writestr(name, text)
    buf.seek(0)
    return buf.getvalue()


def list_ea_languages(idir: Path) -> list[dict]:
    """Liste der aktiven Sprachen mit Indikator wie viele EA-Felder befuellt sind.

    Lisbeth NT-549 Pass 8: kaputte translations/<lang>.json setzen ``corrupt=True``
    statt den ganzen Status-Endpoint kollabieren zu lassen.
    """
    meta = edit_ops.read_meta(idir)
    out = []
    for lang in meta.active_languages:
        values, corrupt = _values_for_lang(idir, lang)
        n_filled = sum(1 for v in values.values() if v)
        l = steam_codes.get(lang)
        out.append({
            "code": lang,
            "display": l.display,
            "iso": l.iso,
            "filled": n_filled,
            "total": len(schema.STEAM_FIELDS_EA),
            "is_master": lang == meta.master_lang,
            "corrupt": corrupt,
        })
    return out
