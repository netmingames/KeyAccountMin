"""NT-551 Pass 12 (Lisbeth 09:56 MEDIUM FUNCTIONAL).

`POST /api/items/{p}/{id}/translate/{lang}` fing bisher nur
``translator.TranslationError`` und ``ValueError``. Ein korruptes
``master_*.json`` oder ``translations/<lang>.json`` rutschte als 500 durch.
Erwartet: 422 mit kontrollierter Fehlermeldung, konsistent mit
``translate-all`` und ``translate-stream``.
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
    import app as app_mod  # noqa: WPS433
    monkeypatch.setattr(app_mod, "DATA_ROOT", tmp_path)
    return app_mod


def _seed_item(tmp_path: Path) -> Path:
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
    return idir


def test_translate_lang_returns_422_on_corrupt_master(app_with_data, tmp_path: Path) -> None:
    """Master_*.json mit kaputtem JSON -> 422 statt 500."""
    from fastapi.testclient import TestClient

    idir = _seed_item(tmp_path)
    (idir / "master_de.json").write_text("{ not json", encoding="utf-8")

    client = TestClient(app_with_data.app, raise_server_exceptions=False)
    resp = client.post(
        "/api/items/steam/1141975/translate/english",
        json={"engine": "mock"},
    )
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"].lower()
    assert "unlesbar" in detail or "schema" in detail


def test_translate_lang_returns_422_on_schema_invalid_master(app_with_data, tmp_path: Path) -> None:
    """Master_*.json mit invalidem Schema -> 422 statt 500."""
    from fastapi.testclient import TestClient

    idir = _seed_item(tmp_path)
    # Schema-invalid: fields ist ein int statt dict
    (idir / "master_de.json").write_text(
        json.dumps({
            "schema_version": 1,
            "item_id": "1141975",
            "lang": "german",
            "fields": 42,
            "updated_at": "2026-05-12T10:00:00",
        }),
        encoding="utf-8",
    )

    client = TestClient(app_with_data.app, raise_server_exceptions=False)
    resp = client.post(
        "/api/items/steam/1141975/translate/english",
        json={"engine": "mock"},
    )
    assert resp.status_code == 422, resp.text


def test_translate_lang_returns_422_on_invalid_utf8_master(app_with_data, tmp_path: Path) -> None:
    """Master_*.json mit kaputtem UTF-8 -> 422 statt 500."""
    from fastapi.testclient import TestClient

    idir = _seed_item(tmp_path)
    (idir / "master_de.json").write_bytes(b"\xff\xfe\x00invalid utf-8")

    client = TestClient(app_with_data.app, raise_server_exceptions=False)
    resp = client.post(
        "/api/items/steam/1141975/translate/english",
        json={"engine": "mock"},
    )
    assert resp.status_code == 422, resp.text


def test_translate_lang_returns_422_on_corrupt_existing_translation(
    app_with_data, tmp_path: Path
) -> None:
    """Eine bereits vorhandene translations/<lang>.json kaputt -> 422 statt 500."""
    from fastapi.testclient import TestClient

    idir = _seed_item(tmp_path)
    (idir / "master_de.json").write_text(
        json.dumps({
            "schema_version": 1,
            "item_id": "1141975",
            "lang": "german",
            "fields": {
                "about": {"value": "Eine deutsche About-Beschreibung."},
            },
            "updated_at": "2026-05-12T10:00:00",
        }),
        encoding="utf-8",
    )
    tdir = idir / "translations"
    tdir.mkdir()
    (tdir / "english.json").write_text("{ broken", encoding="utf-8")

    client = TestClient(app_with_data.app, raise_server_exceptions=False)
    resp = client.post(
        "/api/items/steam/1141975/translate/english",
        json={"engine": "mock"},
    )
    assert resp.status_code == 422, resp.text


# Sanity (gesunder Pfad bleibt 200) wird bereits durch test_translate_item_lang.py
# fuer den Translator selbst und durch tests/test_api_get_item_legacy.py fuer den
# Endpoint-Wrapper abgedeckt. Hier nur die corrupt-source-Guards.
