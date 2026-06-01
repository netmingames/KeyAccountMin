"""Tests fuer core.assets (NT-564) — Asset-/Screenshot-Verwaltung.

Deckt ab: Slot-Katalog, Bild-Validierung (Maß/Format als Warnung),
Default + Per-Sprache-Override mit Fallback, sprachneutrale Slots ohne
Override, Screenshots (add/override/caption/reorder/delete), Status-Uebersicht
und ZIP-Export.
"""
from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import assets  # noqa: E402


def _png(w: int, h: int) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGBA", (w, h), (10, 20, 30, 255)).save(buf, format="PNG")
    return buf.getvalue()


def _jpg(w: int, h: int) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (10, 20, 30)).save(buf, format="JPEG")
    return buf.getvalue()


def _item(tmp_path: Path) -> Path:
    idir = tmp_path / "steam" / "1141975_passage5"
    idir.mkdir(parents=True)
    (idir / "meta.json").write_text('{"item_id":"1141975","platform":"steam","name":"P5"}',
                                    encoding="utf-8")
    return idir


# --- Katalog + Validierung ---------------------------------------------------

def test_catalog_has_screenshots_and_slots() -> None:
    keys = {s["key"] for s in assets.catalog()}
    assert "header_capsule" in keys
    assert "screenshots" in keys
    assert assets.SLOTS_BY_KEY["page_background"].localizable is False


def test_validate_correct_png() -> None:
    slot = assets.SLOTS_BY_KEY["header_capsule"]
    v = assets.validate_image(_png(slot.width, slot.height),
                              target_w=slot.width, target_h=slot.height, formats=slot.formats)
    assert v.ok and v.size_ok and v.warnings == []


def test_validate_wrong_size_warns_but_ok() -> None:
    slot = assets.SLOTS_BY_KEY["header_capsule"]
    v = assets.validate_image(_png(100, 100),
                              target_w=slot.width, target_h=slot.height, formats=slot.formats)
    assert v.ok and not v.size_ok and any("Maß" in w for w in v.warnings)


def test_validate_garbage_not_ok() -> None:
    v = assets.validate_image(b"not an image", target_w=10, target_h=10, formats=("png",))
    assert v.ok is False and v.size_ok is False


# --- Feste Slots: default + override + fallback ------------------------------

def test_store_default_and_resolve(tmp_path: Path) -> None:
    idir = _item(tmp_path)
    slot = assets.SLOTS_BY_KEY["header_capsule"]
    assets.store_asset(idir, "header_capsule", _png(slot.width, slot.height), ext="png")
    # Ohne Override faellt jede Sprache auf default zurueck
    p = assets.resolve_asset(idir, "header_capsule", "french")
    assert p is not None and p.name == "default.png"


def test_override_takes_precedence(tmp_path: Path) -> None:
    idir = _item(tmp_path)
    slot = assets.SLOTS_BY_KEY["header_capsule"]
    assets.store_asset(idir, "header_capsule", _png(slot.width, slot.height), ext="png")
    assets.store_asset(idir, "header_capsule", _png(slot.width, slot.height),
                       lang="german", ext="png")
    assert assets.resolve_asset(idir, "header_capsule", "german").name == "german.png"
    assert assets.resolve_asset(idir, "header_capsule", "french").name == "default.png"


def test_non_localizable_rejects_override(tmp_path: Path) -> None:
    idir = _item(tmp_path)
    slot = assets.SLOTS_BY_KEY["page_background"]
    with pytest.raises(ValueError):
        assets.store_asset(idir, "page_background", _png(slot.width, slot.height),
                           lang="german", ext="png")


def test_ext_switch_removes_old_file(tmp_path: Path) -> None:
    idir = _item(tmp_path)
    slot = assets.SLOTS_BY_KEY["header_capsule"]
    assets.store_asset(idir, "header_capsule", _png(slot.width, slot.height), ext="png")
    assets.store_asset(idir, "header_capsule", _jpg(slot.width, slot.height), ext="jpg")
    slot_dir = idir / "assets" / "header_capsule"
    assert (slot_dir / "default.jpg").exists()
    assert not (slot_dir / "default.png").exists()
    assert assets.resolve_asset(idir, "header_capsule", "german").name == "default.jpg"


