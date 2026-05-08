"""Mapping zwischen Steam-Loka-JSON-Schluesseln und Sid-Feldnamen.

Steam-JSON-Format pro Sprache:
    "app[content][short_description]": "...",
    "app[content][about]": "...",
    "app[content][sysreqs][windows][min][osversion]": "...",
    ...

Sid speichert Felder unter Slash-freien Namen (s. core/schema.py
STEAM_FIELDS_STANDARD), z.B. "short_description", "sysreqs_min_processor".

Diese Modul liefert die bidirektionale Map:
- STEAM_TO_FIELD: Steam-Key -> Sid-Feldname
- FIELD_TO_STEAM: umgekehrt (fuer Export)
"""
from __future__ import annotations

# Reihenfolge identisch zur Sid-Feld-Liste in core/schema.py
STEAM_TO_FIELD: dict[str, str] = {
    "app[content][short_description]":                    "short_description",
    "app[content][about]":                                "about",
    "app[content][sysreqs][windows][min][osversion]":     "sysreqs_min_osversion",
    "app[content][sysreqs][windows][min][processor]":     "sysreqs_min_processor",
    "app[content][sysreqs][windows][min][memory]":        "sysreqs_min_memory",
    "app[content][sysreqs][windows][min][graphics]":      "sysreqs_min_graphics",
    "app[content][sysreqs][windows][min][storage]":       "sysreqs_min_storage",
    "app[content][sysreqs][windows][rec][osversion]":     "sysreqs_rec_osversion",
    "app[content][sysreqs][windows][rec][processor]":     "sysreqs_rec_processor",
    "app[content][sysreqs][windows][rec][memory]":        "sysreqs_rec_memory",
    "app[content][sysreqs][windows][rec][graphics]":      "sysreqs_rec_graphics",
    "app[content][sysreqs][windows][rec][storage]":       "sysreqs_rec_storage",
}

FIELD_TO_STEAM: dict[str, str] = {v: k for k, v in STEAM_TO_FIELD.items()}
