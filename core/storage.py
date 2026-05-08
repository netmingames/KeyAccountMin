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


def item_dir(data_root: Path, platform: str, item_id: str, name: str | None = None) -> Path:
    """Pfad zum Item-Ordner: data/<platform>/<item_id>_<slug>/.

    Wenn name fehlt, wird der bestehende Ordner gesucht. Wenn keiner existiert
    und name gesetzt ist, wird ein neuer Ordner-Pfad zurueckgegeben (nicht
    angelegt).
    """
    base = data_root / platform
    base.mkdir(parents=True, exist_ok=True)
    # Existierenden Ordner per Praefix finden
    for d in base.iterdir():
        if d.is_dir() and d.name.startswith(f"{item_id}_"):
            return d
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
