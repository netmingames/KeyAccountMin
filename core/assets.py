"""Asset-/Screenshot-Verwaltung fuer Sid (NT-564).

Phase 2 der Roadmap: Steam-Grafiken (Capsules, Library-Assets, Icons) und
Screenshots pro Item verwalten — mehrsprachig, aber mit minimalem Aufwand.

Designprinzip (mit Thomas abgestimmt):
- Sprachneutrale Slots (Hintergrund, Icons, Gameplay-Screenshots): EIN Bild
  ("default") gilt fuer alle Sprachen.
- Lokalisierbare Slots (Capsules mit Text, Library-Logo): EIN default-Bild
  plus OPTIONALE Per-Sprache-Overrides. resolve_asset() faellt automatisch
  auf default zurueck, wenn fuer eine Sprache kein Override hinterlegt ist.

Das spiegelt das Text-Modell (DE-Master + abgeleitete Sprachen): Thomas
pflegt einmal den Standard und ueberschreibt nur dort, wo wirklich Text im
Bild steckt.

Layout auf der Platte (pro Item), Quelle der Wahrheit ist die manifest.json:

  assets/
    manifest.json
    header_capsule/
      default.png
      german.png           # optionaler Per-Sprache-Override
    page_background/
      default.png
    screenshots/
      1_default.jpg
      1_german.jpg         # optionaler Per-Sprache-Override
      2_default.jpg

Steam hat keine Schreib-API fuer Assets — der Export ist ein ZIP pro Sprache
mit korrekten Dateinamen zum manuellen Upload im Partner-Backend (Phase 4
automatisiert das via Playwright).
"""
from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from . import steam_codes, storage

SCHEMA_VERSION = 1

# "default" ist der Slot-interne Schluessel fuer das sprachneutrale Bild.
# Bewusst KEIN gueltiger Steam-Sprachcode, damit es nie mit einer echten
# Sprache kollidiert.
DEFAULT_KEY = "default"


# --- Slot-Katalog ------------------------------------------------------------

class AssetSlot(BaseModel):
    """Spezifikation eines festen Asset-Typs (Sollmaße, Format, Lokalisierbarkeit)."""

    key: str
    label: str
    width: int
    height: int
    formats: tuple[str, ...] = ("png",)  # erlaubte Endungen (klein, ohne Punkt)
    localizable: bool = False
    category: str = "store"  # "store" | "library" | "icon"
    required: bool = False
    note: str = ""


# Sollmaße entsprechen Steams aktueller Asset-Doku. Die Validierung behandelt
# Maß-/Format-Abweichungen als WARNUNG (nicht als harten Block) — Thomas kann
# bewusst abweichen, das UI markiert es nur rot.
SLOTS: tuple[AssetSlot, ...] = (
    AssetSlot(key="small_capsule", label="Small Capsule", width=231, height=87,
              formats=("png", "jpg"), localizable=True, category="store", required=True,
              note="Suchergebnisse/Listen. Muss Logo/Titel enthalten -> oft lokalisiert."),
    AssetSlot(key="header_capsule", label="Header Capsule", width=460, height=215,
              formats=("png", "jpg"), localizable=True, category="store", required=True,
              note="Kopf der Store-Seite, in Listen."),
    AssetSlot(key="main_capsule", label="Main Capsule", width=616, height=353,
              formats=("png", "jpg"), localizable=True, category="store",
              note="Featured/Frontpage-Aktionen."),
    AssetSlot(key="vertical_capsule", label="Vertical Capsule", width=374, height=448,
              formats=("png", "jpg"), localizable=True, category="store",
              note="Sales/Seasonal-Aktionen."),
    AssetSlot(key="page_background", label="Page Background", width=1438, height=810,
              formats=("png", "jpg"), localizable=False, category="store",
              note="Hintergrund der Store-Seite. Sprachneutral."),
    AssetSlot(key="library_capsule", label="Library Capsule", width=600, height=900,
              formats=("png", "jpg"), localizable=False, category="library",
              note="Hochkant-Kachel in der Steam-Bibliothek."),
    AssetSlot(key="library_hero", label="Library Hero", width=1920, height=620,
              formats=("png", "jpg"), localizable=False, category="library",
              note="Banner oben in der Bibliothek. Px final aus Steam-Doc pinnen."),
    AssetSlot(key="library_logo", label="Library Logo (transparent)", width=1280, height=720,
              formats=("png",), localizable=True, category="library",
              note="Transparentes PNG, liegt ueber dem Hero. Bei Text-Logo lokalisierbar."),
    AssetSlot(key="community_icon", label="Community Icon", width=184, height=184,
              formats=("png", "jpg"), localizable=False, category="icon",
              note="Rundes Community-Icon. Sprachneutral."),
)

