"""NT-549/550/551 Pass 10 (Lisbeth 09:01/08:57/09:24 MEDIUM/LOW FUNCTIONAL).

Drei Findings:
1) core/ea_exporter.py:47-49 — _values_for_lang ruft im master-Branch
   edit_ops.read_master() ohne Guard. Korrupte master_*.json -> 500 statt
   kontrolliertem corrupt-Flag wie im Translations-Pfad.
2) app.py:510-521 (translate-stream) und app.py:438-456 (translate-all) —
   Exception-Liste deckt nur TranslationError + ValueError ab. Eine korrupte
   translations/<lang>.json kann ValidationError/JSONDecodeError/OSError/
   UnicodeDecodeError aus read_master/read_translation werfen und den Stream
   bzw. Batch killen.
3) (sid.js LOW — JS-only, hier nicht testbar; via Live-Smoke abgedeckt.)
"""

from __future__ import annotations

import json as _json
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


def _write_meta(idir: Path, *, active_languages, early_access=True) -> None:
    (idir / "meta.json").write_text(
        _json.dumps({
            "platform": "steam",
            "item_id": "1141975",
            "name": "Passage 5",
            "active_languages": active_languages,
            "early_access": early_access,
            "schema_version": 1,
            "master_lang": "german",
        }),
        encoding="utf-8",
    )


def _write_master_valid(idir: Path) -> None:
    (idir / "master_de.json").write_text(
        _json.dumps({
            "schema_version": 1,
            "item_id": "1141975",
            "lang": "german",
            "fields": {
                "ea_why": "Weil Feedback wichtig ist.",
                "ea_duration": "12 Monate.",
            },
            "updated_at": "2026-05-12T09:30:00",
        }),
        encoding="utf-8",
    )


# --- Finding 1: master_*.json read mit Guard ------------------------------


def test_ea_status_marks_master_corrupt_when_master_json_broken(
    app_with_data, tmp_path: Path,
) -> None:
    """Eine kaputte master_*.json wirft jetzt KEINE 500 mehr — der Master
    wird wie korrupte Translations behandelt (corrupt=True, filled=0)."""
    from fastapi.testclient import TestClient

    idir = tmp_path / "steam" / "1141975_passage5"
    idir.mkdir(parents=True)
    _write_meta(idir, active_languages=["german", "english"])
    # master kaputt
    (idir / "master_de.json").write_text("{ kaputtes json", encoding="utf-8")
    (idir / "translations").mkdir(parents=True, exist_ok=True)

    client = TestClient(app_with_data.app)
    resp = client.get("/api/items/steam/1141975/ea-status")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    langs = {l["code"]: l for l in body["languages"]}
    assert langs["german"]["is_master"] is True
    assert langs["german"]["corrupt"] is True
    assert langs["german"]["filled"] == 0


def test_ea_status_marks_master_corrupt_on_invalid_utf8(
    app_with_data, tmp_path: Path,
) -> None:
    from fastapi.testclient import TestClient

    idir = tmp_path / "steam" / "1141975_passage5"
    idir.mkdir(parents=True)
    _write_meta(idir, active_languages=["german"])
    (idir / "master_de.json").write_bytes(b'{"\xff\xfe": "BROKEN"}')

    client = TestClient(app_with_data.app)
    resp = client.get("/api/items/steam/1141975/ea-status")
    assert resp.status_code == 200
    body = resp.json()
    g = next(l for l in body["languages"] if l["code"] == "german")
    assert g["corrupt"] is True


def test_ea_export_text_master_corrupt_returns_warning_block(
    app_with_data, tmp_path: Path,
) -> None:
    """Plain-Text-Export der Master-Sprache mit kaputter master_*.json:
    Warning-Block + leere Antworten statt 500."""
    from fastapi.testclient import TestClient

    idir = tmp_path / "steam" / "1141975_passage5"
    idir.mkdir(parents=True)
    _write_meta(idir, active_languages=["german"])
    (idir / "master_de.json").write_text("{ broken", encoding="utf-8")

    client = TestClient(app_with_data.app)
    resp = client.get("/api/items/steam/1141975/ea-export/german.txt")
    assert resp.status_code == 200
    text = resp.text
    # Der Helper schreibt "translations/<lang>.json" in die Warnung — fuer den
    # Master ist das nicht ideal, aber der Hauptpunkt ist: kein 500, Warn-Block
    # vorhanden, alle Q&A-Bloecke leer.
    assert "WARNUNG" in text
    assert "noch nicht ausgefuellt" in text


