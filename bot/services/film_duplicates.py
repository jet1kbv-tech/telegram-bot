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
    AMBIGUOUS_EXTERNAL_IDENTITY = "ambiguous_external_identity"
    AMBIGUOUS_MEDIA_TYPE = "ambiguous_media_type"


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


def effective_media_type(film: dict[str, Any]) -> str:
    value = str(film.get("media_type") or "")
    if value in {"movie", "tv"}:
        return value
    if str(film.get("metadata_provider") or "") == "tmdb" and str(film.get("external_id") or ""):
        return "movie"
    return ""


def find_movie_duplicate(candidate: MovieMetadata, existing_films: list[dict[str, Any]]) -> DuplicateResult:
    candidate_type = candidate.media_type
    if not candidate_type and candidate.metadata_provider == "tmdb" and candidate.external_id:
        candidate_type = "movie"
    if candidate.external_id:
        possible_identity: dict[str, Any] | None = None
        for film in existing_films:
            if str(film.get("external_id") or "") != candidate.external_id:
                continue
            existing_provider = str(film.get("metadata_provider") or "")
            if existing_provider != candidate.metadata_provider:
                continue
            existing_type = effective_media_type(film)
            if existing_type and candidate_type and existing_type == candidate_type:
                return DuplicateResult(DuplicateKind.DEFINITIVE, film, DuplicateReason.EXTERNAL_ID)
            if not existing_type or not candidate_type:
                possible_identity = possible_identity or film
        if possible_identity is not None:
            return DuplicateResult(DuplicateKind.POSSIBLE, possible_identity, DuplicateReason.AMBIGUOUS_EXTERNAL_IDENTITY)

    candidate_title = normalize_movie_title(candidate.title)
    if not candidate_title:
        return NO_DUPLICATE

    possible: dict[str, Any] | None = None
    for film in existing_films:
        if normalize_movie_title(film.get("title")) != candidate_title:
            continue
        existing_type = effective_media_type(film)
        if existing_type and candidate_type and existing_type != candidate_type:
            continue
        ambiguous_type = not existing_type or not candidate_type
        candidate_year = candidate.year
        existing_year = film.get("year") if isinstance(film.get("year"), int) and not isinstance(film.get("year"), bool) else None
        if candidate_year is not None and existing_year is not None:
            if candidate_year != existing_year:
                continue
            existing_external_id = str(film.get("external_id") or "")
            existing_provider = str(film.get("metadata_provider") or "")
            if candidate.external_id and existing_external_id and existing_provider and existing_provider != candidate.metadata_provider:
                continue
            if ambiguous_type:
                possible = possible or film
                continue
            return DuplicateResult(DuplicateKind.DEFINITIVE, film, DuplicateReason.TITLE_AND_YEAR)
        if possible is None:
            possible = film

    if possible is not None:
        return DuplicateResult(DuplicateKind.POSSIBLE, possible, DuplicateReason.TITLE_WITH_MISSING_YEAR)
    return NO_DUPLICATE
