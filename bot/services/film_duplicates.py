from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Any

from bot.services.movie_metadata import MovieMetadata

_PUNCTUATION = str.maketrans({char: " " for char in "‐‑‒–—−-.,:;!?\"'«»()[]{}"})


class DuplicateKind(str, Enum):
    NONE = "no_duplicate"
    DEFINITIVE = "definitive_duplicate"
    POSSIBLE = "possible_duplicate"


class DuplicateReason(str, Enum):
    EXTERNAL_ID = "external_id"
    TITLE_AND_YEAR = "title_and_year"
    TITLE_WITH_MISSING_YEAR = "title_with_missing_year"


@dataclass(frozen=True, slots=True)
class DuplicateResult:
    kind: DuplicateKind
    matching_film: dict[str, Any] | None = None
    reason: DuplicateReason | None = None


NO_DUPLICATE = DuplicateResult(DuplicateKind.NONE)


def normalize_movie_title(title: Any) -> str:
    if not isinstance(title, str):
        return ""
    normalized = unicodedata.normalize("NFKC", title).strip().casefold().replace("ё", "е")
    normalized = normalized.translate(_PUNCTUATION)
    return re.sub(r"\s+", " ", normalized).strip()


def find_movie_duplicate(candidate: MovieMetadata, existing_films: list[dict[str, Any]]) -> DuplicateResult:
    if candidate.external_id:
        for film in existing_films:
            if str(film.get("external_id") or "") != candidate.external_id:
                continue
            existing_provider = str(film.get("metadata_provider") or "")
            if not existing_provider or existing_provider == candidate.metadata_provider:
                return DuplicateResult(DuplicateKind.DEFINITIVE, film, DuplicateReason.EXTERNAL_ID)

    candidate_title = normalize_movie_title(candidate.title)
    if not candidate_title:
        return NO_DUPLICATE

    possible: dict[str, Any] | None = None
    for film in existing_films:
        if normalize_movie_title(film.get("title")) != candidate_title:
            continue
        candidate_year = candidate.year
        existing_year = film.get("year") if isinstance(film.get("year"), int) and not isinstance(film.get("year"), bool) else None
        if candidate_year is not None and existing_year is not None:
            if candidate_year != existing_year:
                continue
            existing_external_id = str(film.get("external_id") or "")
            existing_provider = str(film.get("metadata_provider") or "")
            if candidate.external_id and existing_external_id and existing_provider and existing_provider != candidate.metadata_provider:
                continue
            return DuplicateResult(DuplicateKind.DEFINITIVE, film, DuplicateReason.TITLE_AND_YEAR)
        if possible is None:
            possible = film

    if possible is not None:
        return DuplicateResult(DuplicateKind.POSSIBLE, possible, DuplicateReason.TITLE_WITH_MISSING_YEAR)
    return NO_DUPLICATE
