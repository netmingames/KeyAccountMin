"""NT-548 Pass 9 (Lisbeth 14:59 LOW FUNCTIONAL): translate_item_lang
muss ``fields=[]`` von ``fields=None`` unterscheiden.

Vorher: ``fields=[]`` wurde wie ``None`` behandelt -> alle Master-Felder
wurden uebersetzt. Das verletzt die API-Semantik fuer Caller, die explizit
"keine Felder" filtern wollen.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import storage  # noqa: E402
from core.translator import MockTranslator, translate_item_lang  # noqa: E402


def _seed_item(tmp_path: Path) -> Path:
    idir = tmp_path / "steam" / "1141975_passage5"
    idir.mkdir(parents=True)
    storage.write_json_atomic(
        idir / "meta.json",
        {
            "platform": "steam",
            "item_id": "1141975",
            "name": "Passage 5",
            "active_languages": ["english"],
            "early_access": False,
            "schema_version": 1,
            "master_lang": "german",
        },
    )
    storage.write_json_atomic(
        idir / "master_de.json",
        {
            "schema_version": 1,
            "item_id": "1141975",
            "lang": "german",
            "fields": {
                "about": "Eine deutsche About-Beschreibung.",
                "short_description": "Kurzbeschreibung.",
            },
            "updated_at": "2026-05-08T16:00:00",
        },
    )
    return idir


def test_fields_none_translates_all(tmp_path: Path) -> None:
    idir = _seed_item(tmp_path)
    result = translate_item_lang(idir, "english", fields=None, translator=MockTranslator())
    assert set(result.fields_translated.keys()) == {"about", "short_description"}


def test_fields_empty_list_translates_none(tmp_path: Path) -> None:
    """fields=[] ist ein expliziter "leerer Filter" und unterscheidet sich
    von fields=None. Erwartet: KEINE Felder uebersetzt."""
    idir = _seed_item(tmp_path)
    result = translate_item_lang(idir, "english", fields=[], translator=MockTranslator())
    assert result.fields_translated == {}


def test_fields_subset_translates_only_listed(tmp_path: Path) -> None:
    idir = _seed_item(tmp_path)
    result = translate_item_lang(idir, "english", fields=["about"], translator=MockTranslator())
    assert set(result.fields_translated.keys()) == {"about"}
