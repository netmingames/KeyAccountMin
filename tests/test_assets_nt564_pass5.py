"""NT-564 Pass 5 (Lisbeth 17:56) — 2 LOW FUNCTIONAL Findings.

1. get_screenshot_details() liefert size_ok/warnings je Override.
2. export_zip() labelt override/default an der TATSAECHLICH aufgeloesten Datei
   (verwaister Override -> default, kein irrefuehrendes "override" im README).
"""
from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import assets  # noqa: E402


def _jpg(w: int, h: int) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (1, 2, 3)).save(buf, format="JPEG")
    return buf.getvalue()


def _png(w: int, h: int) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGBA", (w, h), (1, 2, 3, 255)).save(buf, format="PNG")
    return buf.getvalue()


def _item(tmp_path: Path) -> Path:
    idir = tmp_path / "steam" / "1141975_passage5"
    idir.mkdir(parents=True)
    (idir / "meta.json").write_text('{"item_id":"1141975","platform":"steam","name":"P5"}',
                                    encoding="utf-8")
    return idir


def test_details_surface_override_size(tmp_path: Path) -> None:
    idir = _item(tmp_path)
    w, h = assets.SCREENSHOT_TARGET
    s = assets.add_screenshot(idir, _jpg(w, h), ext="jpg")
    # falsch dimensionierter Override
    assets.set_screenshot_override(idir, s.id, "german", _jpg(800, 600), ext="jpg")
    d = assets.get_screenshot_details(idir, s.id)
    assert d["overrides"]["german"]["size_ok"] is False
    assert d["overrides"]["german"]["warnings"]
    assert (d["overrides"]["german"]["width"], d["overrides"]["german"]["height"]) == (800, 600)


def test_details_correct_override_has_size_ok(tmp_path: Path) -> None:
    idir = _item(tmp_path)
    w, h = assets.SCREENSHOT_TARGET
    s = assets.add_screenshot(idir, _jpg(w, h), ext="jpg")
    assets.set_screenshot_override(idir, s.id, "german", _jpg(w, h), ext="jpg")
    d = assets.get_screenshot_details(idir, s.id)
    assert d["overrides"]["german"]["size_ok"] is True


def test_export_slot_stale_override_labeled_default(tmp_path: Path) -> None:
    idir = _item(tmp_path)
    hc = assets.SLOTS_BY_KEY["header_capsule"]
    assets.store_asset(idir, "header_capsule", _png(hc.width, hc.height), ext="png")
    assets.store_asset(idir, "header_capsule", _png(hc.width, hc.height), lang="german", ext="png")
    # Override-Datei loeschen, Manifest-Eintrag bleibt -> verwaist
    (idir / "assets" / "header_capsule" / "german.png").unlink()
    data = assets.export_zip(idir, "german")
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        readme = zf.read("_README.txt").decode("utf-8")
        names = zf.namelist()
    assert "header_capsule.png" in names  # default eingepackt
    line = next(l for l in readme.splitlines() if "Header Capsule" in l)
    assert "default" in line and "override" not in line


def test_export_screenshot_stale_override_falls_back(tmp_path: Path) -> None:
    idir = _item(tmp_path)
    w, h = assets.SCREENSHOT_TARGET
    s = assets.add_screenshot(idir, _jpg(w, h), ext="jpg")
    assets.set_screenshot_override(idir, s.id, "german", _jpg(w, h), ext="jpg")
    (idir / "assets" / "screenshots" / f"{s.id}_german.jpg").unlink()  # Override-Datei weg
    data = assets.export_zip(idir, "german")
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        readme = zf.read("_README.txt").decode("utf-8")
        names = zf.namelist()
    assert "screenshots/01.jpg" in names  # default eingepackt, nicht FEHLT
    line = next(l for l in readme.splitlines() if "Screenshot 01" in l)
    assert "default" in line and "override" not in line