def test_ea_export_zip_master_corrupt_does_not_500(
    app_with_data, tmp_path: Path,
) -> None:
    """ZIP-Export mit kaputter master_*.json laeuft durch — alle Sprachen
    bekommen Warn-Blocks, kein 500."""
    import io
    import zipfile

    from fastapi.testclient import TestClient

    idir = tmp_path / "steam" / "1141975_passage5"
    idir.mkdir(parents=True)
    _write_meta(idir, active_languages=["german", "english"])
    (idir / "master_de.json").write_text("{ kaputt", encoding="utf-8")
    (idir / "translations").mkdir(parents=True, exist_ok=True)

    client = TestClient(app_with_data.app)
    resp = client.get("/api/items/steam/1141975/ea-export.zip")
    assert resp.status_code == 200
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        names = zf.namelist()
        assert "README.md" in names
        de_name = next(n for n in names if "german" in n.lower() or n.endswith("_de.txt"))
        de_text = zf.read(de_name).decode("utf-8")
        assert "WARNUNG" in de_text


# --- Finding 2: translate-all / translate-stream Exception-Set -----------


def test_translate_all_handles_corrupt_translation_file(
    app_with_data, tmp_path: Path, monkeypatch,
) -> None:
    """Eine kaputte translations/<lang>.json darf nicht 500 werfen — die
    Sprache wird als failed gemeldet, der Rest laeuft weiter."""
    from fastapi.testclient import TestClient
    import json as _json2

    idir = tmp_path / "steam" / "1141975_passage5"
    idir.mkdir(parents=True)
    _write_meta(idir, active_languages=["german", "english", "french"])
    _write_master_valid(idir)
    (idir / "translations").mkdir(parents=True, exist_ok=True)
    # english: valide; french: kaputt
    (idir / "translations" / "english.json").write_text(
        _json2.dumps({
            "schema_version": 1,
            "item_id": "1141975",
            "lang": "english",
            "fields": {},
            "updated_at": "2026-05-12T09:30:00",
        }),
        encoding="utf-8",
    )
    (idir / "translations" / "french.json").write_text("{ broken", encoding="utf-8")

    # Mock-Translator nutzen, damit kein Claude-CLI angefasst wird.
    client = TestClient(app_with_data.app)
    resp = client.post(
        "/api/items/steam/1141975/translate-all",
        json={"engine": "mock"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    by_lang = {r["lang"]: r for r in body["results"]}
    # english darf nicht 500en, french wird als failed gemeldet
    assert by_lang["french"]["ok"] is False
    # english darf durch, weil JSON ok ist
    assert "english" in by_lang


def test_translate_stream_handles_corrupt_translation_file(
    app_with_data, tmp_path: Path,
) -> None:
    """SSE-Stream: kaputte translations/<lang>.json -> lang_done mit ok=False
    fuer diese Sprache, Stream laeuft weiter und schliesst mit done."""
    from fastapi.testclient import TestClient
    import json as _json2

    idir = tmp_path / "steam" / "1141975_passage5"
    idir.mkdir(parents=True)
    _write_meta(idir, active_languages=["german", "english", "french"])
    _write_master_valid(idir)
    (idir / "translations").mkdir(parents=True, exist_ok=True)
    (idir / "translations" / "english.json").write_text(
        _json2.dumps({
            "schema_version": 1,
            "item_id": "1141975",
            "lang": "english",
            "fields": {},
            "updated_at": "2026-05-12T09:30:00",
        }),
        encoding="utf-8",
    )
    (idir / "translations" / "french.json").write_text("{ broken", encoding="utf-8")

    client = TestClient(app_with_data.app)
    with client.stream(
        "GET", "/api/items/steam/1141975/translate-stream?engine=mock",
    ) as resp:
        assert resp.status_code == 200
        events = list(resp.iter_lines())
    # done-Event muss am Ende auftauchen — also kein Mid-Batch-Abbruch
    joined = "\n".join(events)
    assert "event: done" in joined
    # french wird als failed gemeldet (lang_done mit ok=false)
    assert '"lang": "french"' in joined or '"lang":"french"' in joined
    # ein Fehler muss auftreten — schauen ob ein lang_done mit ok=false fuer
    # french drin steht
    fr_failed = False
    for i, line in enumerate(events):
        if "french" in line and ("ok\": false" in line or "ok\":false" in line):
            fr_failed = True
            break
    assert fr_failed, f"french failed-Event nicht gefunden in:\n{joined}"
