"""JSON-IO mit atomic-write (NT-547).

Sid ist ein Single-User-Tool, daher kein hartes Locking — aber atomic-Replace,
damit halb-geschriebene Dateien (z.B. wenn der Prozess waehrend des Schreibens
gekillt wird) nicht moeglich sind.

Pattern: write-temp + os.replace. os.replace ist atomar auf NTFS und POSIX.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    """Atomic-Write: schreibt in eine Tmp-Datei im selben Verzeichnis, dann replace.

    Atomar gegen Crash-mid-write. Nicht atomar gegen parallele Writer
    (das brauchen wir bei Single-User aber nicht).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=False)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


_SEGMENT_FORBIDDEN = {"", ".", ".."}


def _validate_path_segment(value: str, name: str) -> None:
    """Schuetzt vor Path-Traversal: kein ``..``, keine Separatoren, kein Null-Byte.

    Why: ``platform`` und ``item_id`` kommen aus HTTP-Requests und werden zu
    Verzeichnisnamen. Ohne Validierung kann ein Angreifer mit ``..`` oder
    ``a/b`` aus ``data_root`` ausbrechen und beliebige Pfade beschreiben.
    """
    if not isinstance(value, str) or value in _SEGMENT_FORBIDDEN:
        raise ValueError(f"Ungueltiger {name}: {value!r}")
    if any(sep in value for sep in ("/", "\\", "\x00")) or ":" in value:
        raise ValueError(f"Ungueltiger {name} (verbotene Zeichen): {value!r}")


def item_dir(data_root: Path, platform: str, item_id: str, name: str | None = None) -> Path:
    """Pfad zum Item-Ordner: data/<platform>/<item_id>_<slug>/.

    Wenn name fehlt, wird der bestehende Ordner gesucht. Wenn keiner existiert
    und name gesetzt ist, wird ein neuer Ordner-Pfad zurueckgegeben (nicht
    angelegt). Lookup-Pfade legen nichts an (kein mkdir auf Read-Pfad).

    Match-Strategie:
    1. Primaer: exakter Vergleich mit ``meta.item_id``. Praefix-Match ueber
       den Verzeichnisnamen reicht nicht aus — bei item_ids, die einander
       als Praefix enthalten ("1" vs "1_2"), matched ein Lookup auf "1"
       sonst faelschlich auch "1_2_..."-Ordner.
    2. Fallback (Lisbeth NT-548 13:02): wenn der Folder kein lesbares
       ``meta.json`` hat (legacy / partially written) und sein Name mit
       ``{item_id}_`` anfaengt, gilt er als Match. Verhindert dass ein
       Re-Import per ``--name`` einen Duplicate-Folder neben dem
       (defekten) Original anlegt.
    """
    _validate_path_segment(platform, "platform")
    _validate_path_segment(item_id, "item_id")
    base = data_root / platform
    legacy_match: Path | None = None
    if base.exists():
        for d in base.iterdir():
            if not d.is_dir():
                continue
            mp = d / "meta.json"
            meta_readable = False
            if mp.exists():
                try:
                    with open(mp, encoding="utf-8") as f:
                        meta = json.load(f)
                    meta_readable = True
                    if meta.get("item_id") == item_id:
                        return d
                    # meta lesbar mit anderer ID -> kein Fallback fuer diesen Folder
                except (json.JSONDecodeError, OSError):
                    pass
            if not meta_readable and d.name.startswith(f"{item_id}_"):
                legacy_match = d
        if legacy_match is not None:
            return legacy_match
    if name is None:
        raise FileNotFoundError(f"Item {item_id} unter {base} nicht gefunden")
    slug = _slugify(name)
    return base / f"{item_id}_{slug}"


def _slugify(text: str) -> str:
    out = []
    for ch in text.lower():
        if ch.isalnum():
            out.append(ch)
        elif ch in ("-", "_"):
            out.append("_")
        # Whitespace wird verschluckt: "Passage 5" -> "passage5", nicht "passage_5".
        # Konvention aus CLAUDE.md (data/steam/1141975_passage5/).
    return "".join(out).strip("_") or "item"


def meta_path(idir: Path) -> Path:
    return idir / "meta.json"


def master_path(idir: Path, lang: str) -> Path:
    return idir / f"master_{lang}.json"


def translation_path(idir: Path, lang: str) -> Path:
    return idir / "translations" / f"{lang}.json"
