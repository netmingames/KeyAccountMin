"""Anzeige-Namen und Editor-Hinweise pro Feld (NT-548).

Trennt das technische Feldnamen-Schema (s. core/schema.py) von der UI-Sicht.
"""
from __future__ import annotations

# Mapping: technischer Feldname -> (Anzeigename, multiline?, hilfetext)
FIELD_META: dict[str, tuple[str, bool, str]] = {
    # Standard-Felder (im Steam-Loka-JSON enthalten)
    "short_description":     ("Kurzbeschreibung",                       False, "Maximal 300 Zeichen, ein Satz, Marketing-Hook."),
    "about":                 ("Beschreibung",                           True,  "Lange Spielbeschreibung. Steam zeigt das im 'Ueber dieses Spiel'-Block."),
    "sysreqs_min_osversion": ("Min. Systemanforderung - OS",            False, "z.B. 'Windows 7/8/10/11'."),
    "sysreqs_min_processor": ("Min. Systemanforderung - Prozessor",     False, "z.B. 'Pentium 1 GHz'."),
    "sysreqs_min_memory":    ("Min. Systemanforderung - Arbeitsspeicher", False, "z.B. '2 GB RAM'."),
    "sysreqs_min_graphics":  ("Min. Systemanforderung - Grafik",        False, "z.B. 'OpenGL 2.0 fähig'."),
    "sysreqs_min_storage":   ("Min. Systemanforderung - Speicherplatz", False, "z.B. '500 MB freier Speicherplatz'."),
    "sysreqs_rec_osversion": ("Empfohlen - OS",                         False, ""),
    "sysreqs_rec_processor": ("Empfohlen - Prozessor",                  False, ""),
    "sysreqs_rec_memory":    ("Empfohlen - Arbeitsspeicher",            False, ""),
    "sysreqs_rec_graphics":  ("Empfohlen - Grafik",                     False, ""),
    "sysreqs_rec_storage":   ("Empfohlen - Speicherplatz",              False, ""),
    # Early-Access-Felder (Steam-Backend separater Bereich, nicht im Loka-JSON)
    "ea_why":        ("Warum Early Access?",                                  True,  ""),
    "ea_duration":   ("Wie lange wird das Spiel im Early Access bleiben?",    True,  ""),
    "ea_difference": ("Wie unterscheidet sich die Vollversion vom EA?",       True,  ""),
    "ea_state":      ("Was ist der aktuelle Stand der Early-Access-Version?", True,  ""),
    "ea_pricing":    ("Wird der Preis sich nach dem Early Access aendern?",   True,  ""),
    "ea_community":  ("Wie wird die Community eingebunden?",                  True,  ""),
}


def label(field: str) -> str:
    return FIELD_META.get(field, (field, False, ""))[0]


def is_multiline(field: str) -> bool:
    return FIELD_META.get(field, ("", False, ""))[1]


def hint(field: str) -> str:
    return FIELD_META.get(field, ("", False, ""))[2]
