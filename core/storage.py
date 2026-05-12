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
    2. Fallback (Lisbeth NT-548 13:02 / Pass 8): wenn der Folder kein
       lesbares ``meta.json`` hat (legacy / partially written) und sein
       Name mit ``{item_id}_`` anfaengt, gilt er als Match — aber NUR wenn
       eindeutig. Drei Filter:
         a) Folder-Name muss mit "{item_id}_" anfangen.
         b) Folder darf nicht zu einem laengeren bekannten item_id passen
            ("1_2_xyz" gehoert zu "1_2" wenn dessen meta.json lesbar ist).
         c) Folder darf nicht zu einer laengeren id passen, fuer die ein
            anderer Folder im base existiert (lesbar oder nicht). Pass 8:
            wenn neben "1_2_main" (unreadable) auch "1_2_other" oder ein
            "1_2"-Folder steht, ist "1_2" plausibel eine eigene id.
         d) Es darf nur EINEN Kandidaten geben.

       Pass 7-Verschaerfung (kompletter suffix-underscore-Block) wurde von
       Lisbeth NT-548 14:39 zurueckgewiesen, weil dabei valide Folder wie
       "777_my_game" (slug mit Underscore, kein Konflikt) abgelehnt wurden.
       Pass 8 ersetzt das durch die strukturelle Pruefung in (c): nur dann
       refuse, wenn es konkrete Hinweise auf eine collidierende laengere id
       im Verzeichnis gibt.
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

    # Pass 2: Legacy-Fallback nur bei Eindeutigkeit. Filter siehe Docstring.
    # Pass 8 (Lisbeth NT-548 14:39 + Pass 7-Reverter): Filter (c) ist jetzt
    # struktur-basiert — wir refuse-en nur dann, wenn es im Verzeichnis
    # konkrete Hinweise auf eine collidierende laengere id gibt
    # (z.B. neben "1_2_main" auch "1_2_other" oder ein bare "1_2"-Folder).
    # "777_my_game" allein bleibt damit ein valider Match.
    marker = f"{item_id}_"
    base_dirs = [d for d in base.iterdir() if d.is_dir()]
    candidates: list[Path] = []
    for d in unreadable:
        if not d.name.startswith(marker):
            continue
        # Filter (b): kollidiert mit lesbarer laengerer id?
        ambiguous_readable = any(
            other_id != item_id
            and len(other_id) > len(item_id)
            and d.name.startswith(f"{other_id}_")
            for other_id in other_meta_ids
        )
        if ambiguous_readable:
            continue
        # Filter (c1): koennte d selbst eine laengere item_id sein? Indikator:
        # ein anderer Folder im base hat d.name als Praefix mit "_" — z.B.
        # d=`1_2`, sibling=`1_2_main`. Dann ist `1_2` plausibel die echte id
        # und der Lookup auf "1" sollte refuse-en.
        d_prefix = f"{d.name}_"
        has_descendant = any(
            d2.name != d.name and d2.name.startswith(d_prefix)
            for d2 in base_dirs
        )
        if has_descendant:
            continue
        # Filter (c2): koennte d "Subfolder" einer plausiblen laengeren id
        # sein? Wenn der Suffix nach "{item_id}_" einen weiteren "_" hat,
        # ist die erste Suffix-Komponente eine potentielle id-Erweiterung.
        # Wir checken, ob es ANDERE Folder im base gibt, die zu dieser
        # laengeren id zeigen — als bare directory ("1_2") oder als
        # zweites prefix-shared directory ("1_2_other").
        suffix = d.name[len(marker):]
        if "_" in suffix:
            x_component = suffix.split("_", 1)[0]
            longer_id = f"{item_id}_{x_component}"
            longer_marker = f"{longer_id}_"
            collision = any(
                d2.name != d.name
                and (d2.name == longer_id or d2.name.startswith(longer_marker))
                for d2 in base_dirs
            )
            if collision:
                continue
            # Filter (c3) NT-551 Pass 18 (Lisbeth 11:12 LOW FUNCTIONAL):
            # Lisbeth-Sicht hat sich nach mehrfachem Pendeln (Pass 9-17)
            # auf der Pass-13-Logik stabilisiert: Refuse-en wenn x_component
            # numerisch ist UND nicht (len(x) >= 4 AND len(id) >= 2).
            # Pass 16's milderer Kompromiss (nur 1-stellige ids refuse)
            # war zu permissiv -- Lisbeth verlangt jetzt auch Refuse fuer
            # "12_3_main"/"12" (2-stellige id, 1-stelliger numerischer
            # suffix). Pass 18 nimmt die Pass-13-Bedingung zurueck:
            #
            #   x_component numerisch:
            #     - len(x) >= 4 UND len(id) >= 2 -> accept
            #     - sonst -> refuse
            #
            # Faelle:
            #   "1_2_main"        / "1"   -> refuse (id=1<2)
            #   "1_2024_update"   / "1"   -> refuse (id=1<2)
            #   "12_3_main"       / "12"  -> refuse (x=1<4)   [Pass 18 trigger]
            #   "12_1_main"       / "12"  -> refuse (x=1<4)   [Pass 18 trigger]
            #   "12_123_main"     / "12"  -> refuse (x=3<4)
            #   "1234567_12_main" / "1234567" -> refuse (x=2<4)
            #   "12_2024_update"  / "12"  -> accept (id=2, x=4)
            #   "42_2024_dlc"     / "42"  -> accept (id=2, x=4)
            #   "777_2024_update" / "777" -> accept (id=3, x=4)
            #   "1141975_2024_dlc"/"1141975" -> accept
            #   "42_1st_pass"     / "42"  -> accept (x nicht numerisch)
            if x_component.isdigit() and not (
                len(x_component) >= 4
                and len(item_id) >= 2
            ):
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
