"""Steam-Sprachcodes — konstante Liste der 29 von Steam unterstuetzten Sprachen.

Reihenfolge: identisch zum offiziellen Steam-Loka-Download
(siehe data/_samples/storepage_1141975_all.json).

Jede Sprache hat:
- code:      Steam-interner Schluessel (z.B. "english", "schinese", "koreana")
- iso:       ISO 639-1 / 639-2 / Region-Code (z.B. "en", "zh-Hans", "es-419")
- display:   Deutscher Anzeigename
- native:    Eigenname (wie der Sprecher selbst seine Sprache nennt)

`master_lang` ist per Konvention "german" — Thomas pflegt den deutschen
Master, alles andere wird daraus uebersetzt.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SteamLang:
    code: str
    iso: str
    display: str
    native: str


STEAM_LANGS: tuple[SteamLang, ...] = (
    SteamLang("english",    "en",      "Englisch",                    "English"),
    SteamLang("french",     "fr",      "Franzoesisch",                "Francais"),
    SteamLang("italian",    "it",      "Italienisch",                 "Italiano"),
    SteamLang("german",     "de",      "Deutsch",                     "Deutsch"),
    SteamLang("spanish",    "es",      "Spanisch (Spanien)",          "Espanol"),
    SteamLang("brazilian",  "pt-BR",   "Portugiesisch (Brasilien)",   "Portugues do Brasil"),
    SteamLang("bulgarian",  "bg",      "Bulgarisch",                  "Bulgarski"),
    SteamLang("tchinese",   "zh-Hant", "Chinesisch (traditionell)",   "Zhongwen (Fanti)"),
    SteamLang("schinese",   "zh-Hans", "Chinesisch (vereinfacht)",    "Zhongwen (Jianti)"),
    SteamLang("danish",     "da",      "Daenisch",                    "Dansk"),
    SteamLang("finnish",    "fi",      "Finnisch",                    "Suomi"),
    SteamLang("greek",      "el",      "Griechisch",                  "Ellinika"),
    SteamLang("indonesian", "id",      "Indonesisch",                 "Bahasa Indonesia"),
    SteamLang("japanese",   "ja",      "Japanisch",                   "Nihongo"),
    SteamLang("koreana",    "ko",      "Koreanisch",                  "Hangugeo"),
    SteamLang("dutch",      "nl",      "Niederlaendisch",             "Nederlands"),
    SteamLang("norwegian",  "no",      "Norwegisch",                  "Norsk"),
    SteamLang("polish",     "pl",      "Polnisch",                    "Polski"),
    SteamLang("portuguese", "pt-PT",   "Portugiesisch (Portugal)",    "Portugues"),
    SteamLang("romanian",   "ro",      "Rumaenisch",                  "Romana"),
    SteamLang("russian",    "ru",      "Russisch",                    "Russkiy"),
    SteamLang("swedish",    "sv",      "Schwedisch",                  "Svenska"),
    SteamLang("latam",      "es-419",  "Spanisch (Lateinamerika)",    "Espanol (Latinoamerica)"),
    SteamLang("thai",       "th",      "Thailaendisch",               "Phasa Thai"),
    SteamLang("czech",      "cs",      "Tschechisch",                 "Cestina"),
    SteamLang("turkish",    "tr",      "Tuerkisch",                   "Turkce"),
    SteamLang("ukrainian",  "uk",      "Ukrainisch",                  "Ukrainska"),
    SteamLang("hungarian",  "hu",      "Ungarisch",                   "Magyar"),
    SteamLang("vietnamese", "vi",      "Vietnamesisch",               "Tieng Viet"),
)

CODES: tuple[str, ...] = tuple(l.code for l in STEAM_LANGS)
BY_CODE: dict[str, SteamLang] = {l.code: l for l in STEAM_LANGS}
MASTER_CODE: str = "german"


def is_valid(code: str) -> bool:
    return code in BY_CODE


def get(code: str) -> SteamLang:
    if code not in BY_CODE:
        raise KeyError(f"Unbekannter Steam-Sprachcode: {code!r}")
    return BY_CODE[code]
