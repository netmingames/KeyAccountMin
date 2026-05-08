"""Pass 2 (Lisbeth NT-549): robuster <SID_OUTPUT>-Parser + claude.exe-Resolution."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import translator  # noqa: E402
from core.translator import (
    ClaudeCliTranslator,
    TranslationError,
    _parse_response,
    _resolve_claude_exe,
)


# ---------------------------------------------------------------------------
# _parse_response
# ---------------------------------------------------------------------------

def test_parse_simple_block() -> None:
    text = """Hier kommt mein Output:
<SID_OUTPUT>
{"about": "An English about text", "short_description": "Short en"}
</SID_OUTPUT>"""
    out = _parse_response(text, ["about", "short_description"])
    assert out == {"about": "An English about text", "short_description": "Short en"}


def test_parse_picks_last_block_when_multiple() -> None:
    """Wenn der LLM zwei <SID_OUTPUT>-Bloecke ausgibt (denkt zwischendurch nach),
    nehmen wir den LETZTEN — der ist die finale Antwort."""
    text = """<SID_OUTPUT>
{"about": "draft"}
</SID_OUTPUT>
Hmm, das war noch nicht ideal. Hier korrigiert:
<SID_OUTPUT>
{"about": "final"}
</SID_OUTPUT>"""
    out = _parse_response(text, ["about"])
    assert out == {"about": "final"}


def test_parse_handles_closing_brace_inside_string() -> None:
    """Pass-2-Kernfall: ``}`` im uebersetzten String darf den Parser nicht
    abschneiden. Die alte Regex ``\\{.*?\\}`` haette beim ersten ``}`` aufgehoert."""
    text = """<SID_OUTPUT>
{"about": "Style: { color: red; }", "short_description": "Kurz"}
</SID_OUTPUT>"""
    out = _parse_response(text, ["about", "short_description"])
    assert out["about"] == "Style: { color: red; }"
    assert out["short_description"] == "Kurz"


def test_parse_raises_when_field_missing() -> None:
    """Wenn der LLM ein erwartetes Feld weglaesst, ist das ein Fehler — kein
    silent partial update."""
    text = """<SID_OUTPUT>
{"about": "Only this one"}
</SID_OUTPUT>"""
    with pytest.raises(TranslationError) as excinfo:
        _parse_response(text, ["about", "short_description"])
    assert "short_description" in str(excinfo.value)


def test_parse_raises_when_no_open_marker() -> None:
    text = "Sorry, ich konnte nicht uebersetzen."
    with pytest.raises(TranslationError):
        _parse_response(text, ["about"])


def test_parse_raises_when_no_close_marker() -> None:
    text = "<SID_OUTPUT>\n{\"about\": \"abgeschnitten\""
    with pytest.raises(TranslationError):
        _parse_response(text, ["about"])


def test_parse_ignores_hallucinated_fields() -> None:
    """Felder, die nicht angefragt wurden, werden ignoriert (Halluzinations-Schutz)."""
    text = """<SID_OUTPUT>
{"about": "About text", "short_description": "Short", "extra_hallucination": "Spam"}
</SID_OUTPUT>"""
    out = _parse_response(text, ["about", "short_description"])
    assert "extra_hallucination" not in out
    assert out == {"about": "About text", "short_description": "Short"}


def test_parse_string_coerces_non_string_values() -> None:
    """Wenn Claude versehentlich int/null sendet, normalisieren wir zu str."""
    text = """<SID_OUTPUT>
{"about": null, "short_description": 42}
</SID_OUTPUT>"""
    out = _parse_response(text, ["about", "short_description"])
    assert out["about"] == ""
    assert out["short_description"] == "42"


# ---------------------------------------------------------------------------
# _resolve_claude_exe
# ---------------------------------------------------------------------------

def test_resolve_claude_exe_uses_env_var(monkeypatch) -> None:
    """SID_CLAUDE_EXE Override gewinnt vor PATH und Default."""
    monkeypatch.setenv("SID_CLAUDE_EXE", r"D:\test\custom\claude.exe")
    assert _resolve_claude_exe() == r"D:\test\custom\claude.exe"


def test_resolve_claude_exe_falls_back_to_path(monkeypatch) -> None:
    """Ohne Env-Var faellt's auf shutil.which zurueck, dann auf Default."""
    monkeypatch.delenv("SID_CLAUDE_EXE", raising=False)

    # PATH-Lookup erzwingen: shutil.which faken, sodass es einen Pfad findet
    fake_path = r"C:\fake\bin\claude.exe"
    monkeypatch.setattr("core.translator.shutil.which", lambda name: fake_path if name in ("claude", "claude.exe", "claude.cmd") else None)
    assert _resolve_claude_exe() == fake_path


def test_resolve_claude_exe_default_when_not_in_path(monkeypatch) -> None:
    monkeypatch.delenv("SID_CLAUDE_EXE", raising=False)
    monkeypatch.setattr("core.translator.shutil.which", lambda name: None)
    assert _resolve_claude_exe() == translator.CLAUDE_EXE_DEFAULT


def test_translator_constructor_uses_resolver(monkeypatch) -> None:
    monkeypatch.setenv("SID_CLAUDE_EXE", r"D:\custom.exe")
    t = ClaudeCliTranslator()
    assert t.exe == r"D:\custom.exe"


def test_translator_constructor_explicit_path_wins() -> None:
    """Explicit Konstruktor-Argument schlaegt die Resolver-Logik."""
    t = ClaudeCliTranslator(exe_path=r"D:\force.exe")
    assert t.exe == r"D:\force.exe"
