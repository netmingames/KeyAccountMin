"""NT-563/564 Pass 7: _stale_or_empty robustness + langs-dedupe + default-Datei."""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as app_mod  # noqa: E402
from core import assets  # noqa: E402


def _jpg(w, h):
    from PIL import Image
    b = io.BytesIO(); Image.new("RGB", (w, h), (1, 2, 3)).save(b, format="JPEG"); return b.getvalue()


def _item(tmp_path, master_fields='{"short_description":"x","about":"y"}'):
    idir = tmp_path / "steam" / "1141975_p5"
    (idir / "translations").mkdir(parents=True)
    (idir / "meta.json").write_text(
        '{"schema_version":1,"item_id":"1141975","platform":"steam","name":"P5",'
        '"master_lang":"german","active_languages":["german","french"]}', encoding="utf-8")
    (idir / "master_de.json").write_text(
        '{"schema_version":1,"item_id":"1141975","lang":"de","fields":' + master_fields + '}', encoding="utf-8")
    return idir


def test_stale_skips_manually_edited(tmp_path: Path) -> None:
    idir = _item(tmp_path)
    (idir / "translations" / "french.json").write_text(json.dumps({
        "schema_version": 1, "item_id": "1141975", "lang": "french", "fields": {
            "short_description": {"value": "hand", "source_hash": "", "stale": True, "manually_edited": True, "last_translated_at": ""},
            "about": {"value": "", "source_hash": "", "stale": True, "manually_edited": False, "last_translated_at": ""},
        }}), encoding="utf-8")
    # short_description ist manuell (heilig) -> nicht in der Liste; about leer -> drin
    assert app_mod._stale_or_empty_fields(idir, "french") == ["about"]


def test_stale_corrupt_master_raises(tmp_path: Path) -> None:
    # NT-563 Pass 8: korrupter Master wirft -> per-Sprache-Fehler im Stream,
    # statt stiller 0-Felder-No-op.
    idir = _item(tmp_path)
    (idir / "master_de.json").write_text("{ kaputt", encoding="utf-8")
    with pytest.raises(Exception):
        app_mod._stale_or_empty_fields(idir, "french")


def test_screenshot_details_default_file_aware(tmp_path: Path) -> None:
    idir = tmp_path / "steam" / "1141975_p5b"
    idir.mkdir(parents=True)
    (idir / "meta.json").write_text('{"item_id":"1141975","platform":"steam","name":"P5"}', encoding="utf-8")
    w, h = assets.SCREENSHOT_TARGET
    s = assets.add_screenshot(idir, _jpg(w, h), ext="jpg")
    assert assets.get_screenshot_details(idir, s.id)["has_default"] is True
    (idir / "assets" / "screenshots" / f"{s.id}_default.jpg").unlink()  # default-Datei weg
    d = assets.get_screenshot_details(idir, s.id)
    assert d["has_default"] is False and d["default_size_ok"] is None


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_mod, "DATA_ROOT", tmp_path)
    from fastapi.testclient import TestClient
    idir = tmp_path / "steam" / "1141975_p5"
    (idir / "translations").mkdir(parents=True)
    (idir / "meta.json").write_text(
        '{"schema_version":1,"item_id":"1141975","platform":"steam","name":"P5",'
        '"master_lang":"german","active_languages":["german","french"]}', encoding="utf-8")
    (idir / "master_de.json").write_text(
        '{"schema_version":1,"item_id":"1141975","lang":"de","fields":{"short_description":"x"}}', encoding="utf-8")
    return TestClient(app_mod.app)


def test_stream_langs_deduped(client):
    body = client.get("/api/items/steam/1141975/translate-stream?engine=mock&langs=french,french,french").text
    # 'start'-Event meldet n_total=1, french-lang_start kommt nur einmal
    assert '"n_total": 1' in body
    assert body.count('event: lang_start') == 1
