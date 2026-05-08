"""Tests fuer core.storage.item_dir.

Fokus auf Lisbeth NT-548 Pass 6 (MEDIUM FUNCTIONAL): Praefix-Kollisionen
zwischen item_ids duerfen den Legacy-Fallback nicht falsch resolven.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.storage import item_dir  # noqa: E402


def _write_meta(folder: Path, item_id: str, name: str = "Item") -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "meta.json").write_text(
        json.dumps({"item_id": item_id, "name": name, "platform": "steam"}),
        encoding="utf-8",
    )


def test_exact_meta_match(tmp_path: Path) -> None:
    p = tmp_path / "steam" / "1141975_passage5"
    _write_meta(p, "1141975", "Passage 5")
    assert item_dir(tmp_path, "steam", "1141975") == p


def test_legacy_fallback_used_when_meta_missing(tmp_path: Path) -> None:
    p = tmp_path / "steam" / "777_legacy"
    p.mkdir(parents=True)
    # kein meta.json -> Legacy-Pfad
    assert item_dir(tmp_path, "steam", "777") == p


def test_legacy_fallback_used_when_meta_corrupt(tmp_path: Path) -> None:
    p = tmp_path / "steam" / "888_legacy"
    p.mkdir(parents=True)
    (p / "meta.json").write_text("{ this is not json", encoding="utf-8")
    assert item_dir(tmp_path, "steam", "888") == p


def test_prefix_collision_rejects_longer_id_folder(tmp_path: Path) -> None:
    """Pass 6: Lookup auf "1" darf NICHT den Folder fuer "1_2" treffen.

    Szenario: ein Folder mit lesbarer meta hat item_id "1_2". Daneben gibt es
    einen unlesbaren Legacy-Folder, dessen Name "1_2_orphan" mit "1_" beginnt.
    Beim Lookup auf "1" wuerde der alte startswith-Filter den orphan-Folder
    treffen — falsch, weil er logisch zu "1_2" gehoert.
    """
    long_id = tmp_path / "steam" / "1_2_main"
    _write_meta(long_id, "1_2", "Lang")
    orphan = tmp_path / "steam" / "1_2_orphan"
    orphan.mkdir()
    # kein meta.json im orphan
    with pytest.raises(FileNotFoundError):
        item_dir(tmp_path, "steam", "1")


def test_prefix_collision_short_id_still_finds_meta_match(tmp_path: Path) -> None:
    """Wenn ein readable Folder fuer "1" existiert, muss er trotz "1_2" kommen."""
    short = tmp_path / "steam" / "1_short"
    _write_meta(short, "1", "Kurz")
    long_id = tmp_path / "steam" / "1_2_main"
    _write_meta(long_id, "1_2", "Lang")
    assert item_dir(tmp_path, "steam", "1") == short
    assert item_dir(tmp_path, "steam", "1_2") == long_id


def test_multiple_legacy_candidates_refuses(tmp_path: Path) -> None:
    """Bei mehreren Legacy-Kandidaten ist die Zuordnung mehrdeutig — refuse."""
    a = tmp_path / "steam" / "42_one"
    b = tmp_path / "steam" / "42_two"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        item_dir(tmp_path, "steam", "42")


def test_creates_path_when_name_given_no_existing(tmp_path: Path) -> None:
    p = item_dir(tmp_path, "steam", "999", name="Neu")
    assert p == tmp_path / "steam" / "999_neu"
    assert not p.exists()  # legt nur den Pfad zurueck, kein mkdir


def test_no_platform_dir(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        item_dir(tmp_path, "steam", "1234")


def test_legacy_fallback_skipped_when_meta_lists_other_id(tmp_path: Path) -> None:
    """Folder mit lesbarer meta != gesuchter ID darf nicht als Fallback dienen."""
    other = tmp_path / "steam" / "5_other"
    _write_meta(other, "5", "Andere")
    with pytest.raises(FileNotFoundError):
        item_dir(tmp_path, "steam", "999")


def test_pass8_legacy_with_underscore_slug_matches_when_unique(tmp_path: Path) -> None:
    """Pass 8 (Lisbeth NT-548 14:39): Legacy-Folder mit Underscore im Slug
    (z.B. "777_my_game") wird beim Lookup auf "777" akzeptiert, solange es
    keine Hinweise auf eine kollidierende laengere id gibt.

    Pass 7 hatte das pauschal abgelehnt, was Lisbeth zu strikt fand.
    """
    p = tmp_path / "steam" / "777_my_game"
    p.mkdir(parents=True)
    # kein meta.json, einzelner Kandidat
    assert item_dir(tmp_path, "steam", "777") == p


def test_pass8_lookup_on_compound_id_still_finds_unreadable(tmp_path: Path) -> None:
    """Lookup auf compound id "1_2" mit Folder "1_2_main" (kein meta.json)
    matched — Suffix nach "1_2_" ist "main", kein "_"."""
    legacy = tmp_path / "steam" / "1_2_main"
    legacy.mkdir(parents=True)
    assert item_dir(tmp_path, "steam", "1_2") == legacy


def test_pass8_collision_with_bare_longer_id_folder_refuses(tmp_path: Path) -> None:
    """Pass 8 Filter (c): wenn neben "1_2_main" auch ein "1_2"-Folder
    (lesbar oder nicht) existiert, ist "1_2" plausibel eine eigene id und
    der Lookup auf "1" muss refuse-en."""
    main = tmp_path / "steam" / "1_2_main"
    main.mkdir(parents=True)
    bare = tmp_path / "steam" / "1_2"
    bare.mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        item_dir(tmp_path, "steam", "1")


def test_pass8_collision_with_sibling_longer_prefix_refuses(tmp_path: Path) -> None:
    """Pass 8 Filter (c): wenn "1_2_main" und "1_2_other" beide unreadable
    nebeneinander stehen, ist "1_2_" ein shared prefix und damit eine
    plausible eigene id. Lookup auf "1" muss refuse-en."""
    main = tmp_path / "steam" / "1_2_main"
    other = tmp_path / "steam" / "1_2_other"
    main.mkdir(parents=True)
    other.mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        item_dir(tmp_path, "steam", "1")
