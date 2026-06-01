"""NT-563/564/565 Re-Review: stale-Override-Konsistenz + Fehler-Surfacing.

- status() / get_screenshot_details() melden Override nur, wenn die Datei da ist.
- api_screenshot_file() faellt bei verwaistem Override auf default zurueck (kein 404).
- translate-captions liefert bei korruptem glossary.json 422 statt 500.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import assets  # noqa: E402


def _jpg(w: int, h: int) -> bytes:
    from PIL import Image
    b = io.BytesIO(); Image.new("RGB", (w, h), (1, 2, 3)).save(b, format="JPEG"); return b.getvalue()


def _png(w: int, h: int) -> bytes:
    from PIL import Image
    b = io.BytesIO(); Image.new("RGBA", (w, h), (1, 2, 3, 255)).save(b, format="PNG"); return b.getvalue()


def _item(tmp_path: Path) -> Path:
    idir = tmp_path / "steam" / "1141975_passage5"
    idir.mkdir(parents=True)
    (idir / "meta.json").write_text('{"item_id":"1141975","platform":"steam","name":"P5"}', encoding="utf-8")
    return idir


# --- core -------------------------------------------------------------------

def test_status_stale_override_falls_to_default(tmp_path: Path) -> None:
    idir = _item(tmp_path)
    hc = assets.SLOTS_BY_KEY["header_capsule"]
    assets.store_asset(idir, "header_capsule", _png(hc.width, hc.height), ext="png")
    assets.store_asset(idir, "header_capsule", _png(hc.width, hc.height), lang="german", ext="png")
    (idir / "assets" / "header_capsule" / "german.png").unlink()  # Override-Datei weg
    st = assets.status(idir, ["german", "french"])
    row = next(r for r in st["slots"] if r["key"] == "header_capsule")
    assert row["per_lang"]["german"]["mode"] == "default"  # nicht mehr "override"
    assert row["n_override"] == 0


def test_details_excludes_stale_override(tmp_path: Path) -> None:
    idir = _item(tmp_path)
    w, h = assets.SCREENSHOT_TARGET
    s = assets.add_screenshot(idir, _jpg(w, h), ext="jpg")
    assets.set_screenshot_override(idir, s.id, "german", _jpg(w, h), ext="jpg")
    (idir / "assets" / "screenshots" / f"{s.id}_german.jpg").unlink()
    d = assets.get_screenshot_details(idir, s.id)
    assert "german" not in d["has_override"]
    assert "german" not in d["overrides"]


# --- api --------------------------------------------------------------------

@pytest.fixture
def client_and_item(tmp_path: Path, monkeypatch):
    import app as app_mod
    monkeypatch.setattr(app_mod, "DATA_ROOT", tmp_path)
    from fastapi.testclient import TestClient
    idir = tmp_path / "steam" / "1141975_passage5"
    (idir / "translations").mkdir(parents=True)
    (idir / "meta.json").write_text(
        '{"schema_version":1,"item_id":"1141975","platform":"steam","name":"P5",'
        '"master_lang":"german","active_languages":["german","french","english"]}', encoding="utf-8")
    (idir / "master_de.json").write_text(
        '{"schema_version":1,"item_id":"1141975","lang":"de","fields":{"short_description":"x"}}', encoding="utf-8")
    return TestClient(app_mod.app), idir


def test_api_screenshot_file_stale_override_serves_default(client_and_item) -> None:
    client, idir = client_and_item
    w, h = assets.SCREENSHOT_TARGET
    client.post("/api/items/steam/1141975/assets/screenshots",
                files={"file": ("a.jpg", _jpg(w, h), "image/jpeg")})
    client.post("/api/items/steam/1141975/assets/screenshots/1/override",
                files={"file": ("a_de.jpg", _jpg(w, h), "image/jpeg")}, data={"lang": "german"})
    (idir / "assets" / "screenshots" / "1_german.jpg").unlink()  # Override verwaist
    r = client.get("/api/items/steam/1141975/assets/screenshots/1/file?lang=german")
    assert r.status_code == 200  # default ausgeliefert statt 404
    assert r.headers["content-type"].startswith("image/")


def test_translate_captions_corrupt_glossary_422(client_and_item) -> None:
    client, idir = client_and_item
    w, h = assets.SCREENSHOT_TARGET
    client.post("/api/items/steam/1141975/assets/screenshots",
                files={"file": ("a.jpg", _jpg(w, h), "image/jpeg")}, data={"master_caption": "Tor"})
    (idir / "glossary.json").write_text("{ kaputt", encoding="utf-8")
    r = client.post("/api/items/steam/1141975/assets/screenshots/1/translate-captions",
                    json={"engine": "mock"})
    assert r.status_code == 422
    assert "glossary" in r.json()["detail"].lower()
