"""NT-550 Pass 13 (Lisbeth 10:23 MEDIUM FUNCTIONAL).

translate-stream macht den ganzen Bulk-Batch synchron — Closing-Modal auf der
Client-Seite stoppte den Server frueher nicht. Erwartet: ``request.is_disconnected()``
zwischen den Sprachen prueft, ob der Client noch da ist; falls nicht, abort
plus ``cancelled`` event statt ``done``.
"""

from __future__ import annotations

import json
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


def _seed_three_lang_item(tmp_path: Path) -> Path:
    idir = tmp_path / "steam" / "1141975_passage5"
    idir.mkdir(parents=True)
    (idir / "meta.json").write_text(
        json.dumps({
            "platform": "steam",
            "item_id": "1141975",
            "name": "Passage 5",
            "active_languages": ["english", "french", "italian"],
            "early_access": False,
            "schema_version": 1,
            "master_lang": "german",
        }),
        encoding="utf-8",
    )
    (idir / "master_de.json").write_text(
        json.dumps({
            "schema_version": 1,
            "item_id": "1141975",
            "lang": "german",
            "fields": {
                "about": "Eine deutsche Beschreibung.",
            },
            "updated_at": "2026-05-12T10:00:00",
        }),
        encoding="utf-8",
    )
    return idir


def _collect_events(resp) -> list[str]:
    events: list[str] = []
    for line in resp.iter_lines():
        text = line if isinstance(line, str) else line.decode("utf-8", errors="replace")
        if text.startswith("event:"):
            events.append(text.split(":", 1)[1].strip())
    return events


def test_translate_stream_emits_cancelled_after_disconnect(
    app_with_data, tmp_path: Path, monkeypatch,
) -> None:
    """Sobald `is_disconnected()` True meldet, bricht der Stream ab; es
    erscheint ein `cancelled`-Event, kein `done`."""
    from fastapi.testclient import TestClient

    _seed_three_lang_item(tmp_path)

    # is_disconnected: erste call False, ab zweitem True -> Cancel nach erster Sprache
    counter = {"calls": 0}

    async def fake_is_disconnected(self):
        counter["calls"] += 1
        return counter["calls"] > 1

    monkeypatch.setattr(
        "starlette.requests.Request.is_disconnected",
        fake_is_disconnected,
    )

    client = TestClient(app_with_data.app, raise_server_exceptions=False)
    with client.stream(
        "GET",
        "/api/items/steam/1141975/translate-stream?engine=mock",
    ) as resp:
        assert resp.status_code == 200
        events = _collect_events(resp)

    assert "start" in events
    assert "cancelled" in events
    assert "done" not in events
    # Hoechstens eine Sprache komplett durch, dann abort
    assert events.count("lang_done") <= 1


def test_translate_stream_emits_done_when_not_disconnected(
    app_with_data, tmp_path: Path, monkeypatch,
) -> None:
    """Negativ-Kontrolle: Client bleibt verbunden -> normaler done-Pfad."""
    from fastapi.testclient import TestClient

    _seed_three_lang_item(tmp_path)

    async def fake_is_disconnected(self):
        return False

    monkeypatch.setattr(
        "starlette.requests.Request.is_disconnected",
        fake_is_disconnected,
    )

    client = TestClient(app_with_data.app, raise_server_exceptions=False)
    with client.stream(
        "GET",
        "/api/items/steam/1141975/translate-stream?engine=mock",
    ) as resp:
        assert resp.status_code == 200
        events = _collect_events(resp)

    assert "done" in events
    assert "cancelled" not in events
    assert events.count("lang_done") == 3
