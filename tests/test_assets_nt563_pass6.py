"""NT-563/564 Pass 6: Screenshot-n_overrides dateibewusst + Glossar-Schema-422."""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import assets  # noqa: E402


def _jpg(w, h):
    from PIL import Image
    b = io.BytesIO(); Image.new("RGB", (w, h), (1, 2, 3)).save(b, format="JPEG"); return b.getvalue()


def _item(tmp_path):
    idir = tmp_path / "steam" / "1141975_passage5"
    idir.mkdir(parents=True)
    (idir / "meta.json").write_text('{"item_id":"1141975","platform":"steam","name":"P5"}', encoding="utf-8")
    return idir


def test_status_screenshot_n_overrides_file_aware(tmp_path: Path) -> None:
    idir = _item(tmp_path)
    w, h = assets.SCREENSHOT_TARGET
    s = assets.add_screenshot(idir, _jpg(w, h), ext="jpg")
    assets.set_screenshot_override(idir, s.id, "german", _jpg(w, h), ext="jpg")
    item = next(i for i in assets.status(idir, ["german"])["screenshots"]["items"] if i["id"] == s.id)
    assert item["n_overrides"] == 1
    (idir / "assets" / "screenshots" / f"{s.id}_german.jpg").unlink()  # verwaist
    item2 = next(i for i in assets.status(idir, ["german"])["screenshots"]["items"] if i["id"] == s.id)
    assert item2["n_overrides"] == 0


@pytest.fixture
def client_and_item(tmp_path: Path, monkeypatch):
    import app as app_mod
    monkeypatch.setattr(app_mod, "DATA_ROOT", tmp_path)
    from fastapi.testclient import TestClient
    idir = tmp_path / "steam" / "1141975_passage5"
    (idir / "translations").mkdir(parents=True)
    (idir / "meta.json").write_text(
        '{"schema_version":1,"item_id":"1141975","platform":"steam","name":"P5",'
        '"master_lang":"german","active_languages":["german","french"]}', encoding="utf-8")
    (idir / "master_de.json").write_text(
        '{"schema_version":1,"item_id":"1141975","lang":"de","fields":{"short_description":"x"}}', encoding="utf-8")
    return TestClient(app_mod.app), idir


def test_translate_captions_schema_broken_glossary_422(client_and_item) -> None:
    client, idir = client_and_item
    w, h = assets.SCREENSHOT_TARGET
    client.post("/api/items/steam/1141975/assets/screenshots",
                files={"file": ("a.jpg", _jpg(w, h), "image/jpeg")}, data={"master_caption": "Tor"})
    # JSON-valide, aber Schema kaputt: entries enthaelt Nicht-Dicts
    (idir / "glossary.json").write_text('{"entries":["kein-dict","auch nicht"]}', encoding="utf-8")
    r = client.post("/api/items/steam/1141975/assets/screenshots/1/translate-captions",
                    json={"engine": "mock"})
    assert r.status_code == 422
    assert "glossary" in r.json()["detail"].lower()
