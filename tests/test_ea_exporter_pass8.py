"""NT-549 Pass 8 (Lisbeth 16:33 MEDIUM FUNCTIONAL).

Zwei Findings:
1) app.py:569-590 — EA-Endpoints (ea-status / ea-export/<lang>.txt /
   ea-export.zip) muessen die Meta-Validierung via _resolve_idir_with_meta
   nutzen. Korrupte oder fehlende meta.json darf nicht als 500 bubbeln.
2) core/ea_exporter.py:30-121 — eine kaputte translations/<lang>.json darf
   nicht den ganzen EA-Flow killen. Per-Sprache Error-Isolation wie der
   Haupt-Exporter (skipped_translations + leere Werte).
"""

from __future__ import annotations

import json as _json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture
def app_with_data(tmp_path: Path, monkeypatch):
    import app as app_mod  # noqa: WPS433
    monkeypatch.setattr(app_mod, "DATA_ROOT", tmp_path)
    return app_mod


def _write_meta(idir: Path, *, active_languages, early_access=True) -> None:
    (idir / "meta.json").write_text(
        _json.dumps({
            "platform": "steam",
            "item_id": "1141975",
            "name": "Passage 5",
            "active_languages": active_languages,
            "early_access": early_access,
            "schema_version": 1,
            "master_lang": "german",
        }),
        encoding="utf-8",
    )


def _write_master(idir: Path) -> None:
    (idir / "master_de.json").write_text(
        _json.dumps({
            "schema_version": 1,
            "item_id": "1141975",
            "lang": "german",
            "fields": {
                "ea_why": "Wir wollen Feedback.",
            },
            "updated_at": "2026-05-08T16:00:00",
        }),
        encoding="utf-8",
    )


# --- Finding 1: meta-Validierung in EA-Endpoints --------------------------


