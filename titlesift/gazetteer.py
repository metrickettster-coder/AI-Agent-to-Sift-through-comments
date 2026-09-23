"""Canonical title catalogue and alias index.

In production this is backed by TMDB / AniList / MangaDex / OpenLibrary.
The seed below is a hand-built slice used to develop and test the
resolution logic without network access.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------- text utils

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_WS = re.compile(r"\s+")


def fold(text: str) -> str:
    """Aggressive normalisation used for every index key and lookup."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.casefold()
    text = _PUNCT.sub(" ", text)
    return _WS.sub(" ", text).strip()


# Words that never carry title meaning on their own. Used to strip trailing
# noise ("solo leveling manhwa" -> "solo leveling") and to reject candidates.
MEDIA_WORDS = {
    "manhwa", "manga", "manhua", "webtoon", "anime", "novel", "ln", "wn",
    "lightnovel", "book", "books", "movie", "movies", "film", "show",
    "series", "comic", "webcomic", "adaptation", "manwha",
    "shonen", "shounen", "seinen", "shoujo", "josei", "isekai", "oneshot",
    "novel", "webnovel", "film", "anime", "donghua", "read", "author",
}

SERIAL_NOISE = re.compile(
    r"\b(?:season|s|ep|episode|chapter|ch|vol|volume|part|pt|arc)\s*\.?\s*\d+\b",
    re.IGNORECASE,
)

LEADING_ARTICLES = ("the ", "a ", "an ")


def trigrams(text: str) -> set[str]:
    """Character trigrams over the space-stripped form, for fuzzy blocking."""
    t = text.replace(" ", "")
    if len(t) < 3:
        return {t} if t else set()
    return {t[i:i + 3] for i in range(len(t) - 2)}


@dataclass
class Title:
    canonical: str
    kind: str                      # movie | manhwa | webtoon | manga | book
    aliases: list[str] = field(default_factory=list)
    year: int | None = None

    def all_surface_forms(self) -> list[str]:
        return [self.canonical, *self.aliases]


# ------------------------------------------------------------- seed catalogue

SEED: list[Title] = [
    Title("Solo Leveling", "manhwa", ["sololeveling", "solo levelling", "na honjaman lebel-eop", "only i level up"], 2018),
    Title("Tower of God", "manhwa", ["tog", "sings", "kami no tou"], 2010),
    Title("The Beginning After the End", "manhwa", ["tbate", "beginning after the end"], 2018),
    Title("Omniscient Reader's Viewpoint", "manhwa", ["orv", "omniscient reader", "omniscient readers viewpoint", "jeonjijeok dokja sijeom"], 2018),
    Title("Lookism", "manhwa", ["oemo jisangjuui"], 2014),
    Title("The God of High School", "manhwa", ["gohs", "god of highschool"], 2011),
    Title("Noblesse", "manhwa", [], 2007),
    Title("Sweet Home", "manhwa", [], 2017),
    Title("Nano Machine", "manhwa", ["nanomachine"], 2020),
    Title("Eleceed", "manhwa", [], 2018),
    Title("Second Life Ranker", "manhwa", ["ranker who lives a second time", "slr"], 2017),
    Title("Return of the Mount Hua Sect", "manhwa", ["romhs", "mount hua sect", "return of mount hua"], 2021),
    Title("Villain to Kill", "manhwa", ["vtk"], 2020),
    Title("Bastard", "manhwa", [], 2014),
    Title("Reaper of the Drifting Moon", "manhwa", [], 2021),

    Title("Attack on Titan", "manga", ["aot", "shingeki no kyojin", "snk", "attack titan"], 2009),
    Title("Jujutsu Kaisen", "manga", ["jjk", "jujitsu kaisen", "jujutsu"], 2018),
    Title("One Punch Man", "manga", ["opm", "onepunch man", "one-punch man", "saitama"], 2009),
    Title("Berserk", "manga", [], 1989),
    Title("Vagabond", "manga", [], 1998),
    Title("Vinland Saga", "manga", ["vs"], 2005),
    Title("Chainsaw Man", "manga", ["csm", "chainsawman"], 2018),
    Title("Hunter x Hunter", "manga", ["hxh", "hunter hunter"], 1998),
    Title("Monster", "manga", ["naoki urasawa monster"], 1994),
    Title("Oyasumi Punpun", "manga", ["goodnight punpun", "punpun"], 2007),
    Title("Death Note", "manga", ["dn"], 2003),
    Title("Fullmetal Alchemist", "manga", ["fma", "fmab", "fullmetal alchemist brotherhood"], 2001),
    Title("Blue Lock", "manga", ["bluelock"], 2018),
    Title("Dungeon Meshi", "manga", ["delicious in dungeon", "dunmeshi"], 2014),

    Title("Lore Olympus", "webtoon", [], 2018),
    Title("Let's Play", "webtoon", ["lets play"], 2017),
    Title("unOrdinary", "webtoon", ["unordinary"], 2016),
    Title("True Beauty", "webtoon", [], 2018),

    Title("Interstellar", "movie", [], 2014),
    Title("Parasite", "movie", ["gisaengchung"], 2019),
    Title("Everything Everywhere All at Once", "movie", ["eeaao", "everything everywhere"], 2022),
    Title("Blade Runner 2049", "movie", ["br2049", "blade runner"], 2017),
    Title("The Prestige", "movie", [], 2006),
    Title("Oldboy", "movie", ["old boy"], 2003),
    Title("Whiplash", "movie", [], 2014),
    Title("Arrival", "movie", [], 2016),

    Title("Dune", "book", ["dune frank herbert"], 1965),
    Title("The Name of the Wind", "book", ["notw", "name of the wind", "kingkiller"], 2007),
    Title("Project Hail Mary", "book", ["phm", "hail mary"], 2021),
    Title("Blood Meridian", "book", [], 1985),
    Title("The Three-Body Problem", "book", ["three body problem", "3 body problem", "tbp"], 2008),
    Title("Piranesi", "book", [], 2020),

    Title("One Piece", "manga", ["op", "onepiece"], 1997),
    Title("The Boxer", "manhwa", ["boxer"], 2019),
    Title("Ember Knight", "manhwa", [], 2020),
    Title("Viral Hit", "manhwa", ["how to fight", "naneun nareul jeongbihanda"], 2020),
    Title("Legend of the Northern Blade", "manhwa", ["lotnb", "northern blade", "lotn"], 2019),
    Title("SSS-Class Suicide Hunter", "manhwa", ["sss class suicide hunter", "suicide hunter", "sss class revival hunter", "revival hunter"], 2020),
    Title("Shotgun Boy", "manhwa", ["shot gun boy"], 2020),
    Title("Pigpen", "manhwa", [], 2021),
    Title("Medical Return", "manhwa", [], 2018),
    Title("Manager Kim", "manhwa", [], 2021),
    Title("Pyramid Game", "manhwa", [], 2020),
    Title("The Book Eater", "manhwa", ["book eater"], 2021),
    Title("Regressor's Instruction Manual", "manhwa", ["regressor instruction manual", "regressors instruction manual"], 2020),
    Title("Mushoku Tensei", "manga", ["mushoko tensei", "jobless reincarnation"], 2012),
    Title("JoJo's Bizarre Adventure", "manga", ["jojo", "jjba"], 1987),
    Title("20th Century Boys", "manga", ["20thcb", "20th century boys"], 1999),
    Title("Wind Breaker", "manhwa", ["windbreaker"], 2013),
    Title("Weak Hero", "manhwa", ["weakhero"], 2019),
    Title("Get Schooled", "manhwa", [], 2020),
    Title("Study Group", "manhwa", [], 2018),
    Title("Vigilante", "manhwa", [], 2020),
    Title("Kengan Ashura", "manga", ["kengan"], 2012),
    Title("Vagabond", "manga", [], 1998),
    Title("Slam Dunk", "manga", ["slamdunk"], 1990),
    Title("Demon Slayer", "manga", ["kimetsu no yaiba", "kny"], 2016),
    Title("My Hero Academia", "manga", ["mha", "boku no hero academia", "bnha"], 2014),
    Title("Naruto", "manga", [], 1999),
    Title("Bleach", "manga", [], 2001),
    Title("Dragon Ball", "manga", ["dragonball", "db", "dbz"], 1984),
    Title("Tokyo Ghoul", "manga", ["tg"], 2011),
    Title("Made in Abyss", "manga", ["mia"], 2012),
    Title("Vinland Saga", "manga", ["vinland"], 2005),
]