SLOTS_BY_KEY: dict[str, AssetSlot] = {s.key: s for s in SLOTS}

# Screenshots sind ein Sonderfall (variable Anzahl + Reihenfolge + Caption),
# darum nicht in SLOTS, sondern eigene Funktionen weiter unten.
SCREENSHOT_TARGET = (1920, 1080)
SCREENSHOT_FORMATS = ("jpg", "png")


def catalog() -> list[dict]:
    """Slot-Katalog als JSON-serialisierbare Liste fuer das UI."""
    out = [s.model_dump() for s in SLOTS]
    out.append({
        "key": "screenshots",
        "label": "Screenshots",
        "width": SCREENSHOT_TARGET[0],
        "height": SCREENSHOT_TARGET[1],
        "formats": list(SCREENSHOT_FORMATS),
        "localizable": True,
        "category": "screenshots",
        "required": True,
        "note": "Mind. 5 empfohlen. Bild sprachneutral; Caption laeuft durch die Uebersetzung.",
        "multi": True,
    })
    return out


# --- Manifest-Modell ---------------------------------------------------------

class AssetFile(BaseModel):
    """Metadaten einer einzelnen Asset-Datei (das Bild selbst liegt daneben)."""

    filename: str            # relativ zum Slot-Ordner, z.B. "default.png"
    width: int = 0
    height: int = 0
    fmt: str = ""            # "png" | "jpg"
    bytes_size: int = 0
    size_ok: bool = True     # entspricht das Bild den Sollmaßen/-format?
    warnings: list[str] = Field(default_factory=list)
    uploaded_at: str = ""


class SlotState(BaseModel):
    """Zustand eines festen Slots: Default-Bild + Per-Sprache-Overrides."""

    default: AssetFile | None = None
    localized: dict[str, AssetFile] = Field(default_factory=dict)


class Screenshot(BaseModel):
    """Ein Screenshot: stabile id, Reihenfolge, Bild (default + Overrides), Caption je Sprache."""

    id: int
    order: int = 0
    default: AssetFile | None = None
    localized: dict[str, AssetFile] = Field(default_factory=dict)
    # Caption-Texte: master_caption (DE) ist die Quelle, captions[lang] die
    # Uebersetzung. Verdrahtung mit der Claude-CLI-Pipeline folgt im selben
    # Ticket; das Modell haelt die Felder schon bereit.
    master_caption: str = ""
    captions: dict[str, str] = Field(default_factory=dict)


class AssetManifest(BaseModel):
    """assets/manifest.json — Quelle der Wahrheit ueber alle Assets eines Items."""

    schema_version: int = SCHEMA_VERSION
    item_id: str = ""
    slots: dict[str, SlotState] = Field(default_factory=dict)
    screenshots: list[Screenshot] = Field(default_factory=list)
    updated_at: str = ""


def _assets_dir(idir: Path) -> Path:
    return idir / "assets"


def _manifest_path(idir: Path) -> Path:
    return _assets_dir(idir) / "manifest.json"


def load_manifest(idir: Path, item_id: str = "") -> AssetManifest:
    """Liest die manifest.json oder liefert ein leeres Manifest (kein Schreiben)."""
    p = _manifest_path(idir)
    if not p.exists():
        return AssetManifest(item_id=item_id)
    return AssetManifest(**storage.read_json(p))


def save_manifest(idir: Path, manifest: AssetManifest) -> None:
    manifest.updated_at = storage.now_iso()
    storage.write_json_atomic(_manifest_path(idir), manifest.model_dump())


# --- Validierung -------------------------------------------------------------

def _norm_fmt(fmt: str) -> str:
    """Pillow-Format-Name -> unsere Endung. JPEG->jpg, PNG->png, ..."""
    f = (fmt or "").lower()
    return {"jpeg": "jpg", "jpg": "jpg", "png": "png"}.get(f, f)


