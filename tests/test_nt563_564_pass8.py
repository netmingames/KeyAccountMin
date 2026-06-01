"""NT-563/564/568 Pass 8: has_default file-aware + pending-Zaehler."""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as app_mod  # noqa: E402
from core import assets  # noqa: E402


def _png(w, h):
    from PIL import Image
    b = io.BytesIO(); Image.new("RGBA", (w, h), (1, 2, 3, 255)).save(b, format="PNG"); return b.getvalue()


def _jpg(w, h):
    from PIL import Image
    b = io.BytesIO(); Image.new("RGB", (w, h), (1, 2, 3)).save(b, format="JPEG"); return b.getvalue()


def _item(tmp_path):
    idir = tmp_path / "steam" / "1141975_p5"
    idir.mkdir(parents=True)
    (idir / "meta.json").write_text('{"item_id":"1141975","platform":"steam","name":"P5"}', encoding="utf-8")
    return idir


def test_status_slot_has_default_file_aware(tmp_path: Path) -> None:
    idir = _item(tmp_path)
    hc = assets.SLOTS_BY_KEY["header_capsule"]
    assets.store_asset(idir, "header_capsule", _png(hc.width, hc.height), ext="png")
    row = next(r for r in assets.status(idir, [])["slots"] if r["key"] == "header_capsule")
    assert row["has_default"] is True
    (idir / "assets" / "header_capsule" / "default.png").unlink()
    row2 = next(r for r in assets.status(idir, [])["slots"] if r["key"] == "header_capsule")
    assert row2["has_default"] is False and row2["default_size_ok"] is None


def test_status_screenshot_has_default_file_aware(tmp_path: Path) -> None:
    idir = _item(tmp_path)
    w, h = assets.SCREENSHOT_TARGET
    s = assets.add_screenshot(idir, _jpg(w, h), ext="jpg")
    item = next(i for i in assets.status(idir, [])["screenshots"]["items"] if i["id"] == s.id)
    assert item["has_default"] is True
    (idir / "assets" / "screenshots" / f"{s.id}_default.jpg").unlink()
    item2 = next(i for i in assets.status(idir, [])["screenshots"]["items"] if i["id"] == s.id)
    assert item2["has_default"] is False and item2["size_ok"] is None


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_mod, "DATA_ROOT", tmp_path)
    from fastapi.testclient import TestClient
    idir = tmp_path / "steam" / "1141975_p5"
    (idir / "translations").mkdir(parents=True)
    (idir / "meta.json").write_text(
        '{"schema_version":1,"item_id":"1141975","platform":"steam","name":"P5",'
        '"master_lang":"german","active_languages":["german"]}', encoding="utf-8")
    (idir / "master_de.json").write_text(
        '{"schema_version":1,"item_id":"1141975","lang":"de","fields":{"short_description":"x"}}', encoding="utf-8")
    return TestClient(app_mod.app)


def test_api_get_item_pending(client) -> None:
    # franzoesischen Stub erzeugen (alle Felder leer/stale)
    client.put("/api/items/steam/1141975/active-languages", json={"languages": ["german", "french"]})
    d = client.get("/api/items/steam/1141975").json()
    assert d["translations"]["french"]["pending"] == 1  # short_description leer+stale


def test_api_get_item_active_no_file_lang_listed(tmp_path, monkeypatch) -> None:
    # NT-564 Pass 8: aktive Zielsprache OHNE Translation-Datei -> trotzdem Summary
    # mit pending, damit das Pill nicht leer rendert.
    monkeypatch.setattr(app_mod, "DATA_ROOT", tmp_path)
    from fastapi.testclient import TestClient
    idir = tmp_path / "steam" / "1141975_p5"
    (idir / "translations").mkdir(parents=True)
    (idir / "meta.json").write_text(
        '{"schema_version":1,"item_id":"1141975","platform":"steam","name":"P5",'
        '"master_lang":"german","active_languages":["german","french"]}', encoding="utf-8")
    (idir / "master_de.json").write_text(
        '{"schema_version":1,"item_id":"1141975","lang":"de","fields":{"short_description":"x"}}', encoding="utf-8")
    # KEINE french.json
    d = TestClient(app_mod.app).get("/api/items/steam/1141975").json()
    assert d["translations"]["french"]["pending"] == 1
    assert d["translations"]["french"]["filled"] == 0