class Gazetteer:
    """Alias -> canonical index with acronym and token support."""

    def __init__(self, titles: list[Title] | None = None):
        self.titles: dict[str, Title] = {}
        self.alias_to_canonical: dict[str, str] = {}
        self.acronym_to_canonical: dict[str, set[str]] = {}
        # char-trigram -> alias keys, so a misspelled span can be blocked down
        # to a few dozen plausible aliases instead of scoring the whole catalogue.
        self.trigram_index: dict[str, set[str]] = {}
        # "demonslayer" -> "demon slayer". Hashtags arrive with the spaces
        # already gone, and a frequency segmenter split them into real words
        # that are the wrong words: "demons layer", "jujutsu kai sen".
        self.despaced: dict[str, str] = {}
        self.max_ngram = 1
        for t in titles if titles is not None else SEED:
            self.add(t)

    # ------------------------------------------------------------- building
    def add(self, title: Title) -> None:
        self.titles[title.canonical] = title
        for surface in title.all_surface_forms():
            key = fold(surface)
            if not key:
                continue
            self.alias_to_canonical.setdefault(key, title.canonical)
            glued = key.replace(" ", "")
            if len(glued) >= 6 and " " in key:
                self.despaced.setdefault(glued, key)
            self.max_ngram = max(self.max_ngram, len(key.split()))
            for tri in trigrams(key):
                self.trigram_index.setdefault(tri, set()).add(key)
        acro = self._acronym(title.canonical)
        if len(acro) >= 2:
            self.acronym_to_canonical.setdefault(acro, set()).add(title.canonical)

    @staticmethod
    def _acronym(canonical: str) -> str:
        words = [w for w in fold(canonical).split() if w not in {"of", "the", "a", "an", "at", "in", "to", "and", "x"}]
        return "".join(w[0] for w in words)

    # -------------------------------------------------------------- lookup
    def block(self, folded: str, min_shared: int = 2) -> list[str]:
        """Alias keys plausibly close to `folded`, via shared char trigrams."""
        counts: dict[str, int] = {}
        for tri in trigrams(folded):
            for alias in self.trigram_index.get(tri, ()):
                counts[alias] = counts.get(alias, 0) + 1
        return [a for a, c in counts.items() if c >= min_shared]

    def exact(self, folded: str) -> str | None:
        return self.alias_to_canonical.get(folded)

    def by_acronym(self, folded: str) -> str | None:
        hits = self.acronym_to_canonical.get(folded.replace(" ", ""))
        if hits and len(hits) == 1:
            return next(iter(hits))
        return None

    def kind_of(self, canonical: str) -> str:
        t = self.titles.get(canonical)
        return t.kind if t else "unknown"

    def year_of(self, canonical: str) -> int | None:
        t = self.titles.get(canonical)
        return t.year if t else None

    @classmethod
    def from_json(cls, path: str | Path) -> "Gazetteer":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls([Title(**row) for row in data])