class ValidationResult(BaseModel):
    ok: bool                 # lesbares Bild in erlaubtem Format (Maß egal)
    size_ok: bool            # Maß UND Format entsprechen der Spec
    width: int = 0
    height: int = 0
    fmt: str = ""
    warnings: list[str] = Field(default_factory=list)


def validate_image(data: bytes, *, target_w: int, target_h: int,
                   formats: tuple[str, ...]) -> ValidationResult:
    """Prueft Bild-Bytes gegen Sollmaß/-format. Maß-/Format-Abweichung = Warnung.

    ok=False nur wenn die Bytes gar kein lesbares Bild sind. Sonst ok=True und
    size_ok zeigt, ob Maß + Format stimmen (UI: rotes Badge bei size_ok=False).
    """
    try:
        from PIL import Image  # lazy import: Modul laedt auch ohne Pillow
        with Image.open(io.BytesIO(data)) as im:
            w, h = im.size
            fmt = _norm_fmt(im.format or "")
    except Exception as e:  # noqa: BLE001 - kaputte/kein Bild -> kontrolliertes ok=False
        return ValidationResult(ok=False, size_ok=False,
                                warnings=[f"Kein lesbares Bild: {e}"])

    warnings: list[str] = []
    if fmt not in formats:
        warnings.append(f"Format {fmt or '?'} nicht in {list(formats)}")
    if (w, h) != (target_w, target_h):
        warnings.append(f"Maß {w}x{h} != Soll {target_w}x{target_h}")
    return ValidationResult(
        ok=True, size_ok=not warnings, width=w, height=h, fmt=fmt, warnings=warnings,
    )


def _lang_key(lang: str | None) -> str:
    """None/'default' -> DEFAULT_KEY, sonst validierter Steam-Sprachcode."""
    if lang in (None, "", DEFAULT_KEY):
        return DEFAULT_KEY
    if not steam_codes.is_valid(lang):
        raise ValueError(f"Unbekannte Sprache: {lang!r}")
    return lang


_SAFE_EXT = re.compile(r"^[a-z0-9]+$")


def _check_ext(ext: str, allowed: tuple[str, ...]) -> str:
    ext = ext.lower().lstrip(".")
    if ext == "jpeg":
        ext = "jpg"
    if not _SAFE_EXT.match(ext):
        raise ValueError(f"Ungueltige Datei-Endung: {ext!r}")
    if ext not in allowed:
        raise ValueError(f"Endung {ext!r} nicht erlaubt fuer diesen Slot (erlaubt: {list(allowed)})")
    return ext


def _meta_from_validation(filename: str, v: ValidationResult, size: int) -> AssetFile:
    return AssetFile(
        filename=filename, width=v.width, height=v.height, fmt=v.fmt,
        bytes_size=size, size_ok=v.size_ok, warnings=v.warnings,
        uploaded_at=storage.now_iso(),
    )


# --- Feste Slots: store / resolve / delete -----------------------------------

def store_asset(idir: Path, slot_key: str, data: bytes, *,
                lang: str | None = None, ext: str) -> AssetFile:
    """Speichert ein Bild fuer einen festen Slot (default oder Per-Sprache).

    Schreibt assets/<slot>/<langkey>.<ext>, aktualisiert das Manifest und
    raeumt eine evtl. vorhandene Datei mit anderer Endung fuer denselben
    Langkey weg (z.B. Wechsel png -> jpg).
    """
    slot = SLOTS_BY_KEY.get(slot_key)
    if slot is None:
        raise ValueError(f"Unbekannter Slot: {slot_key!r}")
    if not slot.localizable and _lang_key(lang) != DEFAULT_KEY:
        raise ValueError(f"Slot {slot_key!r} ist sprachneutral — kein Per-Sprache-Override erlaubt")
    lk = _lang_key(lang)
    ext = _check_ext(ext, slot.formats)
    v = validate_image(data, target_w=slot.width, target_h=slot.height, formats=slot.formats)
    if not v.ok:
        raise ValueError(v.warnings[0] if v.warnings else "Kein lesbares Bild")

    slot_dir = _assets_dir(idir) / slot_key
    slot_dir.mkdir(parents=True, exist_ok=True)
    _remove_other_ext(slot_dir, lk, keep_ext=ext)
    filename = f"{lk}.{ext}"
    _write_bytes_atomic(slot_dir / filename, data)

    manifest = load_manifest(idir)
    st = manifest.slots.get(slot_key) or SlotState()
    entry = _meta_from_validation(filename, v, len(data))
    if lk == DEFAULT_KEY:
        st.default = entry
    else:
        st.localized[lk] = entry
    manifest.slots[slot_key] = st
    save_manifest(idir, manifest)
    return entry


