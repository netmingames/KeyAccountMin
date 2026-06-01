"""NT-565: Steam-Feld sysreqs_min/rec_soundcard in Schema/Mapping/Labels.

Sichert den verlustfreien Import->Export-Roundtrip ab — vorher hat der Importer
die Soundkarten-Zeile als "Unbekannten Steam-Key" verworfen.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import import_storepage  # noqa: E402
from core import exporter, labels, schema, steam_mapping, storage  # noqa: E402


def test_soundcard_in_schema():
    assert "sysreqs_min_soundcard" in schema.STEAM_FIELDS_STANDARD
    assert "sysreqs_rec_soundcard" in schema.STEAM_FIELDS_STANDARD


def test_soundcard_mapping_both_directions():
    assert steam_mapping.STEAM_TO_FIELD["app[content][sysreqs][windows][min][soundcard]"] == "sysreqs_min_soundcard"
    assert steam_mapping.FIELD_TO_STEAM["sysreqs_min_soundcard"] == "app[content][sysreqs][windows][min][soundcard]"
    assert steam_mapping.FIELD_TO_STEAM["sysreqs_rec_soundcard"] == "app[content][sysreqs][windows][rec][soundcard]"


def test_soundcard_labels():
    assert "Soundkarte" in labels.label("sysreqs_min_soundcard")
    assert "Soundkarte" in labels.label("sysreqs_rec_soundcard")


def test_soundcard_import_export_roundtrip(tmp_path: Path):
    payload = {"itemid": "999", "languages": {"german": {
        "app[content][short_description]": "kurz",
        "app[content][sysreqs][windows][min][soundcard]": "DirectX 9 kompatibel",
        "app[content][sysreqs][windows][rec][soundcard]": "Surround 5.1",
    }}}
    jpath = tmp_path / "sp.json"
    jpath.write_text(json.dumps(payload), encoding="utf-8")
    data_root = tmp_path / "data"

    rc = import_storepage.cmd_import(jpath, name="Test", force=False, data_root=data_root)
    assert rc == 0

    idir = storage.item_dir(data_root, "steam", "999")
    master = schema.MasterDocument(**storage.read_json(storage.master_path(idir, "de")))
    assert master.fields["sysreqs_min_soundcard"] == "DirectX 9 kompatibel"
    assert master.fields["sysreqs_rec_soundcard"] == "Surround 5.1"

    # Export-Roundtrip: Soundkarten-Keys wieder im Steam-JSON (kein Verlust)
    data = exporter.export_steam_loka(idir)
    de = data["languages"]["german"]
    assert de["app[content][sysreqs][windows][min][soundcard]"] == "DirectX 9 kompatibel"
    assert de["app[content][sysreqs][windows][rec][soundcard]"] == "Surround 5.1"
