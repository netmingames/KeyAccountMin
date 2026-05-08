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


def test_get_item_returns_422_on_schema_invalid_meta(app_with_data, tmp_path: Path) -> None:
    """NT-549 Pass 3: meta.json ist JSON-decodierbar aber Pydantic-invalid -> 422.

    item_id muss als String matchen, damit Pass 1 den Folder zurueckliefert
    (sonst wird der Folder als unreadable behandelt und Pass 2 scheitert).
    Andere required Felder fehlen -> ValidationError im Endpoint -> 422.
    """
    from fastapi.testclient import TestClient

    idir = tmp_path / "steam" / "555_legacy"
    idir.mkdir(parents=True)
    # Pass 1 liest meta, mid="555" matched item_id, gibt Ordner zurueck.
    # Im Endpoint scheitert dann das Pydantic-Schema, weil master_lang etc.
    # fehlen -> ValidationError -> 422.
    (idir / "meta.json").write_text(
        # item_id fuer Pass 1-Match, aber required `name` fehlt -> ValidationError
        json.dumps({"item_id": "555", "platform": "steam"}),
        encoding="utf-8",
    )

    client = TestClient(app_with_data.app)
    resp = client.get("/api/items/steam/555")
    assert resp.status_code == 422
    assert "Schema" in resp.json()["detail"]


def test_export_preview_returns_422_on_corrupt_master(app_with_data, tmp_path: Path) -> None:
    """NT-550 Pass 4 (Lisbeth 16:05 MEDIUM FUNCTIONAL): /export-preview soll
    bei kaputter master_*.json kontrolliert 422 liefern, nicht 500."""
    from fastapi.testclient import TestClient
    import json as _json

    idir = tmp_path / "steam" / "1141975_passage5"
    idir.mkdir(parents=True)
    (idir / "meta.json").write_text(
        _json.dumps({
            "platform": "steam",
            "item_id": "1141975",
            "name": "Passage 5",
            "active_languages": [],
            "early_access": False,
            "schema_version": 1,
            "master_lang": "german",
        }),
        encoding="utf-8",
    )
    (idir / "master_de.json").write_text("{ corrupt", encoding="utf-8")

    client = TestClient(app_with_data.app)
    resp = client.get("/api/items/steam/1141975/export-preview")
    assert resp.status_code == 422
    assert "master" in resp.json()["detail"].lower()


def test_export_to_file_returns_422_on_corrupt_master(app_with_data, tmp_path: Path) -> None:
    """NT-550 Pass 4: POST /export bei kaputter master_*.json -> 422."""
    from fastapi.testclient import TestClient
    import json as _json

    idir = tmp_path / "steam" / "1141975_passage5"
    idir.mkdir(parents=True)
    (idir / "meta.json").write_text(
        _json.dumps({
            "platform": "steam",
            "item_id": "1141975",
            "name": "Passage 5",
            "active_languages": [],
            "early_access": False,
            "schema_version": 1,
            "master_lang": "german",
        }),
        encoding="utf-8",
    )
    (idir / "master_de.json").write_text("{ corrupt", encoding="utf-8")

    client = TestClient(app_with_data.app)
    resp = client.post("/api/items/steam/1141975/export")
    assert resp.status_code == 422


