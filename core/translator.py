"""Translator-Engine fuer Sid (NT-549).

Liefert eine abstrakte Translator-Schnittstelle und zwei Implementierungen:
- ClaudeCliTranslator: ruft `claude.exe -p` als Subprocess (Abo-OAuth)
- MockTranslator: deterministisch, fuer Tests und UI-Smoke

Engine-Auswahl ueber Env-Var SID_TRANSLATOR:
- nicht gesetzt oder "claude": ClaudeCliTranslator
- "mock": MockTranslator (gibt Praefix-markierten DE-Text zurueck)

Architectural-Hinweis: claude.exe -p braucht eine eigene OAuth-Session, die
NICHT automatisch von der Live-Claude-Code-Session vererbt wird. Vor dem
ersten Einsatz: einmalig `claude /login` interaktiv ausfuehren. Siehe
D:/Claude/agent-state/CREDENTIALS.md Punkt 5.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol

from . import edit_ops, glossary as glossary_mod, schema, steam_codes, storage

CLAUDE_EXE = r"C:\Users\netmin_m\AppData\Roaming\Claude\claude-code\2.1.128\claude.exe"
DEFAULT_TIMEOUT = 120  # pro Prompt-Aufruf, in Sekunden


class TranslationError(RuntimeError):
    """Tritt auf wenn die Engine nicht antwortet, nicht eingeloggt ist,
    timed out, oder kaputten Output liefert."""


@dataclass
class TranslationResult:
    lang: str
    fields_translated: dict[str, str]   # field -> uebersetzter Text
    fields_skipped: list[str]           # geschuetzt durch manually_edited
    duration_seconds: float
    engine: str


class Translator(Protocol):
    name: str

    def translate_lang(
        self,
        master_fields: dict[str, str],
        lang_code: str,
        lang_display: str,
        glossary_block: str,
        style_block: str,
    ) -> dict[str, str]:
        """Uebersetzt alle Master-Felder in Zielsprache. Returns {field: value}."""
        ...


# --- Mock --------------------------------------------------------------------

class MockTranslator:
    name = "mock"

    def translate_lang(
        self,
        master_fields: dict[str, str],
        lang_code: str,
        lang_display: str,
        glossary_block: str,
        style_block: str,
    ) -> dict[str, str]:
        out = {}
        prefix = f"[{lang_code.upper()}] "
        for field, value in master_fields.items():
            out[field] = prefix + value if value else ""
        return out


# --- Claude CLI ---------------------------------------------------------------

class ClaudeCliTranslator:
    name = "claude-cli"

    def __init__(self, exe_path: str = CLAUDE_EXE, timeout: int = DEFAULT_TIMEOUT):
        self.exe = exe_path
        self.timeout = timeout

    def translate_lang(
        self,
        master_fields: dict[str, str],
        lang_code: str,
        lang_display: str,
        glossary_block: str,
        style_block: str,
    ) -> dict[str, str]:
        prompt = _build_prompt(master_fields, lang_code, lang_display, glossary_block, style_block)
        try:
            r = subprocess.run(
                [self.exe, "-p", "--disable-slash-commands", prompt],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            raise TranslationError(
                f"Claude-CLI Timeout nach {self.timeout}s (Sprache {lang_code})"
            )
        except FileNotFoundError:
            raise TranslationError(f"Claude-CLI nicht gefunden: {self.exe}")

        out = (r.stdout or "").strip()
        err = (r.stderr or "").strip()
        if "Not logged in" in out or "Not logged in" in err:
            raise TranslationError(
                "Claude-CLI ist nicht eingeloggt. Einmalig in einer normalen "
                "Shell ausfuehren: claude /login. Siehe "
                "D:/Claude/agent-state/CREDENTIALS.md Punkt 5."
            )
        if r.returncode != 0:
            raise TranslationError(
                f"Claude-CLI Exit {r.returncode}: {err[:300] or out[:300]}"
            )
        return _parse_response(out, list(master_fields.keys()))


# --- Prompt-Bau --------------------------------------------------------------

def _build_prompt(
    master_fields: dict[str, str],
    lang_code: str,
    lang_display: str,
    glossary_block: str,
    style_block: str,
) -> str:
    """Erstellt den Prompt fuer eine sprachweise Uebersetzung.

    Output-Vertrag: ein JSON-Block mit den gleichen Feldnamen wie input,
    eingeschlossen in <SID_OUTPUT>...</SID_OUTPUT> Markierungen, damit das
    Parsing robust ist gegen LLM-Praeambeln.
    """
    payload = {k: v for k, v in master_fields.items() if v}  # leere Felder skippen
    fields_json = json.dumps(payload, ensure_ascii=False, indent=2)

    return f"""Du uebersetzt Marketing-Texte fuer eine Steam-Spielbeschreibung von Deutsch nach {lang_display}.

REGELN:
- Stil: Marketing-Tone, kurze Saetze, aktiv. Keine Hyperlatives uebersetzen wo das DE neutral ist.
- Laenge: ungefaehr gleich lang wie Original (Steam-UI hat Platzbeschraenkungen).
- Keine Erklaerungen, keine Anmerkungen, keine Begruessung.
- Kein Markdown (kein **, kein _, keine Listenstriche aenderung) — nur die Inhalts-Uebersetzung.
- Wenn ein Feld nur Sysreqs-Text wie 'Pentium 1 GHz' ist: in der Zielsprache analog ('Pentium 1 GHz' bleibt typischerweise gleich).

