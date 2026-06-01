"""API-Tests fuer die Asset-Endpoints (NT-564).

Fixture-Muster wie test_api_get_item_legacy: DATA_ROOT auf tmp_path patchen,
TestClient gegen app.app. Bilder werden mit Pillow erzeugt.
"""
from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture
def client_and_item(tmp_path: Path, monkeypatch):
    import app as app_mod
    monkeypatch.setattr(app_mod, "DATA_ROOT", tmp_path)
    from fastapi.testclient import TestClient

    idir = tmp_path / "steam" / "1141975_passage5"
    (idir / "translations").mkdir(parents=True)
    (idir / "meta.json").write_text(
        '{"schema_version":1,"item_id":"1141975","platform":"steam","name":"Passage 5",'
        '"master_lang":"german","active_languages":["german","french","english"]}',
        encoding="utf-8",
    )
    (idir / "master_de.json").write_text(
        '{"schema_version":1,"item_id":"1141975","lang":"de","fields":{"short_description":"x"}}',
        encoding="utf-8",
    )
    return TestClient(app_mod.app), idir


def _png(w: int, h: int) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGBA", (w, h), (1, 2, 3, 255)).save(buf, format="PNG")
    return buf.getvalue()


def _jpg(w: int, h: int) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (1, 2, 3)).save(buf, format="JPEG")
    return buf.getvalue()


def test_catalog(client_and_item) -> None:
    client, _ = client_and_item
    r = client.get("/api/assets/catalog")
    assert r.status_code == 200
    keys = {s["key"] for s in r.json()["slots"]}
    assert {"header_capsule", "screenshots"} <= keys


def test_upload_default_and_status_and_file(client_and_item) -> None:
    client, _ = client_and_item
    data = _png(460, 215)
    r = client.post(
        "/api/items/steam/1141975/assets/header_capsule",
        files={"file": ("h.png", data, "image/png")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["entry"]["size_ok"] is True

    st = client.get("/api/items/steam/1141975/assets").json()
    row = next(s for s in st["slots"] if s["key"] == "header_capsule")
    assert row["has_default"] is True
    # header_capsule ist localizable -> per_lang fuer aktive Sprachen, alle auf default
    assert row["per_lang"]["french"] == "default"

    f = client.get("/api/items/steam/1141975/assets/header_capsule/file?lang=french")
    assert f.status_code == 200 and f.headers["content-type"].startswith("image/")


def test_override_precedence(client_and_item) -> None:
    client, _ = client_and_item
    client.post("/api/items/steam/1141975/assets/header_capsule",
                files={"file": ("h.png", _png(460, 215), "image/png")})
    r = client.post("/api/items/steam/1141975/assets/header_capsule",
                    files={"file": ("h.png", _png(460, 215), "image/png")},
                    data={"lang": "german"})
    assert r.status_code == 200, r.text
    st = client.get("/api/items/steam/1141975/assets").json()
    row = next(s for s in st["slots"] if s["key"] == "header_capsule")
    assert row["per_lang"]["german"] == "override"
    assert row["per_lang"]["french"] == "default"


def test_non_localizable_override_rejected(client_and_item) -> None:
    client, _ = client_and_item
    r = client.post("/api/items/steam/1141975/assets/page_background",
                    files={"file": ("bg.png", _png(1438, 810), "image/png")},
                    data={"lang": "german"})
    assert r.status_code == 400


def test_upload_no_extension_400(client_and_item) -> None:
    client, _ = client_and_item
    r = client.post("/api/items/steam/1141975/assets/header_capsule",
                    files={"file": ("noext", _png(460, 215), "image/png")})
    assert r.status_code == 400


def test_delete_asset(client_and_item) -> None:
    client, _ = client_and_item
    client.post("/api/items/steam/1141975/assets/header_capsule",
                files={"file": ("h.png", _png(460, 215), "image/png")})
    r = client.delete("/api/items/steam/1141975/assets/header_capsule")
    assert r.status_code == 200 and r.json()["removed"] is True
    assert client.get("/api/items/steam/1141975/assets/header_capsule/file").status_code == 404


def test_screenshots_lifecycle(client_and_item) -> None:
    client, _ = client_and_item
    s1 = client.post("/api/items/steam/1141975/assets/screenshots",
                     files={"file": ("a.jpg", _jpg(1920, 1080), "image/jpeg")},
                     data={"master_caption": "Tor!"}).json()["screenshot"]
    s2 = client.post("/api/items/steam/1141975/assets/screenshots",
                     files={"file": ("b.jpg", _jpg(1920, 1080), "image/jpeg")}).json()["screenshot"]
    assert (s1["id"], s2["id"]) == (1, 2)

    # Override + Caption
    assert client.post(f"/api/items/steam/1141975/assets/screenshots/{s1['id']}/override",
                       files={"file": ("a_de.jpg", _jpg(1920, 1080), "image/jpeg")},
                       data={"lang": "german"}).status_code == 200
    assert client.put(f"/api/items/steam/1141975/assets/screenshots/{s1['id']}/caption",
                      json={"text": "Goal!", "lang": "english"}).status_code == 200

    # Reorder
    rr = client.put("/api/items/steam/1141975/assets/screenshots/reorder",
                    json={"ordered_ids": [s2["id"], s1["id"]]})
    assert rr.status_code == 200
    order = {s["id"]: s["order"] for s in rr.json()["screenshots"]}
    assert order == {2: 0, 1: 1}

    # File (override-Sprache)
    assert client.get(f"/api/items/steam/1141975/assets/screenshots/{s1['id']}/file?lang=german").status_code == 200

    # Delete
    assert client.delete(f"/api/items/steam/1141975/assets/screenshots/{s1['id']}").status_code == 200
    assert client.delete("/api/items/steam/1141975/assets/screenshots/999").status_code == 404


def test_export_zip(client_and_item) -> None:
    client, _ = client_and_item
    client.post("/api/items/steam/1141975/assets/header_capsule",
                files={"file": ("h.png", _png(460, 215), "image/png")})
    client.post("/api/items/steam/1141975/assets/screenshots",
                files={"file": ("a.jpg", _jpg(1920, 1080), "image/jpeg")})
    r = client.get("/api/items/steam/1141975/assets/export.zip?lang=french")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        names = zf.namelist()
    assert "header_capsule.png" in names
    assert "screenshots/01.jpg" in names
    assert "_README.txt" in names


def test_export_zip_bad_lang_400(client_and_item) -> None:
    client, _ = client_and_item
    assert client.get("/api/items/steam/1141975/assets/export.zip?lang=klingon").status_code == 400