def resolve_asset(idir: Path, slot_key: str, lang: str | None) -> Path | None:
    """Liefert den Pfad des fuer `lang` gueltigen Bildes (Override sonst default)."""
    manifest = load_manifest(idir)
    st = manifest.slots.get(slot_key)
    if st is None:
        return None
    lk = _lang_key(lang)
    entry = st.localized.get(lk) if lk != DEFAULT_KEY else None
    if entry is None:
        entry = st.default
    if entry is None:
        return None
    p = _assets_dir(idir) / slot_key / entry.filename
    return p if p.exists() else None


def delete_asset(idir: Path, slot_key: str, lang: str | None = None) -> bool:
    """Loescht das default- oder Per-Sprache-Bild eines Slots. True wenn etwas weg war."""
    if slot_key not in SLOTS_BY_KEY:
        raise ValueError(f"Unbekannter Slot: {slot_key!r}")
    lk = _lang_key(lang)
    manifest = load_manifest(idir)
    st = manifest.slots.get(slot_key)
    if st is None:
        return False
    entry = st.default if lk == DEFAULT_KEY else st.localized.get(lk)
    if entry is None:
        return False
    _unlink_quiet(_assets_dir(idir) / slot_key / entry.filename)
    if lk == DEFAULT_KEY:
        st.default = None
    else:
        st.localized.pop(lk, None)
    manifest.slots[slot_key] = st
    save_manifest(idir, manifest)
    return True


# --- Screenshots -------------------------------------------------------------

def add_screenshot(idir: Path, data: bytes, *, ext: str,
                   master_caption: str = "") -> Screenshot:
    """Fuegt einen neuen Screenshot hinten an (default-Bild)."""
    ext = _check_ext(ext, SCREENSHOT_FORMATS)
    v = validate_image(data, target_w=SCREENSHOT_TARGET[0], target_h=SCREENSHOT_TARGET[1],
                       formats=SCREENSHOT_FORMATS)
    if not v.ok:
        raise ValueError(v.warnings[0] if v.warnings else "Kein lesbares Bild")

    manifest = load_manifest(idir)
    new_id = (max((s.id for s in manifest.screenshots), default=0)) + 1
    new_order = (max((s.order for s in manifest.screenshots), default=-1)) + 1
    sdir = _assets_dir(idir) / "screenshots"
    sdir.mkdir(parents=True, exist_ok=True)
    filename = f"{new_id}_{DEFAULT_KEY}.{ext}"
    _write_bytes_atomic(sdir / filename, data)

    shot = Screenshot(
        id=new_id, order=new_order,
        default=_meta_from_validation(filename, v, len(data)),
        master_caption=master_caption,
    )
    manifest.screenshots.append(shot)
    save_manifest(idir, manifest)
    return shot


def set_screenshot_override(idir: Path, shot_id: int, lang: str, data: bytes, *,
                            ext: str) -> Screenshot:
    """Hinterlegt ein Per-Sprache-Bild fuer einen Screenshot."""
    lk = _lang_key(lang)
    if lk == DEFAULT_KEY:
        raise ValueError("Override braucht eine echte Sprache (nicht default)")
    ext = _check_ext(ext, SCREENSHOT_FORMATS)
    v = validate_image(data, target_w=SCREENSHOT_TARGET[0], target_h=SCREENSHOT_TARGET[1],
                       formats=SCREENSHOT_FORMATS)
    if not v.ok:
        raise ValueError(v.warnings[0] if v.warnings else "Kein lesbares Bild")
    manifest = load_manifest(idir)
    shot = _find_shot(manifest, shot_id)
    sdir = _assets_dir(idir) / "screenshots"
    sdir.mkdir(parents=True, exist_ok=True)
    _remove_other_ext(sdir, f"{shot_id}_{lk}", keep_ext=ext)
    filename = f"{shot_id}_{lk}.{ext}"
    _write_bytes_atomic(sdir / filename, data)
    shot.localized[lk] = _meta_from_validation(filename, v, len(data))
    save_manifest(idir, manifest)
    return shot