def test_delete_default(tmp_path: Path) -> None:
    idir = _item(tmp_path)
    slot = assets.SLOTS_BY_KEY["header_capsule"]
    assets.store_asset(idir, "header_capsule", _png(slot.width, slot.height), ext="png")
    assert assets.delete_asset(idir, "header_capsule") is True
    assert assets.resolve_asset(idir, "header_capsule", "german") is None


def test_unknown_slot_raises(tmp_path: Path) -> None:
    idir = _item(tmp_path)
    with pytest.raises(ValueError):
        assets.store_asset(idir, "nope", _png(10, 10), ext="png")


# --- Screenshots -------------------------------------------------------------

def test_screenshot_add_override_caption_reorder_delete(tmp_path: Path) -> None:
    idir = _item(tmp_path)
    w, h = assets.SCREENSHOT_TARGET
    s1 = assets.add_screenshot(idir, _jpg(w, h), ext="jpg", master_caption="Tor!")
    s2 = assets.add_screenshot(idir, _jpg(w, h), ext="jpg")
    assert (s1.id, s2.id) == (1, 2)

    assets.set_screenshot_override(idir, s1.id, "german", _jpg(w, h), ext="jpg")
    assets.set_screenshot_caption(idir, s1.id, "Goal!", lang="english")

    m = assets.load_manifest(idir)
    shot1 = next(s for s in m.screenshots if s.id == 1)
    assert "german" in shot1.localized
    assert shot1.captions["english"] == "Goal!"
    assert shot1.master_caption == "Tor!"

    assets.reorder_screenshots(idir, [s2.id, s1.id])
    order = {s.id: s.order for s in assets.load_manifest(idir).screenshots}
    assert order == {2: 0, 1: 1}

    assert assets.delete_screenshot(idir, s1.id) is True
    assert [s.id for s in assets.load_manifest(idir).screenshots] == [2]


def test_reorder_rejects_mismatched_ids(tmp_path: Path) -> None:
    idir = _item(tmp_path)
    w, h = assets.SCREENSHOT_TARGET
    assets.add_screenshot(idir, _jpg(w, h), ext="jpg")
    with pytest.raises(ValueError):
        assets.reorder_screenshots(idir, [99])


# --- Status + Export ---------------------------------------------------------

def test_status_reports_per_lang_resolution(tmp_path: Path) -> None:
    idir = _item(tmp_path)
    slot = assets.SLOTS_BY_KEY["header_capsule"]
    assets.store_asset(idir, "header_capsule", _png(slot.width, slot.height), ext="png")
    assets.store_asset(idir, "header_capsule", _png(slot.width, slot.height),
                       lang="german", ext="png")
    st = assets.status(idir, ["german", "english", "french"])
    row = next(r for r in st["slots"] if r["key"] == "header_capsule")
    assert row["per_lang"]["german"] == "override"
    assert row["per_lang"]["english"] == "default"
    assert row["n_override"] == 1


def test_export_zip_contains_assets_and_readme(tmp_path: Path) -> None:
    idir = _item(tmp_path)
    hc = assets.SLOTS_BY_KEY["header_capsule"]
    assets.store_asset(idir, "header_capsule", _png(hc.width, hc.height), ext="png")
    w, h = assets.SCREENSHOT_TARGET
    assets.add_screenshot(idir, _jpg(w, h), ext="jpg", master_caption="Hallo")

    data = assets.export_zip(idir, "german")
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        readme = zf.read("_README.txt").decode("utf-8")
    assert "header_capsule.png" in names
    assert "screenshots/01.jpg" in names
    assert "_README.txt" in names
    # small_capsule ist Pflicht und fehlt -> README weist darauf hin
    assert "small_capsule" in readme and "FEHLT" in readme


def test_export_zip_unknown_lang_raises(tmp_path: Path) -> None:
    idir = _item(tmp_path)
    with pytest.raises(ValueError):
        assets.export_zip(idir, "klingon")
