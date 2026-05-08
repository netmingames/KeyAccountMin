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
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol

from . import edit_ops, glossary as glossary_mod, schema, steam_codes, storage

DEFAULT_TIMEOUT = 120  # pro Prompt-Aufruf, in Sekunden

# Marker-Wert fuer "nichts gefunden" — der ClaudeCliTranslator wirft beim
# Subprocess-Aufruf eine klare TranslationError statt eines kryptischen
# FileNotFoundError. Tests pruefen darauf.
CLAUDE_EXE_DEFAULT = ""


def _version_key(name: str) -> tuple:
    """Sortier-Key fuer Versions-Ordner: zerlegt in numerische Segmente.

    Beispiele:
        "2.10.0" -> (1, 2, 10, 0)
        "2.9.0"  -> (1, 2, 9, 0)
        "2.1.128" -> (1, 2, 1, 128)
        "draft"  -> (0, "draft")  # nicht-numerische Namen sortieren niedriger

    Lisbeth NT-548 15:33: lexikalisches Sortieren brach bei 2.10.0 < 2.9.0.
    Numerische Segment-Sortierung ist robust fuer Semver-artige Tags.
    """
    parts: list = []
    for segment in name.split("."):
        try:
            parts.append(int(segment))
        except ValueError:
            return (0, name)  # nicht-numerisch -> hinter alle int-Versionen
    return (1, *parts)


def _scan_claude_install_dirs() -> str | None:
    """NT-549 Pass 4 (Lisbeth 15:10) + NT-548 Pass 10 (Lisbeth 15:33):
    Wildcard-Scan ueber bekannte Claude-Code-Install-Ordner pro User-Profil.

    Findet die jeweils neueste claude.exe unter
    ``%USERPROFILE%\\AppData\\Roaming\\Claude\\claude-code\\<version>\\claude.exe``,
    so dass ein Versions-Upgrade (z.B. 2.1.128 -> 2.2.0 -> 2.10.0) keinen
    manuellen Eingriff verlangt. Sortierung ueber numerische Versions-Tuples
    (siehe ``_version_key``), nicht lexikalisch.

    Returns: Pfad zur neuesten claude.exe oder None wenn nichts gefunden.
    """
    candidate_roots: list[Path] = []
    user_profile = os.environ.get("USERPROFILE", "").strip()
    if user_profile:
        candidate_roots.append(Path(user_profile) / "AppData" / "Roaming" / "Claude" / "claude-code")
    appdata = os.environ.get("APPDATA", "").strip()
    if appdata:
        candidate_roots.append(Path(appdata) / "Claude" / "claude-code")
    seen: set[Path] = set()
    found: list[Path] = []
    for root in candidate_roots:
        if root in seen or not root.is_dir():
            continue
        seen.add(root)
        version_dirs = [d for d in root.iterdir() if d.is_dir()]
        version_dirs.sort(key=lambda d: _version_key(d.name), reverse=True)
        for version_dir in version_dirs:
            exe = version_dir / "claude.exe"
            if exe.exists():
                found.append(exe)
                break  # erste (neueste) je root reicht
    if not found:
        return None
    return str(found[0])


