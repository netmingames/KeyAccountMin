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


def test_pass16_lone_compound_slug_with_numeric_subid_refuses(tmp_path: Path) -> None:
    """NT-558 Pass 16 (Lisbeth 10:57 LOW FUNCTIONAL): Lisbeth pendelte
    Pass 14 (entfernen) zurueck. Pass 16 macht Minimal-Kompromiss: nur
    1-stellige item_ids mit numerischem x_component refuse-en. "1_2_main"
    /"1" trifft das (id=1, x=numeric), also refuse."""
    main = tmp_path / "steam" / "1_2_main"
    main.mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        item_dir(tmp_path, "steam", "1")


def test_pass10_lone_folder_with_long_numeric_segment_matches(tmp_path: Path) -> None:
    """Pass 10 (Lisbeth NT-548 15:33): "777_2024_update" mit Lookup "777"
    soll matchen, weil "2024" (4-stellig) wie ein Jahres-/Slug-Anteil
    aussieht und nicht wie eine kurze Sub-id-Erweiterung.

    Heuristik (c3): nur first components mit <= 3 Ziffern gelten als
    Sub-id-Indikator. Alles laengere wird als Slug akzeptiert.
    """
    p = tmp_path / "steam" / "777_2024_update"
    p.mkdir(parents=True)
    assert item_dir(tmp_path, "steam", "777") == p


def test_pass10_lone_folder_with_alphanumeric_segment_matches(tmp_path: Path) -> None:
    """Pass 10: "42_1st_pass" mit Lookup "42" matched — first component
    "1st" ist nicht rein numerisch (isdigit()=False), also kein Sub-id."""
    p = tmp_path / "steam" / "42_1st_pass"
    p.mkdir(parents=True)
    assert item_dir(tmp_path, "steam", "42") == p


def test_pass16_short_item_id_with_year_like_segment_refuses(tmp_path: Path) -> None:
    """NT-558 Pass 16: einstellige item_ids mit numerischem Suffix
    werden weiterhin refuse-t (Pass 14 wurde teilweise widerrufen)."""
    p = tmp_path / "steam" / "1_2024_update"
    p.mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        item_dir(tmp_path, "steam", "1")


def test_pass16_short_item_id_with_long_numeric_segment_refuses(tmp_path: Path) -> None:
    """NT-558 Pass 16: "1_1234_main" mit Lookup "1" -> refuse (1-stellige
    id mit numerischem suffix)."""
    p = tmp_path / "steam" / "1_1234_main"
    p.mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        item_dir(tmp_path, "steam", "1")


def test_pass11_pass10_year_like_match_still_works(tmp_path: Path) -> None:
    """Pass 11 darf den Pass-10-Fall nicht regressieren: "777_2024_update"
    mit Lookup "777" muss weiterhin matchen."""
    p = tmp_path / "steam" / "777_2024_update"
    p.mkdir(parents=True)
    assert item_dir(tmp_path, "steam", "777") == p


def test_pass13_two_digit_id_with_year_like_segment_accepts(tmp_path: Path) -> None:
    """NT-551 Pass 13 (Lisbeth 10:27 MEDIUM FUNCTIONAL): "12_2024_update"
    mit Lookup "12" muss jetzt ACCEPT-en. Die Pass-12-Bedingung
    `len(item_id) >= len(x_component) - 1` war zu restriktiv — Filter (c1)
    und (c2) decken die compound-id-Risiken bereits ab; bei kollisionsfreien
    Folders reicht len(x_component) >= 4 und len(item_id) >= 2 als Slug-
    Erkennung. Vorher (Pass 12) war's refuse, das war eine zu starke
    Verschaerfung."""
    p = tmp_path / "steam" / "12_2024_update"
    p.mkdir(parents=True)
    assert item_dir(tmp_path, "steam", "12") == p


def test_pass18_long_id_with_short_numeric_segment_refuses(tmp_path: Path) -> None:
    """NT-551 Pass 18 (Lisbeth 11:12 LOW FUNCTIONAL): "1234567_12_main"
    mit Lookup "1234567" -> refuse. 2-stelliger numerischer Suffix ist
    nicht substantiell genug fuer Slug-Erkennung; sieht wie Sub-Id aus.
    Pass 14 hatte hier accept gefordert, Pass 18 widerruft."""
    p = tmp_path / "steam" / "1234567_12_main"
    p.mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        item_dir(tmp_path, "steam", "1234567")


def test_pass12_appid_with_year_match_works(tmp_path: Path) -> None:
    """Pass 12: typische Steam-AppID mit Jahres-Slug muss weiter matchen.
    item_id "1141975" (7-stellig) mit "1141975_2024_dlc" -> id >> x, accept."""
    p = tmp_path / "steam" / "1141975_2024_dlc"
    p.mkdir(parents=True)
    assert item_dir(tmp_path, "steam", "1141975") == p


def test_pass18_three_digit_numeric_suffix_refuses(tmp_path: Path) -> None:
    """NT-551 Pass 18: "12_123_main" mit Lookup "12" -> refuse. 3-stelliger
    numerischer Suffix wird als Sub-Id-Indiz gewertet (analog Pass 11).
    Pass 14 hatte das accept-en wollen, Pass 18 widerruft."""
    p = tmp_path / "steam" / "12_123_main"
    p.mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        item_dir(tmp_path, "steam", "12")


def test_pass11_four_digit_year_suffix_still_works(tmp_path: Path) -> None:
    """NT-550 Pass 11: 4-stelliger numerischer Suffix (Jahreszahl-aehnlich)
    bleibt akzeptiert, wenn item_id substantiell ist. "777_2024_update"
    mit Lookup "777" muss matchen (Regress-Schutz fuer Pass 10/12)."""
    p = tmp_path / "steam" / "777_2024_update"
    p.mkdir(parents=True)
    assert item_dir(tmp_path, "steam", "777") == p


def test_pass13_42_2024_dlc_accepts(tmp_path: Path) -> None:
    """NT-551 Pass 13 (Lisbeth 10:27 Beispiel): "42_2024_dlc" mit Lookup
    "42" muss matchen. Diese 2-stellige item_id + 4-stelliger Jahres-Suffix
    Kombination wurde nach Pass 12 faelschlich refused."""
    p = tmp_path / "steam" / "42_2024_dlc"
    p.mkdir(parents=True)
    assert item_dir(tmp_path, "steam", "42") == p


def test_pass16_single_digit_id_with_year_suffix_refuses(tmp_path: Path) -> None:
    """NT-558 Pass 16: einstellige item_ids mit numerischem suffix bleiben
    refuse — Pass 14 hatte sie accept-en wollen, Pass 16 widerruft."""
    p = tmp_path / "steam" / "1_2024_update"
    p.mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        item_dir(tmp_path, "steam", "1")


def test_pass18_two_digit_id_with_one_digit_numeric_suffix_refuses(tmp_path: Path) -> None:
    """NT-551 Pass 18 (Lisbeth 11:12 konkretes Beispiel): "12_3_main" mit
    Lookup "12" -> refuse. 1-stelliger numerischer Suffix sieht wie
    Sub-Id-Indiz aus, nicht wie Jahres-Slug. Pass 14/16 hatte das
    akzeptiert, Pass 18 widerruft."""
    p = tmp_path / "steam" / "12_3_main"
    p.mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        item_dir(tmp_path, "steam", "12")
