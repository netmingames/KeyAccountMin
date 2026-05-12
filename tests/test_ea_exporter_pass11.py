"""NT-551 Pass 11 (Lisbeth 09:40 LOW FUNCTIONAL).

Finding: core/ea_exporter.py:85-88 — die Korruptions-Warnung war hartkodiert auf
``translations/{lang}.json``. Fuer eine korrupte Master-Datei zeigte das auf die
falsche Quelldatei (Master liegt unter ``master_<iso>.json``). Operatoren bekamen
einen Pfad, an dem der Fehler gar nicht steht.

Fix: render_ea_text und der ZIP-README zeigen jetzt pro Sprache den tatsaechlichen
Quelldateinamen — ``master_<iso>.json`` fuer den Master, ``translations/<lang>.json``
fuer alle anderen Sprachen.
"""

from __future__ import annotations

import io
import json as _json
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture
def app_with_data(tmp_path: Path, monkeypatch):
    import app as app_mod  # noqa: WPS433
    monkeypatch.setattr(app_mod, "DATA_ROOT", tmp_path)
    return app_mod


def _write_meta(idir: Path, *, active_languages, early_access=True, master_lang="german") -> None:
    (idir / "meta.json").write_text(
        _json.dumps({
            "platform": "steam",
            "item_id": "1141975",
            "name": "Passage 5",
            "active_languages": active_languages,
            "early_access": early_access,
            "schema_version": 1,
            "master_lang": master_lang,
        }),
        encoding="utf-8",
    )


def _write_master_valid(idir: Path, lang_iso_short: str = "de") -> None:
    (idir / f"master_{lang_iso_short}.json").write_text(
        _json.dumps({
            "schema_version": 1,
            "item_id": "1141975",
            "lang": "german" if lang_iso_short == "de" else lang_iso_short,
            "fields": {"ea_why": "Weil Feedback wichtig ist."},
            "updated_at": "2026-05-12T09:30:00",
        }),
        encoding="utf-8",
    )


def test_master_corrupt_warning_points_to_master_file(
    app_with_data, tmp_path: Path,
) -> None:
    """Master-Datei kaputt -> Warnung nennt ``master_de.json``, nicht ``translations/german.json``."""
    from fastapi.testclient import TestClient

    idir = tmp_path / "steam" / "1141975_passage5"
    idir.mkdir(parents=True)
    _write_meta(idir, active_languages=["german"])
    (idir / "master_de.json").write_text("{ kaputt", encoding="utf-8")

    client = TestClient(app_with_data.app)
    resp = client.get("/api/items/steam/1141975/ea-export/german.txt")
    assert resp.status_code == 200
    text = resp.text
    assert "WARNUNG" in text
    assert "master_de.json" in text
    assert "translations/german.json" not in text


def test_translation_corrupt_warning_still_points_to_translation_file(
    app_with_data, tmp_path: Path,
) -> None:
    """Sanity: bei korrupter translations/<lang>.json zeigt die Warnung weiterhin
    auf translations/<lang>.json (nicht versehentlich auf den Master)."""
    from fastapi.testclient import TestClient

    idir = tmp_path / "steam" / "1141975_passage5"
    idir.mkdir(parents=True)
    _write_meta(idir, active_languages=["german", "english"])
    _write_master_valid(idir)
    (idir / "translations").mkdir(parents=True, exist_ok=True)
    (idir / "translations" / "english.json").write_text("{ kaputt", encoding="utf-8")

    client = TestClient(app_with_data.app)
    resp = client.get("/api/items/steam/1141975/ea-export/english.txt")
    assert resp.status_code == 200
    text = resp.text
    assert "WARNUNG" in text
    assert "translations/english.json" in text
    assert "master_" not in text


def test_zip_readme_lists_master_file_for_corrupt_master(
    app_with_data, tmp_path: Path,
) -> None:
    """README im ZIP nennt den tatsaechlichen Quelldateinamen pro skipped Sprache."""
    from fastapi.testclient import TestClient

    idir = tmp_path / "steam" / "1141975_passage5"
    idir.mkdir(parents=True)
    _write_meta(idir, active_languages=["german", "english"])
    (idir / "master_de.json").write_text("{ kaputt", encoding="utf-8")
    (idir / "translations").mkdir(parents=True, exist_ok=True)
    (idir / "translations" / "english.json").write_text("{ kaputt", encoding="utf-8")

    client = TestClient(app_with_data.app)
    resp = client.get("/api/items/steam/1141975/ea-export.zip")
    assert resp.status_code == 200
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        readme = zf.read("README.md").decode("utf-8")
    assert "WARNUNG" in readme
    assert "master_de.json" in readme
    assert "translations/english.json" in readme


def test_non_german_master_uses_correct_iso_short(
    app_with_data, tmp_path: Path,
) -> None:
    """Wenn der Master z.B. english ist, muss die Warnung master_en.json nennen."""
    from fastapi.testclient import TestClient

    idir = tmp_path / "steam" / "1141975_passage5"
    idir.mkdir(parents=True)
    _write_meta(idir, active_languages=["english"], master_lang="english")
    (idir / "master_en.json").write_text("{ kaputt", encoding="utf-8")

    client = TestClient(app_with_data.app)
    resp = client.get("/api/items/steam/1141975/ea-export/english.txt")
    assert resp.status_code == 200
    text = resp.text
    assert "WARNUNG" in text
    assert "master_en.json" in text
    assert "translations/english.json" not in text