def set_screenshot_caption(idir: Path, shot_id: int, text: str, *,
                           lang: str | None = None) -> Screenshot:
    """Setzt die Master-Caption (lang=None) oder eine uebersetzte Caption."""
    manifest = load_manifest(idir)
    shot = _find_shot(manifest, shot_id)
    if lang in (None, "", DEFAULT_KEY):
        shot.master_caption = text
    else:
        if not steam_codes.is_valid(lang):
            raise ValueError(f"Unbekannte Sprache: {lang!r}")
        shot.captions[lang] = text
    save_manifest(idir, manifest)
    return shot


def reorder_screenshots(idir: Path, ordered_ids: list[int]) -> list[Screenshot]:
    """Setzt die Reihenfolge anhand der uebergebenen id-Liste."""
    manifest = load_manifest(idir)
    known = {s.id for s in manifest.screenshots}
    if set(ordered_ids) != known:
        raise ValueError(f"ordered_ids {ordered_ids} != vorhandene {sorted(known)}")
    pos = {sid: i for i, sid in enumerate(ordered_ids)}
    for s in manifest.screenshots:
        s.order = pos[s.id]
    manifest.screenshots.sort(key=lambda s: s.order)
    save_manifest(idir, manifest)
    return manifest.screenshots


def delete_screenshot(idir: Path, shot_id: int) -> bool:
    manifest = load_manifest(idir)
    shot = next((s for s in manifest.screenshots if s.id == shot_id), None)
    if shot is None:
        return False
    sdir = _assets_dir(idir) / "screenshots"
    for entry in [shot.default, *shot.localized.values()]:
        if entry is not None:
            _unlink_quiet(sdir / entry.filename)
    manifest.screenshots = [s for s in manifest.screenshots if s.id != shot_id]
    save_manifest(idir, manifest)
    return True


def _find_shot(manifest: AssetManifest, shot_id: int) -> Screenshot:
    shot = next((s for s in manifest.screenshots if s.id == shot_id), None)
    if shot is None:
        raise ValueError(f"Screenshot {shot_id} nicht gefunden")
    return shot


# --- Status / Uebersicht -----------------------------------------------------

def status(idir: Path, active_langs: list[str]) -> dict:
    """Fuellstand-Uebersicht fuer das UI: pro Slot was vorhanden ist + Warnungen.

    Pro lokalisierbarem Slot wird je aktiver Sprache aufgeloest, ob ein
    Override existiert ('override'), auf default zurueckgefallen wird
    ('default') oder gar nichts da ist ('missing').
    """
    manifest = load_manifest(idir, item_id=_item_id(idir))
    # Anders als beim Text-Modell ist hier NICHT die Master-Sprache der Anker,
    # sondern das sprachneutrale 'default'-Bild. Jede aktive Sprache (auch
    # German) kann einen eigenen Override haben oder auf default zurueckfallen.
    langs = list(active_langs) if active_langs else []

    slot_rows = []
    for slot in SLOTS:
        st = manifest.slots.get(slot.key) or SlotState()
        row: dict[str, Any] = {
            "key": slot.key, "label": slot.label, "category": slot.category,
            "localizable": slot.localizable, "required": slot.required,
            "target": f"{slot.width}x{slot.height}",
            "has_default": st.default is not None,
            "default_size_ok": st.default.size_ok if st.default else None,
        }
        if slot.localizable and langs:
            per_lang = {}
            for lang in langs:
                if lang in st.localized:
                    per_lang[lang] = "override"
                elif st.default is not None:
                    per_lang[lang] = "default"
                else:
                    per_lang[lang] = "missing"
            row["per_lang"] = per_lang
            row["n_override"] = sum(1 for v in per_lang.values() if v == "override")
        slot_rows.append(row)

    return {
        "slots": slot_rows,
        "screenshots": {
            "count": len(manifest.screenshots),
            "items": [
                {
                    "id": s.id, "order": s.order,
                    "has_default": s.default is not None,
                    "size_ok": s.default.size_ok if s.default else None,
                    "n_overrides": len(s.localized),
                    "master_caption": s.master_caption,
                    "n_captions": len(s.captions),
                }
                for s in sorted(manifest.screenshots, key=lambda s: s.order)
            ],
        },
        "updated_at": manifest.updated_at,
    }


