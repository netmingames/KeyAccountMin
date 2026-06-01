"""NT-565: _stale_or_empty_fields() faengt korrupte Translation ab (kein 500)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as app_mod  # noqa: E402


def _make(tmp_path: Path, french_content: str) -> Path:
    idir = tmp_path / "steam" / "1141975_p5"
    (idir / "translations").mkdir(parents=True)
    (idir / "meta.json").write_text(
        '{"schema_version":1,"item_id":"1141975","platform":"steam","name":"P5",'
        '"master_lang":"german","active_languages":["german","french"]}', encoding="utf-8")
    (idir / "master_de.json").write_text(
        '{"schema_version":1,"item_id":"1141975","lang":"de",'
        '"fields":{"short_description":"x","about":"y"}}', encoding="utf-8")
    (idir / "translations" / "french.json").write_text(french_content, encoding="utf-8")
    return idir


def test_corrupt_translation_raises_for_per_lang_failure(tmp_path: Path) -> None:
    # NT-563 Pass 8: korrupte Translation wirft -> api_translate_stream meldet das
    # als per-Sprache-Fehler (kein 500, kein stiller No-op).
    idir = _make(tmp_path, "{ kaputtes json")
    raised = False
    try:
        app_mod._stale_or_empty_fields(idir, "french")
    except Exception:
        raised = True
    assert raised


def test_missing_translation_returns_all_content(tmp_path: Path) -> None:
    idir = _make(tmp_path, "{}")
    # franzoesische Datei loeschen -> kein File -> alle Content-Felder faellig
    (idir / "translations" / "french.json").unlink()
    fields = app_mod._stale_or_empty_fields(idir, "french")
    assert set(fields) == {"short_description", "about"}