GLOSSAR:
{glossary_block or "(kein item-spezifisches Glossar)"}

STIL-HINWEISE:
{style_block or "(keine sprachspezifischen Stil-Hinweise)"}

QUELLE (Deutsch):
{fields_json}

OUTPUT-FORMAT — sehr wichtig:
Gib ausschliesslich einen JSON-Block zurueck, eingeschlossen in <SID_OUTPUT>-Marker:

<SID_OUTPUT>
{{
  "field_name_1": "uebersetzter Wert ...",
  "field_name_2": "uebersetzter Wert ..."
}}
</SID_OUTPUT>

Keine zusaetzlichen Texte vor oder nach den Markern."""


_OUTPUT_RE = re.compile(r"<SID_OUTPUT>\s*(\{.*?\})\s*</SID_OUTPUT>", re.DOTALL)


def _parse_response(text: str, expected_fields: list[str]) -> dict[str, str]:
    """Extrahiert das JSON aus <SID_OUTPUT>-Markern."""
    m = _OUTPUT_RE.search(text)
    if not m:
        raise TranslationError(
            f"Kein <SID_OUTPUT>-Block in der Claude-Antwort gefunden. "
            f"Antwort-Anfang: {text[:300]!r}"
        )
    raw = m.group(1)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise TranslationError(f"JSON-Decode-Fehler im SID_OUTPUT: {e}; raw={raw[:300]!r}")
    if not isinstance(data, dict):
        raise TranslationError(f"SID_OUTPUT ist kein Objekt: {type(data).__name__}")
    # Nur erwartete Felder uebernehmen, andere sind Halluzinationen
    return {k: str(v) for k, v in data.items() if k in expected_fields}


# --- High-level Operation ----------------------------------------------------

def get_translator() -> Translator:
    name = (os.environ.get("SID_TRANSLATOR", "claude") or "claude").lower()
    if name == "mock":
        return MockTranslator()
    if name in ("claude", "claude-cli"):
        return ClaudeCliTranslator()
    raise ValueError(f"Unbekannte Translator-Engine: {name}")


def translate_item_lang(
    idir: Path,
    lang: str,
    *,
    fields: Iterable[str] | None = None,
    translator: Translator | None = None,
) -> TranslationResult:
    """Uebersetzt alle Master-Felder eines Items in eine Zielsprache.

    fields: optional eingrenzen auf bestimmte Feldnamen.
    translator: optional eigene Engine (sonst aus SID_TRANSLATOR-Env).

    Verhalten:
    - Manuell editierte Translation-Felder werden uebersprungen (manually_edited=True).
    - Andere Felder werden via via_translation_engine=True geupdatet — kein
      manually_edited-Flag wird gesetzt, source_hash auf aktuellen Master,
      stale=False.
    - Leere Master-Felder werden uebersprungen.
    """
    if not steam_codes.is_valid(lang):
        raise ValueError(f"Unbekannte Sprache: {lang}")
    meta = edit_ops.read_meta(idir)
    if lang == meta.master_lang:
        raise ValueError(f"Master-Sprache {lang} kann nicht uebersetzt werden")

    master = edit_ops.read_master(idir)
    tpath = storage.translation_path(idir, lang)
    if tpath.exists():
        existing_t = edit_ops.read_translation(idir, lang)
    else:
        existing_t = schema.TranslationDocument(item_id=meta.item_id, lang=lang, fields={})

    field_filter = set(fields) if fields else None

    candidates: dict[str, str] = {}
    skipped: list[str] = []
    for f, value in master.fields.items():
        if field_filter and f not in field_filter:
            continue
        if not value:
            continue
        existing_field = existing_t.fields.get(f)
        if existing_field and existing_field.manually_edited:
            skipped.append(f)
            continue
        candidates[f] = value

    if not candidates:
        return TranslationResult(
            lang=lang,
            fields_translated={},
            fields_skipped=skipped,
            duration_seconds=0.0,
            engine="(keine Felder)",
        )

    g = glossary_mod.load(idir)
    glossary_block = glossary_mod.to_prompt_block(g)
    style_block = _read_style(idir, lang)

    tx = translator or get_translator()
    lang_display = steam_codes.get(lang).display

    t0 = time.monotonic()
    translated = tx.translate_lang(
        master_fields=candidates,
        lang_code=lang,
        lang_display=lang_display,
        glossary_block=glossary_block,
        style_block=style_block,
    )
    duration = time.monotonic() - t0

    # Werte schreiben via edit_ops (mit via_translation_engine=True)
    for field, value in translated.items():
        edit_ops.update_translation_field(
            idir, lang, field, value, via_translation_engine=True
        )

    return TranslationResult(
        lang=lang,
        fields_translated=translated,
        fields_skipped=skipped,
        duration_seconds=duration,
        engine=tx.name,
    )


def _read_style(idir: Path, lang: str) -> str:
    style_dir = idir / "style"
    style_file = style_dir / f"{lang}.md"
    if not style_file.exists():
        return ""
    try:
        return style_file.read_text(encoding="utf-8")
    except Exception:
        return ""