# --- Export ------------------------------------------------------------------

def export_zip(idir: Path, lang: str) -> bytes:
    """Baut ein ZIP mit allen fuer `lang` gueltigen Assets + Screenshots.

    Dateinamen sind nach Slot benannt (z.B. header_capsule.png), Screenshots
    werden in Reihenfolge durchnummeriert. Ein _README.txt listet das Mapping
    und etwaige fehlende Pflicht-Slots. Sprachneutrale/auf-default-zurueck-
    fallende Slots landen mit ihrem Standard-Bild im ZIP.
    """
    if not steam_codes.is_valid(lang):
        raise ValueError(f"Unbekannte Sprache: {lang!r}")
    manifest = load_manifest(idir, item_id=_item_id(idir))
    buf = io.BytesIO()
    lines = [f"Sid Asset-Export — Sprache: {lang}", ""]
    missing_required: list[str] = []

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for slot in SLOTS:
            p = resolve_asset(idir, slot.key, lang)
            if p is None:
                if slot.required:
                    missing_required.append(slot.key)
                    lines.append(f"[FEHLT] {slot.label} ({slot.key}) — Pflicht-Slot")
                else:
                    lines.append(f"[leer]  {slot.label} ({slot.key})")
                continue
            arcname = f"{slot.key}{p.suffix}"
            zf.write(p, arcname)
            # Kam das Bild aus einem Override oder dem Default?
            st = manifest.slots.get(slot.key) or SlotState()
            src = "override" if lang in st.localized else "default"
            lines.append(f"[ok/{src}] {slot.label}: {arcname}")

        shots = sorted(manifest.screenshots, key=lambda s: s.order)
        for i, shot in enumerate(shots, start=1):
            entry = shot.localized.get(lang) or shot.default
            if entry is None:
                lines.append(f"[FEHLT] Screenshot {shot.id}")
                continue
            src_path = _assets_dir(idir) / "screenshots" / entry.filename
            if not src_path.exists():
                lines.append(f"[FEHLT] Screenshot {shot.id} (Datei weg)")
                continue
            ext = src_path.suffix
            arcname = f"screenshots/{i:02d}{ext}"
            zf.write(src_path, arcname)
            src = "override" if lang in shot.localized else "default"
            caption = shot.captions.get(lang) or shot.master_caption
            lines.append(f"[ok/{src}] Screenshot {i:02d}: {arcname}"
                         + (f"  | Caption: {caption}" if caption else ""))

        if missing_required:
            lines.insert(1, f"ACHTUNG: {len(missing_required)} Pflicht-Slot(s) fehlen: "
                            + ", ".join(missing_required))
        zf.writestr("_README.txt", "\n".join(lines) + "\n")

    return buf.getvalue()


# --- kleine Helfer -----------------------------------------------------------

def _item_id(idir: Path) -> str:
    mp = idir / "meta.json"
    if mp.exists():
        try:
            return str(storage.read_json(mp).get("item_id", ""))
        except Exception:  # noqa: BLE001
            return ""
    return ""


def _write_bytes_atomic(path: Path, data: bytes) -> None:
    """Atomic-Write fuer Binaerdaten (analog storage.write_json_atomic)."""
    import os
    import tempfile
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", suffix=path.suffix, dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _remove_other_ext(folder: Path, stem: str, *, keep_ext: str) -> None:
    """Loescht <stem>.<x> fuer alle x != keep_ext (Endungs-Wechsel beim Re-Upload)."""
    if not folder.exists():
        return
    for p in folder.glob(f"{stem}.*"):
        if p.suffix.lstrip(".").lower() != keep_ext:
            _unlink_quiet(p)


def _unlink_quiet(p: Path) -> None:
    try:
        p.unlink()
    except OSError:
        pass