def test_ea_status_returns_404_on_missing_meta(app_with_data, tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    legacy = tmp_path / "steam" / "777_legacy"
    legacy.mkdir(parents=True)
    # kein meta.json

    client = TestClient(app_with_data.app)
    resp = client.get("/api/items/steam/777/ea-status")
    assert resp.status_code == 404


def test_ea_status_returns_404_on_corrupt_meta(app_with_data, tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    legacy = tmp_path / "steam" / "888_legacy"
    legacy.mkdir(parents=True)
    # Pass 1 sucht nach mid="888" Match -> "888_legacy" wird matchen
    (legacy / "meta.json").write_text("{ not json", encoding="utf-8")

    client = TestClient(app_with_data.app)
    resp = client.get("/api/items/steam/888/ea-status")
    assert resp.status_code == 404


def test_ea_export_text_returns_404_on_missing_meta(app_with_data, tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    legacy = tmp_path / "steam" / "999_legacy"
    legacy.mkdir(parents=True)

    client = TestClient(app_with_data.app)
    resp = client.get("/api/items/steam/999/ea-export/english.txt")
    assert resp.status_code == 404


def test_ea_export_zip_returns_404_on_missing_meta(app_with_data, tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    legacy = tmp_path / "steam" / "999_legacy"
    legacy.mkdir(parents=True)

    client = TestClient(app_with_data.app)
    resp = client.get("/api/items/steam/999/ea-export.zip")
    assert resp.status_code == 404


def test_ea_status_returns_422_on_schema_invalid_meta(app_with_data, tmp_path: Path) -> None:
    """meta.json JSON-decodierbar aber Pydantic-invalid -> 422."""
    from fastapi.testclient import TestClient

    idir = tmp_path / "steam" / "555_legacy"
    idir.mkdir(parents=True)
    (idir / "meta.json").write_text(
        # item_id passt fuer Pass 1, aber Pflichtfelder fehlen
        _json.dumps({"item_id": "555", "platform": "steam"}),
        encoding="utf-8",
    )

    client = TestClient(app_with_data.app)
    resp = client.get("/api/items/steam/555/ea-status")
    assert resp.status_code == 422


# --- Finding 2: per-lang JSON/UTF-8/IO-Isolation --------------------------


def test_ea_status_marks_corrupt_translation(app_with_data, tmp_path: Path) -> None:
    """Eine kaputte translations/<lang>.json setzt ``corrupt=True`` und
    legt die Sprache mit 0 gefuellten Feldern aus, statt 500 zu werfen."""
    from fastapi.testclient import TestClient

    idir = tmp_path / "steam" / "1141975_passage5"
    idir.mkdir(parents=True)
    _write_meta(idir, active_languages=["english", "french"])
    _write_master(idir)
    (idir / "translations").mkdir(parents=True, exist_ok=True)
    # english: valide
    (idir / "translations" / "english.json").write_text(
        _json.dumps({
            "schema_version": 1,
            "item_id": "1141975",
            "lang": "english",
            "fields": {"ea_why": {"value": "We want feedback.", "stale": False, "manually_edited": False}},
            "updated_at": "2026-05-08T16:00:00",
        }),
        encoding="utf-8",
    )
    # french: kaputt
    (idir / "translations" / "french.json").write_text("{ broken", encoding="utf-8")

    client = TestClient(app_with_data.app)
    resp = client.get("/api/items/steam/1141975/ea-status")
    assert resp.status_code == 200
    body = resp.json()
    langs = {l["code"]: l for l in body["languages"]}
    assert "english" in langs
    assert "french" in langs
    assert langs["english"]["corrupt"] is False
    assert langs["english"]["filled"] >= 1
    assert langs["french"]["corrupt"] is True
    assert langs["french"]["filled"] == 0


def test_ea_status_handles_invalid_utf8_translation(app_with_data, tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    idir = tmp_path / "steam" / "1141975_passage5"
    idir.mkdir(parents=True)
    _write_meta(idir, active_languages=["french"])
    _write_master(idir)
    (idir / "translations").mkdir(parents=True, exist_ok=True)
    (idir / "translations" / "french.json").write_bytes(b'{"\xff\xfe": "BROKEN"}')

    client = TestClient(app_with_data.app)
    resp = client.get("/api/items/steam/1141975/ea-status")
    assert resp.status_code == 200
    body = resp.json()
    fr = next(l for l in body["languages"] if l["code"] == "french")
    assert fr["corrupt"] is True
    assert fr["filled"] == 0


def test_ea_export_text_corrupt_lang_returns_warning_block(app_with_data, tmp_path: Path) -> None:
    """Plain-Text-Export einer kaputten Sprache liefert leeren Block + Warnung,
    statt 500."""
    from fastapi.testclient import TestClient

    idir = tmp_path / "steam" / "1141975_passage5"
    idir.mkdir(parents=True)
    _write_meta(idir, active_languages=["french"])
    _write_master(idir)
    (idir / "translations").mkdir(parents=True, exist_ok=True)
    (idir / "translations" / "french.json").write_text("{ broken", encoding="utf-8")

    client = TestClient(app_with_data.app)
    resp = client.get("/api/items/steam/1141975/ea-export/french.txt")
    assert resp.status_code == 200
    text = resp.text
    assert "WARNUNG" in text
    assert "translations/french.json" in text
    # Q&A-Bloecke alle mit "noch nicht ausgefuellt"
    assert "noch nicht ausgefuellt" in text


def test_ea_export_zip_skips_corrupt_translation(app_with_data, tmp_path: Path) -> None:
    """ZIP-Export bei einer kaputten Sprache: keine 500, README listet Skip,
    .txt der kaputten Sprache enthaelt Warnung."""
    import io
    import zipfile

    from fastapi.testclient import TestClient

    idir = tmp_path / "steam" / "1141975_passage5"
    idir.mkdir(parents=True)
    _write_meta(idir, active_languages=["english", "french"])
    _write_master(idir)
    (idir / "translations").mkdir(parents=True, exist_ok=True)
    (idir / "translations" / "english.json").write_text(
        _json.dumps({
            "schema_version": 1,
            "item_id": "1141975",
            "lang": "english",
            "fields": {"ea_why": {"value": "We want feedback.", "stale": False, "manually_edited": False}},
            "updated_at": "2026-05-08T16:00:00",
        }),
        encoding="utf-8",
    )
    (idir / "translations" / "french.json").write_text("{ broken", encoding="utf-8")

    client = TestClient(app_with_data.app)
    resp = client.get("/api/items/steam/1141975/ea-export.zip")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        names = zf.namelist()
        assert "README.md" in names
        readme = zf.read("README.md").decode("utf-8")
        assert "french" in readme
        assert "WARNUNG" in readme or "Warnung" in readme or "unlesbar" in readme
        # English .txt soll OK sein (echte Antwort), French .txt mit Warnung
        en_name = next(n for n in names if n.endswith("_en.txt") or "english" in n.lower())
        fr_name = next(n for n in names if n.endswith("_fr.txt") or "french" in n.lower())
        en_text = zf.read(en_name).decode("utf-8")
        fr_text = zf.read(fr_name).decode("utf-8")
        assert "We want feedback" in en_text
        assert "WARNUNG" in fr_text
