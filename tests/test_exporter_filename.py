"""NT-549 Pass 3 / NT-550: export_to_file darf bei zwei Aufrufen in derselben
Sekunde nicht ueberschreiben."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _seed_minimal_item(idir: Path) -> None:
    idir.mkdir(parents=True, exist_ok=True)
    (idir / "meta.json").write_text(
        json.dumps({
            "platform": "steam",
            "item_id": "555",
            "name": "Test",
            "active_languages": [],
            "early_access": False,
            "schema_version": 1,
            "master_lang": "german",
        }),
        encoding="utf-8",
    )
    (idir / "master_de.json").write_text(
        json.dumps({
            "schema_version": 1,
            "item_id": "555",
            "lang": "german",
            "fields": {"about": "X"},
            "updated_at": "2026-05-08T16:00:00",
        }),
        encoding="utf-8",
    )


def test_two_exports_same_microsecond_get_unique_filenames(tmp_path: Path, monkeypatch) -> None:
    """Wenn datetime.now() denselben Stempel liefert (Mock), produziert
    der Counter einen eindeutigen Pfad statt zu ueberschreiben."""
    from core import exporter

    idir = tmp_path / "steam" / "555_test"
    _seed_minimal_item(idir)

    # Mock: jedes datetime.now() gibt denselben Zeitstempel zurueck
    import datetime as real_dt

    fixed = real_dt.datetime(2026, 5, 8, 14, 55, 30, 123456)

    class _FixedDt(real_dt.datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: D401
            return fixed

    monkeypatch.setattr(exporter._dt, "datetime", _FixedDt)

    p1 = exporter.export_to_file(idir)
    p2 = exporter.export_to_file(idir)

    assert p1.exists() and p2.exists()
    assert p1 != p2
    # Erste hat Basis-Name, zweite haengt _0001 dran
    assert "_0001" in p2.name
    assert "_0001" not in p1.name


def test_microsecond_in_filename(tmp_path: Path) -> None:
    """Standardfall: Filename enthaelt jetzt %f (Microsekunden) — sonst koennte
    der gleiche Name in derselben Sekunde wiederkommen."""
    from core import exporter

    idir = tmp_path / "steam" / "555_test"
    _seed_minimal_item(idir)

    p = exporter.export_to_file(idir)
    # Format: storepage_555_<YYYYMMDD>_<HHMMSS>_<microseconds>.json
    parts = p.stem.split("_")
    # storepage, 555, YYYYMMDD, HHMMSS, microseconds
    assert len(parts) >= 5
    assert parts[-1].isdigit() and len(parts[-1]) == 6  # microseconds = 6 digits


def test_corrupt_translation_skipped_not_500(tmp_path: Path) -> None:
    """NT-550 Pass 2 (Lisbeth 15:14 MEDIUM FUNCTIONAL): eine kaputte
    translations/<lang>.json darf nicht den ganzen Export killen.
    Stattdessen wird die Sprache uebersprungen + in skipped_translations
    gemeldet, andere Sprachen funktionieren normal."""
    from core import exporter

    idir = tmp_path / "steam" / "555_test"
    _seed_minimal_item(idir)

    # Eine valide englische Translation
    (idir / "translations").mkdir(parents=True, exist_ok=True)
    (idir / "translations" / "english.json").write_text(
        json.dumps({
            "schema_version": 1,
            "item_id": "555",
            "lang": "english",
            "fields": {"about": {"value": "EN about", "stale": False, "manually_edited": False}},
            "updated_at": "2026-05-08T16:00:00",
        }),
        encoding="utf-8",
    )
    # Eine zerschossene franzoesische Translation
    (idir / "translations" / "french.json").write_text("{ this is not json", encoding="utf-8")

    data = exporter.export_steam_loka(idir)
    assert "french" in data["skipped_translations"]
    assert "english" not in data["skipped_translations"]
    # English-Block ist befuellt, French-Block wird leer ausgegeben (kein 500)
    assert any(v for v in data["languages"]["english"].values())


def test_export_skips_translation_with_invalid_utf8(tmp_path: Path) -> None:
    """NT-549 Pass 6 (Lisbeth 15:54 MEDIUM FUNCTIONAL):
    Eine Translation-Datei mit kaputten UTF-8-Bytes darf den Export nicht
    killen. Sprache wird in skipped_translations gemeldet."""
    from core import exporter

    idir = tmp_path / "steam" / "555_test"
    _seed_minimal_item(idir)

    (idir / "translations").mkdir(parents=True, exist_ok=True)
    # Valide english
    (idir / "translations" / "english.json").write_text(
        json.dumps({
            "schema_version": 1,
            "item_id": "555",
            "lang": "english",
            "fields": {"about": {"value": "EN", "stale": False, "manually_edited": False}},
            "updated_at": "2026-05-08T16:00:00",
        }),
        encoding="utf-8",
    )
    # Kaputte UTF-8-Bytes in french
    (idir / "translations" / "french.json").write_bytes(b'{"\xff\xfe\xff": "X"}')

    data = exporter.export_steam_loka(idir)
    assert "french" in data["skipped_translations"]
    assert "english" not in data["skipped_translations"]


def test_empty_master_field_exported_as_empty_string(tmp_path: Path) -> None:
    """NT-550 Pass 3 (Lisbeth 15:50 MEDIUM FUNCTIONAL): Felder die im
    Master als leerer String existieren werden trotzdem exportiert (""),
    nicht weggelassen. Das ist explizite Akzeptanz im Ticket."""
    from core import exporter

    idir = tmp_path / "steam" / "555_test"
    idir.mkdir(parents=True, exist_ok=True)
    (idir / "meta.json").write_text(
        json.dumps({
            "platform": "steam",
            "item_id": "555",
            "name": "Test",
            "active_languages": [],
            "early_access": False,
            "schema_version": 1,
            "master_lang": "german",
        }),
        encoding="utf-8",
    )
    # Master mit "about" gefuellt UND "short_description" leer + sysreqs leer.
    # Vor dem Fix wurden die leeren Felder weggelassen.
    (idir / "master_de.json").write_text(
        json.dumps({
            "schema_version": 1,
            "item_id": "555",
            "lang": "german",
            "fields": {
                "about": "Filled",
                "short_description": "",
                "sysreqs_min_osversion": "",
                "sysreqs_min_processor": "",
            },
            "updated_at": "2026-05-08T16:00:00",
        }),
        encoding="utf-8",
    )

    data = exporter.export_steam_loka(idir)
    de_block = data["languages"]["german"]
    # Alle 4 Master-Standard-Felder muessen im Output-Block sein
    assert "app[content][about]" in de_block
    assert "app[content][short_description]" in de_block
    assert "app[content][sysreqs][windows][min][osversion]" in de_block
    assert "app[content][sysreqs][windows][min][processor]" in de_block
    assert de_block["app[content][about]"] == "Filled"
    assert de_block["app[content][short_description]"] == ""
    assert de_block["app[content][sysreqs][windows][min][osversion]"] == ""

    # EA-Felder bleiben weg (Master kennt sie hier nicht), dito nicht-Master-Felder
    assert "app[content][sysreqs][windows][min][memory]" not in de_block


def test_export_to_file_strips_diagnose_fields(tmp_path: Path) -> None:
    """NT-550 Pass 2: skipped_translations ist ein Diagnose-Feld in der
    API-Response, darf aber nicht ins Steam-Upload-JSON. Steam erwartet
    nur itemid + languages."""
    from core import exporter

    idir = tmp_path / "steam" / "555_test"
    _seed_minimal_item(idir)

    p = exporter.export_to_file(idir)
    file_data = json.loads(p.read_text(encoding="utf-8"))
    assert set(file_data.keys()) == {"itemid", "languages"}


def test_export_to_file_accepts_precomputed_data(tmp_path: Path) -> None:
    """NT-549 Pass 7 (Lisbeth 16:12 LOW FUNCTIONAL): export_to_file darf ein
    vorberechnetes data-Dict uebernehmen, damit der Aufrufer dasselbe Dict
    (inkl. skipped_translations) fuer Datei UND Summary nutzen kann."""
    from core import exporter

    idir = tmp_path / "steam" / "555_test"
    _seed_minimal_item(idir)

    # Vorberechnung mit kuenstlich gefuelltem skipped_translations
    data = exporter.export_steam_loka(idir)
    data["skipped_translations"] = ["french", "italian"]

    p = exporter.export_to_file(idir, data=data)
    file_data = json.loads(p.read_text(encoding="utf-8"))
    # Datei selbst bleibt clean (Steam-kompatibel)
    assert set(file_data.keys()) == {"itemid", "languages"}
    # ...aber die Summary aus dem uebergebenen Dict zeigt skipped_translations
    summary = exporter.export_summary(data)
    assert summary["skipped_translations"] == ["french", "italian"]


def test_api_export_summary_reports_skipped_translations(tmp_path: Path, monkeypatch) -> None:
    """NT-549 Pass 7 (Lisbeth 16:12 LOW FUNCTIONAL): /export muss
    skipped_translations in der API-Response melden — frueher hat app.py
    die geschriebene Datei zurueckgelesen, wo skipped_translations bereits
    gestrippt war, und damit eine Inkonsistenz zu /export-preview erzeugt."""
    from fastapi.testclient import TestClient

    import app as app_mod
    monkeypatch.setattr(app_mod, "DATA_ROOT", tmp_path)

    idir = tmp_path / "steam" / "555_test"
    _seed_minimal_item(idir)
    (idir / "translations").mkdir(parents=True, exist_ok=True)
    # eine valide english + eine zerschossene french
    (idir / "translations" / "english.json").write_text(
        json.dumps({
            "schema_version": 1,
            "item_id": "555",
            "lang": "english",
            "fields": {"about": {"value": "EN", "stale": False, "manually_edited": False}},
            "updated_at": "2026-05-08T16:00:00",
        }),
        encoding="utf-8",
    )
    (idir / "translations" / "french.json").write_text("{ broken json", encoding="utf-8")

    client = TestClient(app_mod.app)

    # /export-preview meldet skipped_translations korrekt
    r1 = client.get("/api/items/steam/555/export-preview")
    assert r1.status_code == 200, r1.text
    assert "french" in r1.json()["summary"]["skipped_translations"]

    # /export muss DASSELBE melden (frueher: leer)
    r2 = client.post("/api/items/steam/555/export")
    assert r2.status_code == 200, r2.text
    assert "french" in r2.json()["summary"]["skipped_translations"]