def test_get_translation_returns_422_on_corrupt_file(app_with_data, tmp_path: Path) -> None:
    """NT-550 Pass 4 (Lisbeth 16:05 MEDIUM FUNCTIONAL): /translation/{lang}
    soll bei kaputter Translation-Datei 422 liefern, nicht 500. Frontend
    kann dann einen Fehler-State rendern."""
    from fastapi.testclient import TestClient
    import json as _json

    idir = tmp_path / "steam" / "1141975_passage5"
    idir.mkdir(parents=True)
    (idir / "meta.json").write_text(
        _json.dumps({
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
    (idir / "translations").mkdir(parents=True, exist_ok=True)
    # Kaputte UTF-8-Bytes
    (idir / "translations" / "english.json").write_bytes(b'{"\xff\xfe": "BROKEN"}')

    client = TestClient(app_with_data.app)
    resp = client.get("/api/items/steam/1141975/translation/english")
    assert resp.status_code == 422
    assert "english" in resp.json()["detail"].lower() or "translation" in resp.json()["detail"].lower()


def test_get_item_returns_422_on_master_with_invalid_utf8(app_with_data, tmp_path: Path) -> None:
    """NT-549 Pass 6 (Lisbeth 15:54 MEDIUM FUNCTIONAL):
    master_*.json mit kaputten UTF-8-Bytes -> 422, nicht 500.
    Die alte Logik catched nur ValidationError/JSONDecodeError/OSError —
    UnicodeDecodeError aus json.load(open(..., encoding='utf-8')) ging
    durch und produzierte 500."""
    from fastapi.testclient import TestClient
    import json as _json

    idir = tmp_path / "steam" / "1141975_passage5"
    idir.mkdir(parents=True)
    (idir / "meta.json").write_text(
        _json.dumps({
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
    # Kaputte UTF-8-Bytes: 0xff ist in UTF-8 ungueltig als Start-Byte
    (idir / "master_de.json").write_bytes(b'{"item_id":"1141975","fields":{"about":"\xff\xfeBROKEN"}}')

    client = TestClient(app_with_data.app)
    resp = client.get("/api/items/steam/1141975")
    assert resp.status_code == 422
    assert "master" in resp.json()["detail"].lower()


def test_get_item_skips_translation_with_invalid_utf8(app_with_data, tmp_path: Path) -> None:
    """NT-549 Pass 6 (Lisbeth 15:54 MEDIUM FUNCTIONAL):
    translations/<lang>.json mit kaputten UTF-8-Bytes -> diese Sprache
    wird uebersprungen, andere weiter geliefert (kein 500)."""
    from fastapi.testclient import TestClient
    import json as _json

    idir = tmp_path / "steam" / "1141975_passage5"
    idir.mkdir(parents=True)
    (idir / "meta.json").write_text(
        _json.dumps({
            "platform": "steam",
            "item_id": "1141975",
            "name": "Passage 5",
            "active_languages": ["english", "french"],
            "early_access": False,
            "schema_version": 1,
            "master_lang": "german",
        }),
        encoding="utf-8",
    )
    (idir / "master_de.json").write_text(
        _json.dumps({
            "schema_version": 1,
            "item_id": "1141975",
            "lang": "german",
            "fields": {"about": "OK"},
            "updated_at": "2026-05-08T16:00:00",
        }),
        encoding="utf-8",
    )
    (idir / "translations").mkdir(parents=True, exist_ok=True)
    # Valide englische Translation
    (idir / "translations" / "english.json").write_text(
        _json.dumps({
            "schema_version": 1,
            "item_id": "1141975",
            "lang": "english",
            "fields": {"about": {"value": "EN", "stale": False, "manually_edited": False}},
            "updated_at": "2026-05-08T16:00:00",
        }),
        encoding="utf-8",
    )
    # Franzoesische Translation: kaputte UTF-8-Bytes
    (idir / "translations" / "french.json").write_bytes(b'{"\xff\xfe\xff": "BROKEN"}')

    client = TestClient(app_with_data.app)
    resp = client.get("/api/items/steam/1141975")
    assert resp.status_code == 200
    body = resp.json()
    # Englische Sprache durchgekommen, Franzoesisch wurde geskippt
    assert "english" in body["translations"]
    assert "french" not in body["translations"]


def test_get_item_returns_422_on_corrupt_master_json(app_with_data, tmp_path: Path) -> None:
    """NT-549 Pass 5 (Lisbeth 15:36 MEDIUM FUNCTIONAL):
    Korrupte master_*.json (JSON-Syntax kaputt, nicht nur Schema-mismatch)
    soll 422 sein, nicht 500."""
    from fastapi.testclient import TestClient
    import json as _json

    idir = tmp_path / "steam" / "1141975_passage5"
    idir.mkdir(parents=True)
    (idir / "meta.json").write_text(
        _json.dumps({
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
    # JSON-syntax kaputt
    (idir / "master_de.json").write_text("{ this is not json", encoding="utf-8")

    client = TestClient(app_with_data.app)
    resp = client.get("/api/items/steam/1141975")
    assert resp.status_code == 422
    assert "master" in resp.json()["detail"].lower()


def test_get_item_skips_corrupt_translation_json(app_with_data, tmp_path: Path) -> None:
    """NT-549 Pass 5 (Lisbeth 15:36 MEDIUM FUNCTIONAL):
    Korrupte translations/<lang>.json darf nicht das ganze Item brechen.
    Andere Sprachen muessen weiter geliefert werden."""
    from fastapi.testclient import TestClient
    import json as _json

    idir = tmp_path / "steam" / "1141975_passage5"
    idir.mkdir(parents=True)
    (idir / "meta.json").write_text(
        _json.dumps({
            "platform": "steam",
            "item_id": "1141975",
            "name": "Passage 5",
            "active_languages": ["english", "french"],
            "early_access": False,
            "schema_version": 1,
            "master_lang": "german",
        }),
        encoding="utf-8",
    )
    (idir / "master_de.json").write_text(
        _json.dumps({
            "schema_version": 1,
            "item_id": "1141975",
            "lang": "german",
            "fields": {"about": "DE about"},
            "updated_at": "2026-05-08T16:00:00",
        }),
        encoding="utf-8",
    )
    (idir / "translations").mkdir(parents=True)
    # english: valide
    (idir / "translations" / "english.json").write_text(
        _json.dumps({
            "schema_version": 1,
            "item_id": "1141975",
            "lang": "english",
            "fields": {"about": {"value": "EN about", "stale": False, "manually_edited": False}},
            "updated_at": "2026-05-08T16:00:00",
        }),
        encoding="utf-8",
    )
    # french: kaputt
    (idir / "translations" / "french.json").write_text("{ broken", encoding="utf-8")

    client = TestClient(app_with_data.app)
    resp = client.get("/api/items/steam/1141975")
    assert resp.status_code == 200
    body = resp.json()
    assert "english" in body["translations"]
    assert "french" not in body["translations"]


def test_translate_rejects_unknown_engine(app_with_data, tmp_path: Path) -> None:
    """NT-549 Pass 3: unbekannter engine-Wert -> 400 statt silent fallback."""
    from fastapi.testclient import TestClient

    idir = tmp_path / "steam" / "999_test"
    idir.mkdir(parents=True)
    (idir / "meta.json").write_text(
        json.dumps({
            "platform": "steam",
            "item_id": "999",
            "name": "Test",
            "active_languages": [],
            "early_access": False,
            "schema_version": 1,
            "master_lang": "german",
        }),
        encoding="utf-8",
    )

    client = TestClient(app_with_data.app)
    resp = client.post(
        "/api/items/steam/999/translate/english",
        json={"engine": "totally-not-a-translator"},
    )
    assert resp.status_code == 400
    assert "engine" in resp.json()["detail"].lower()
