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
       ``{item_id}_`` anfaengt, gilt er als Match — aber NUR wenn
       eindeutig. Bei Praefix-Kollision (Lisbeth NT-548 Pass 6 + Pass 7,
       MEDIUM FUNCTIONAL) wird der Fallback verworfen, sonst koennte ein
       Lookup auf item_id "1" einen Folder "1_2_xyz" treffen, der eigentlich
       zu item_id "1_2" gehoert. Pass 7-Verschaerfung: der Suffix nach
       ``{item_id}_`` darf KEINEN weiteren Unterstrich enthalten — sonst
       koennte er auch eine laengere id-Komponente repraesentieren, deren
       eigener Folder ebenfalls unreadable ist (kein Schutz mehr ueber
       readable other_meta_ids).
    """
    _validate_path_segment(platform, "platform")
    _validate_path_segment(item_id, "item_id")
    base = data_root / platform
    if not base.exists():
        if name is None:
            raise FileNotFoundError(f"Item {item_id} unter {base} nicht gefunden")
        slug = _slugify(name)
        return base / f"{item_id}_{slug}"

    # Pass 1: alle Folder klassifizieren. Direkter Match per meta.item_id
    # gewinnt sofort. Andere readable item_ids merken (fuer Ambiguity-Check).
    other_meta_ids: set[str] = set()
    unreadable: list[Path] = []
    for d in base.iterdir():
        if not d.is_dir():
            continue
        mp = d / "meta.json"
        if mp.exists():
            try:
                with open(mp, encoding="utf-8") as f:
                    meta = json.load(f)
                mid = meta.get("item_id")
                if mid == item_id:
                    return d
                if isinstance(mid, str):
                    other_meta_ids.add(mid)
                continue
            except (json.JSONDecodeError, OSError):
                pass
        unreadable.append(d)

    # Pass 2: Legacy-Fallback nur bei Eindeutigkeit. Vier Filter:
    #   a) Folder-Name muss mit "{item_id}_" anfangen.
    #   b) Folder darf nicht zu einem bekannten laengeren item_id passen
    #      ("1_2_xyz" gehoert zu "1_2" wenn dessen meta.json lesbar ist).
    #   c) Pass 7: Suffix nach "{item_id}_" darf KEINEN weiteren "_"
    #      enthalten. Sonst koennte der erste Teil des Suffix selbst eine
    #      laengere id-Komponente sein, deren Folder ebenfalls kein
    #      lesbares meta hat (= Schutz b greift nicht). Beispiel: Lookup
    #      auf "1", Folder "1_2_main" ohne meta.json, kein anderer 1_2-
    #      Folder mit meta — Pass 6 wuerde "1_2_main" akzeptieren, Pass 7
    #      verwirft es weil suffix="2_main" einen "_" enthaelt.
    #   d) Es darf nur EINEN Kandidaten geben — bei mehreren ist die
    #      Zuordnung ohne meta.json nicht entscheidbar.
    marker = f"{item_id}_"
    candidates: list[Path] = []
    for d in unreadable:
        if not d.name.startswith(marker):
            continue
        ambiguous_readable = any(
            other_id != item_id
            and len(other_id) > len(item_id)
            and d.name.startswith(f"{other_id}_")
            for other_id in other_meta_ids
        )
        if ambiguous_readable:
            continue
        suffix = d.name[len(marker):]
        if "_" in suffix:
            # Pass 7: Suffix mit Unterstrich = potenziell laengere id-
            # Komponente ohne lesbare meta.json — Zuordnung mehrdeutig.
            continue
        candidates.append(d)
    if len(candidates) == 1:
        return candidates[0]

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
