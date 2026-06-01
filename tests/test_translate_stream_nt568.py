"""NT-568: translate-stream akzeptiert Sprach-Subset (`langs`) + `only_stale`."""
from __future__ import annotations

import sys
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
        '{"schema_version":1,"item_id":"1141975","platform":"steam","name":"P5",'
        '"master_lang":"german","active_languages":["german","french","english"]}', encoding="utf-8")
    (idir / "master_de.json").write_text(
        '{"schema_version":1,"item_id":"1141975","lang":"de","fields":{"short_description":"x"}}', encoding="utf-8")
    return TestClient(app_mod.app), idir


def test_stream_langs_subset(client_and_item):
    client, _ = client_and_item
    body = client.get("/api/items/steam/1141975/translate-stream?engine=mock&langs=french").text
    assert "event: lang_start" in body
    assert "french" in body
    assert "english" not in body  # nicht im Subset -> kommt nirgends vor


def test_stream_invalid_langs_400(client_and_item):
    client, _ = client_and_item
    r = client.get("/api/items/steam/1141975/translate-stream?engine=mock&langs=klingon")
    assert r.status_code == 400


def test_stream_only_stale_skips_fresh(client_and_item):
    client, _ = client_and_item
    # 1. Lauf: alles neu -> french short_description gefuellt + nicht stale
    client.get("/api/items/steam/1141975/translate-stream?engine=mock&langs=french&only_stale=false")
    # 2. Lauf only_stale: french hat nichts Veraltetes/Leeres mehr -> 0 Felder
    body = client.get("/api/items/steam/1141975/translate-stream?engine=mock&langs=french&only_stale=true").text
    assert '"fields_translated": []' in body


def test_stream_default_is_all_active(client_and_item):
    client, _ = client_and_item
    # ohne langs-Param: alle aktiven Zielsprachen (french + english, ohne master)
    body = client.get("/api/items/steam/1141975/translate-stream?engine=mock").text
    assert "french" in body and "english" in body
    assert "german" not in body  # Master wird nie uebersetzt
