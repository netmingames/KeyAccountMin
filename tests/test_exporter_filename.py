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