def _resolve_claude_exe() -> str:
    """Findet die claude.exe in dieser Reihenfolge:

    1. Env-Var ``SID_CLAUDE_EXE`` — explizite Override fuer Tests / non-default Installs.
    2. PATH-Lookup via ``shutil.which`` — akzeptiert ``claude``, ``claude.exe``, ``claude.cmd``.
    3. Wildcard-Scan ueber bekannte Install-Ordner — robust gegen Versionswechsel.
    4. Leerer String — der Aufrufer muss dann eine TranslationError werfen.

    Lisbeth NT-549 (MEDIUM FUNCTIONAL): vorher war der Pfad hartkodiert auf
    `2.1.128` und ein User-Profil — jedes Claude-Update oder ein anderes
    Profil hat den Real-Pfad zerschossen. Pass 4: hardcoded Fallback ist
    weg, dafuer dynamischer Versions-Scan.
    """
    explicit = os.environ.get("SID_CLAUDE_EXE", "").strip()
    if explicit:
        return explicit
    for cand in ("claude", "claude.exe", "claude.cmd"):
        found = shutil.which(cand)
        if found:
            return found
    scanned = _scan_claude_install_dirs()
    if scanned:
        return scanned
    return CLAUDE_EXE_DEFAULT


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

    def __init__(self, exe_path: str | None = None, timeout: int = DEFAULT_TIMEOUT):
        # exe_path=None -> Auto-Resolve via Env / PATH / Default. Explizite
        # Override (Tests, non-standard Installs) ueber Konstruktor-Argument.
        self.exe = exe_path or _resolve_claude_exe()
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
            # Prompt VIA STDIN, NICHT als CLI-Argument. Bei Prompts ueber ~1k chars
            # mit eingebetteten JSON-Quotes verstuemmelt claude.exe die CLI-Variante
            # auf Windows (Test 08.05.2026, NT-558 Live: gleicher Prompt als arg
            # -> Claude antwortet "keinen zu uebersetzenden Text"; via stdin ->
            # sauberer SID_OUTPUT-Block). Stdin umgeht command-line-Quoting komplett.
            r = subprocess.run(
                [self.exe, "-p", "--disable-slash-commands"],
                input=prompt,
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


_OUTPUT_OPEN = "<SID_OUTPUT>"
_OUTPUT_CLOSE = "</SID_OUTPUT>"


def _parse_response(text: str, expected_fields: list[str]) -> dict[str, str]:
    """Extrahiert das JSON aus <SID_OUTPUT>-Markern.

    Lisbeth NT-549 Pass 2 (MEDIUM FUNCTIONAL): der vorige Parser war brittle:

    1. Regex ``\\{.*?\\}`` non-greedy bricht beim ERSTEN ``}`` ab. Wenn ein
       uebersetzter String selbst ein ``}`` enthaelt (z.B. CSS-aehnliche
       Texte, Sysreqs mit "{ etwas }"), wird der Block frueh abgeschnitten
       und das JSON-Decode failt.
    2. Mehrere ``<SID_OUTPUT>``-Bloecke (LLM denkt zwischendurch nochmal nach
       und setzt einen zweiten Block) wurden vom ``search()`` falsch zugeordnet
       — der ERSTE wurde gewaehlt statt des letzten/finalen.
    3. Es gab keine Validierung, dass alle ``expected_fields`` zurueckkamen.
       Eine Antwort, die ein Feld weglaesst, wurde stillschweigend als
       Erfolg behandelt — Translation-Datei wurde partial geupdated, der
       fehlende Wert blieb stale.

    Pass 2 Loesung:
    - Letzten ``<SID_OUTPUT>`` ... ``</SID_OUTPUT>``-Block nehmen.
    - JSON-Objekt mit ``json.JSONDecoder.raw_decode()`` extrahieren — der
      Decoder respektiert String-Quoting korrekt und stoppt am eigentlichen
      Objekt-Ende, unabhaengig von ``}``-Vorkommen in den Werten.
    - Alle ``expected_fields`` muessen im Output sein, sonst Error.
    """
    last_open = text.rfind(_OUTPUT_OPEN)
    if last_open == -1:
        raise TranslationError(
            f"Kein <SID_OUTPUT>-Block in der Claude-Antwort gefunden. "
            f"Antwort-Anfang: {text[:300]!r}"
        )
    after_open = last_open + len(_OUTPUT_OPEN)
    close_idx = text.find(_OUTPUT_CLOSE, after_open)
    if close_idx == -1:
        raise TranslationError(
            f"Kein </SID_OUTPUT>-Marker nach Open-Tag (LLM-Antwort abgeschnitten?). "
            f"Tail: {text[after_open:after_open + 300]!r}"
        )
    inner = text[after_open:close_idx]
    obj_start = inner.find("{")
    if obj_start == -1:
        raise TranslationError(f"Kein JSON-Objekt im SID_OUTPUT-Block: {inner[:300]!r}")

    decoder = json.JSONDecoder()
    try:
        data, _end = decoder.raw_decode(inner[obj_start:])
    except json.JSONDecodeError as e:
        raise TranslationError(
            f"JSON-Decode-Fehler im SID_OUTPUT: {e}; raw={inner[obj_start:obj_start + 300]!r}"
        )
    if not isinstance(data, dict):
        raise TranslationError(f"SID_OUTPUT ist kein Objekt: {type(data).__name__}")

    # Nur erwartete Felder uebernehmen, andere sind Halluzinationen.
    # Werte werden zu str konvertiert, falls Claude versehentlich int/null sendet.
    out = {k: str(v) if v is not None else "" for k, v in data.items() if k in expected_fields}

    # Pass 2 Validation: jedes erwartete Feld muss zurueckkommen.
    missing = [f for f in expected_fields if f not in out]
    if missing:
        raise TranslationError(
            f"SID_OUTPUT fehlen {len(missing)} Feld(er): {missing}. "
            f"Erhalten: {list(out.keys())}. "
            f"Claude hat die Felder ausgelassen — kein partial update."
        )
    return out


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

    # NT-548 Pass 9 (Lisbeth 14:59 LOW FUNCTIONAL): fields=[] explizit
    # vom None-Fall trennen, sonst bedeutet "leerer Filter" faelschlich
    # "alle Felder uebersetzen" statt "keine Felder".
    field_filter = set(fields) if fields is not None else None

    candidates: dict[str, str] = {}
    skipped: list[str] = []
    for f, value in master.fields.items():
        if field_filter is not None and f not in field_filter:
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
