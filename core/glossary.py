"""Glossar-Verwaltung pro Item (NT-549).

Glossar-Schema (data/<plattform>/<item>/glossary.json):
{
  "schema_version": 1,
  "entries": [
    {
      "term": "Passage 5",         // wie es im DE-Master steht
      "rule": "keep",              // "keep" = nicht uebersetzen | "translate" = uebersetzen aber Hinweis dazu
      "note": "Eigenname, Spieltitel"   // freie Notiz fuer den Translator
    },
    ...
  ]
}

`rule=keep` heisst: der Translator darf den Begriff nicht uebersetzen, sondern
muss ihn 1:1 stehen lassen.

`rule=translate` mit `note` heisst: der Begriff darf uebersetzt werden, aber
der Hinweis (Marketing-Tone, branchenspezifisch, ...) ist zu beachten.
"""
from __future__ import annotations

from pathlib import Path

from . import storage

GLOSSARY_FILENAME = "glossary.json"
SCHEMA_VERSION = 1


def glossary_path(idir: Path) -> Path:
    return idir / GLOSSARY_FILENAME


def load(idir: Path) -> dict:
    """Liest das Glossar oder gibt ein leeres zurueck, wenn keines existiert."""
    p = glossary_path(idir)
    if not p.exists():
        return {"schema_version": SCHEMA_VERSION, "entries": []}
    data = storage.read_json(p)
    if "entries" not in data:
        data["entries"] = []
    if "schema_version" not in data:
        data["schema_version"] = SCHEMA_VERSION
    return data


def save(idir: Path, glossary: dict) -> None:
    """Atomic-write des Glossars. Validiert Schema (entries: list of dicts)."""
    if not isinstance(glossary.get("entries"), list):
        raise ValueError("Glossar 'entries' muss eine Liste sein")
    for e in glossary["entries"]:
        if not isinstance(e, dict) or "term" not in e:
            raise ValueError("Glossar-Eintrag muss 'term' haben")
        if e.get("rule") not in (None, "keep", "translate"):
            raise ValueError(f"Glossar-Eintrag-rule unbekannt: {e.get('rule')}")
    glossary["schema_version"] = SCHEMA_VERSION
    storage.write_json_atomic(glossary_path(idir), glossary)


def upsert_entry(idir: Path, term: str, rule: str = "keep", note: str = "") -> dict:
    g = load(idir)
    found = False
    for e in g["entries"]:
        if e["term"] == term:
            e["rule"] = rule
            e["note"] = note
            found = True
            break
    if not found:
        g["entries"].append({"term": term, "rule": rule, "note": note})
    save(idir, g)
    return g


def remove_entry(idir: Path, term: str) -> dict:
    g = load(idir)
    g["entries"] = [e for e in g["entries"] if e["term"] != term]
    save(idir, g)
    return g


def to_prompt_block(glossary: dict) -> str:
    """Formatiert das Glossar als Block fuer den Translator-Prompt."""
    entries = glossary.get("entries", [])
    if not entries:
        return ""
    keep = [e for e in entries if e.get("rule", "keep") == "keep"]
    note_only = [e for e in entries if e.get("rule") == "translate" and e.get("note")]
    parts = []
    if keep:
        parts.append("STRICT — diese Begriffe NICHT uebersetzen, woertlich uebernehmen:")
        for e in keep:
            line = f"  - {e['term']}"
            if e.get("note"):
                line += f"  ({e['note']})"
            parts.append(line)
    if note_only:
        parts.append("Begriffe mit besonderem Hinweis:")
        for e in note_only:
            parts.append(f"  - {e['term']}: {e['note']}")
    return "\n".join(parts)
