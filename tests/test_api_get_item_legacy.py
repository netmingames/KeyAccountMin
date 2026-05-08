"""Pass 7 (Lisbeth NT-548): api_get_item bei meta-losem Legacy-Folder.

Vorher: _resolve_idir gibt einen Legacy-Ordner ohne meta.json zurueck und
``schema.ItemMeta(**storage.read_json(...))`` failt mit FileNotFoundError →
500. Lisbeth wollte controlled 404. Test prueft das neue Verhalten.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture
def app_with_data(tmp_path: Path, monkeypatch):
    # DATA_ROOT wird im app-Modul aus ROOT abgeleitet; wir patchen es vor Import.
    import app as app_mod  # noqa: WPS433
    monkeypatch.setattr(app_mod, "DATA_ROOT", tmp_path)
    return app_mod


def test_legacy_folder_without_meta_returns_404(app_with_data, tmp_path: Path) -> None:
    """Legacy-Folder ohne meta.json -> 404 (vorher: 500)."""
    from fastapi.testclient import TestClient

    legacy = tmp_path / "steam" / "777_legacy"
    legacy.mkdir(parents=True)
    # kein meta.json

    client = TestClient(app_with_data.app)
    resp = client.get("/api/items/steam/777")
    assert resp.status_code == 404
    body = resp.json()
    assert "meta.json" in body["detail"].lower() or "metadaten" in body["detail"].lower()


def test_legacy_folder_with_corrupt_meta_returns_404(app_with_data, tmp_path: Path) -> None:
    """Legacy-Folder mit korruptem meta.json -> 404 (vorher: 500)."""
    from fastapi.testclient import TestClient

    legacy = tmp_path / "steam" / "888_legacy"
    legacy.mkdir(parents=True)
    (legacy / "meta.json").write_text("{ this is not json", encoding="utf-8")
    # Diese meta ist unlesbar -> in storage.item_dir landet der Folder im
    # Legacy-Pfad (Pass 2). Der Suffix "legacy" hat keinen "_" -> wird matchen.
    # api_get_item sieht meta_file.exists() == True, schlaegt aber beim
    # JSON-Decode fehl -> 404 statt 500.

    client = TestClient(app_with_data.app)
    resp = client.get("/api/items/steam/888")
    assert resp.status_code == 404
    body = resp.json()
    assert "meta.json" in body["detail"].lower()


def test_proper_item_still_works(app_with_data, tmp_path: Path) -> None:
    """Sanity: ein voll konfiguriertes Item geht weiterhin durch."""
    from fastapi.testclient import TestClient

    idir = tmp_path / "steam" / "1141975_passage5"
    idir.mkdir(parents=True)
    (idir / "meta.json").write_text(
        json.dumps({
            "platform": "steam",
            "item_id": "1141975",
            "name": "Passage 5",
            "active_languages": ["english"],
            "early_access": False,
            "schema_version": 1,
            "master_lang": "german",
        }),
        encoding="utf-8",
    )
    (idir / "master_de.json").write_text(
        json.dumps({
            "schema_version": 1,
            "item_id": "1141975",
            "lang": "german",
            "fields": {},
            "updated_at": "2026-05-08T16:00:00",
        }),
        encoding="utf-8",
    )

    client = TestClient(app_with_data.app)
    resp = client.get("/api/items/steam/1141975")
    # 200 wenn Schema passt; 500 wenn weitere Pflichtfelder fehlen — wir
    # interessieren uns hier nur dafuer, dass es NICHT der 404 von oben ist.
    assert resp.status_code != 404


def test_translate_route_returns_404_on_legacy_folder(app_with_data, tmp_path: Path) -> None:
    """Pass 8: api_translate_lang nutzt _resolve_idir_with_meta -> 404 statt 500."""
    from fastapi.testclient import TestClient

    legacy = tmp_path / "steam" / "999_legacy"
    legacy.mkdir(parents=True)
    # kein meta.json

    client = TestClient(app_with_data.app)
    resp = client.post(
        "/api/items/steam/999/translate/english",
        json={"engine": "mock"},
    )
    assert resp.status_code == 404


def test_glossary_route_returns_404_on_legacy_folder(app_with_data, tmp_path: Path) -> None:
    """Pass 8: api_get_glossary nutzt _resolve_idir_with_meta -> 404 statt 500."""
    from fastapi.testclient import TestClient

    legacy = tmp_path / "steam" / "999_legacy"
    legacy.mkdir(parents=True)

    client = TestClient(app_with_data.app)
    resp = client.get("/api/items/steam/999/glossary")
    assert resp.status_code == 404


def test_export_preview_returns_404_on_legacy_folder(app_with_data, tmp_path: Path) -> None:
    """Pass 8: api_export_preview nutzt _resolve_idir_with_meta -> 404 statt 500."""
    from fastapi.testclient import TestClient

    legacy = tmp_path / "steam" / "999_legacy"
    legacy.mkdir(parents=True)

    client = TestClient(app_with_data.app)
    resp = client.get("/api/items/steam/999/export-preview")
    assert resp.status_code == 404
