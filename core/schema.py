"""Pydantic-Schema fuer Sids Daten (NT-547).

Drei Datei-Klassen pro Item:

1. `meta.json`
   - identifiziert das Item plattform-uebergreifend
   - speichert welche Sprachen aktiv sind
   - haelt die Schema-Version, damit Migrations ueber kuenftige Versionen
     hinweg moeglich sind

2. `master_<lang>.json`  (per Konvention `master_de.json`)
   - Single Source of Truth fuer ALLE Felder
   - manuell durch Thomas gepflegt
   - Aenderung hier markiert alle abgeleiteten Sprachen als stale

3. `translations/<lang>.json`
   - eine Datei pro Zielsprache
   - enthaelt UEBERSETZTE Werte und pro Feld:
     * stale-Flag (DE wurde geaendert seit letzter Uebersetzung)
     * source_hash (SHA256 des Master-Werts beim letzten Sync)
     * manually_edited (Thomas hat die Auto-Translation ueberschrieben)
     * last_translated_at (ISO-Timestamp)

Die FELDER selbst (short_description, about, sysreqs.*, ea_*) sind
absichtlich als Free-Form-Dict im `fields`-Block, kein hartes Schema —
weil Steam-Felder sich aendern koennen und wir bei kuenftigen Plattformen
andere Felder kriegen. Der Importer kennt die konkreten Feldnamen.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

SCHEMA_VERSION = 1


class ItemMeta(BaseModel):
    """meta.json — identifiziert das Item, speichert aktive Sprachen."""

    schema_version: int = SCHEMA_VERSION
    item_id: str  # Plattform-spezifische ID (Steam: AppID als String)
    platform: str  # "steam" | "windows" | "xbox" | "android" | ...
    name: str  # interner Name (z.B. "Passage 5")
    master_lang: str = "german"  # welche Sprache ist der Master
    active_languages: list[str] = Field(default_factory=list)
    early_access: bool = False  # bestimmt ob ea_*-Felder gepflegt werden
    notes: str = ""  # freier Text fuer Thomas-Notizen
    created_at: str = ""  # ISO-Timestamp der Anlage
    updated_at: str = ""  # ISO-Timestamp der letzten Aenderung am meta-File


class MasterDocument(BaseModel):
    """master_<lang>.json — Master-Texte (DE per Konvention)."""

    schema_version: int = SCHEMA_VERSION
    item_id: str
    lang: str  # = ItemMeta.master_lang
    fields: dict[str, Any] = Field(default_factory=dict)
    updated_at: str = ""


class TranslationField(BaseModel):
    """Pro Feld in einer uebersetzten Sprache: Wert + Synchronstand."""

    value: str = ""
    source_hash: str = ""  # SHA256 des Master-Werts beim letzten Sync
    stale: bool = True  # True wenn Master geaendert seit letzter Uebersetzung
    manually_edited: bool = False  # Thomas hat Auto-Translation ueberschrieben
    last_translated_at: str = ""  # ISO-Timestamp


class TranslationDocument(BaseModel):
    """translations/<lang>.json — uebersetzte Werte mit Sync-Status."""

    schema_version: int = SCHEMA_VERSION
    item_id: str
    lang: str
    fields: dict[str, TranslationField] = Field(default_factory=dict)
    updated_at: str = ""


# --- Felder, die fuer Steam-Items relevant sind --------------------------------
# Liste der Master-Feldnamen, mit denen der Importer und (spaeter) das UI
# arbeiten. Plattform-Adapter koennen die Liste pro Plattform ueberschreiben.
STEAM_FIELDS_STANDARD: tuple[str, ...] = (
    "short_description",
    "about",
    "sysreqs_min_osversion",
    "sysreqs_min_processor",
    "sysreqs_min_memory",
    "sysreqs_min_graphics",
    "sysreqs_min_storage",
    "sysreqs_min_soundcard",
    "sysreqs_rec_osversion",
    "sysreqs_rec_processor",
    "sysreqs_rec_memory",
    "sysreqs_rec_graphics",
    "sysreqs_rec_storage",
    "sysreqs_rec_soundcard",
)

# Early-Access-Q&A Felder — werden separat exportiert (nicht im Steam-Loka-JSON,
# Steam pflegt die EA-Felder im eigenen Backend-Bereich).
STEAM_FIELDS_EA: tuple[str, ...] = (
    "ea_why",
    "ea_duration",
    "ea_difference",
    "ea_state",
    "ea_pricing",
    "ea_community",
)


def all_fields(early_access: bool = False) -> tuple[str, ...]:
    if early_access:
        return STEAM_FIELDS_STANDARD + STEAM_FIELDS_EA
    return STEAM_FIELDS_STANDARD
