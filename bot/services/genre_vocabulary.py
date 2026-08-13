"""Canonical genre identities shared by recommendation domain boundaries."""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

CANONICAL_GENRES = frozenset({
    "action", "adventure", "animation", "comedy", "crime", "documentary", "drama",
    "family", "fantasy", "history", "horror", "music", "mystery", "romance",
    "science_fiction", "thriller", "war", "western", "action_adventure", "kids",
    "news", "reality", "science_fiction_fantasy", "soap", "talk", "war_politics",
})

_RUSSIAN_ALIASES = {
    "боевик": "action", "приключения": "adventure", "мультфильм": "animation",
    "анимация": "animation", "комедия": "comedy", "комедию": "comedy",
    "смешное": "comedy", "криминал": "crime", "документальный": "documentary",
    "драма": "drama", "семейный": "family", "фэнтези": "fantasy",
    "история": "history", "ужасы": "horror", "музыка": "music",
    "детектив": "mystery", "мелодрама": "romance", "романтика": "romance",
    "фантастика": "science_fiction", "научная фантастика": "science_fiction",
    # This is TMDb's TV genre 10765, rather than either of its two components.
    "нф и фэнтези": "science_fiction_fantasy", "триллер": "thriller",
    "военный": "war", "вестерн": "western",
}

_DISPLAY_RU = {
    "action": "боевик", "adventure": "приключения", "animation": "мультфильм",
    "comedy": "комедия", "crime": "криминал", "documentary": "документальный",
    "drama": "драма", "family": "семейный", "fantasy": "фэнтези",
    "history": "история", "horror": "ужасы", "music": "музыка",
    "mystery": "детектив", "romance": "мелодрама", "science_fiction": "фантастика",
    "thriller": "триллер", "war": "военный", "western": "вестерн",
    "action_adventure": "боевик и приключения", "kids": "детский", "news": "новости",
    "reality": "реалити", "science_fiction_fantasy": "НФ и фэнтези",
    "soap": "мыльная опера", "talk": "ток-шоу", "war_politics": "война и политика",
}


def _identity(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")
    return re.sub(r"(?:[^\w]|_)+", " ", normalized, flags=re.UNICODE).strip()


_ALIASES = {_identity(key): value for key, value in _RUSSIAN_ALIASES.items()}
_ALIASES.update({_identity(key): key for key in CANONICAL_GENRES})


def canonicalize_genre(value: str) -> str | None:
    """Return an allow-listed machine key; never guess an unknown translation."""
    if not isinstance(value, str):
        return None
    return _ALIASES.get(_identity(value))


def canonicalize_genres(values: Iterable[str]) -> tuple[str, ...]:
    """Canonicalize and de-duplicate genres while preserving input order."""
    result: list[str] = []
    for value in values:
        canonical = canonicalize_genre(value)
        if canonical and canonical not in result:
            result.append(canonical)
    return tuple(result)


def genre_display_label(value: str, locale: str = "ru") -> str:
    canonical = canonicalize_genre(value)
    if locale == "ru" and canonical:
        return _DISPLAY_RU[canonical]
    return canonical or value
