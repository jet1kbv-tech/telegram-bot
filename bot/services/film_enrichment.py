from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from bot.services.film_duplicates import normalize_movie_title
from bot.services.movie_metadata import MovieMetadata, MovieSearchResult


class EnrichmentDisposition(str, Enum):
    AUTOMATIC = "automatic"
    ENRICHED = "enriched"
    AMBIGUOUS = "ambiguous"
    UNMATCHED = "unmatched"
    CONFLICT = "conflict"
    PROVIDER_ERROR = "provider_error"
    SKIPPED = "skipped"
    DELETED = "deleted"


@dataclass(frozen=True, slots=True)
class MatchDecision:
    disposition: EnrichmentDisposition
    candidates: tuple[MovieSearchResult, ...] = ()


@dataclass(frozen=True, slots=True)
class ApplyResult:
    disposition: EnrichmentDisposition
    film: dict[str, Any] | None = None
    conflicting_film: dict[str, Any] | None = None


METADATA_FIELDS = (
    "metadata_provider",
    "external_id",
    "localized_title",
    "original_title",
    "year",
    "genres",
    "description",
    "external_rating",
)


def identity_state(film: dict[str, Any]) -> str:
    provider = bool(str(film.get("metadata_provider") or "").strip())
    external_id = bool(str(film.get("external_id") or "").strip())
    if provider and external_id:
        return "complete"
    if provider or external_id:
        return "partial"
    return "missing"


def is_enrichment_candidate(film: dict[str, Any]) -> bool:
    return identity_state(film) == "missing"


def classify_search_results(film: dict[str, Any], results: list[MovieSearchResult]) -> MatchDecision:
    title = normalize_movie_title(film.get("title"))
    if not title:
        return MatchDecision(EnrichmentDisposition.UNMATCHED)

    known_year = film.get("year")
    if isinstance(known_year, bool) or not isinstance(known_year, int):
        known_year = None

    useful: list[MovieSearchResult] = []
    exact: dict[tuple[str, str], MovieSearchResult] = {}
    for result in results:
        if not isinstance(result, MovieSearchResult):
            continue
        useful.append(result)
        matches_title = title in {
            normalize_movie_title(result.title),
            normalize_movie_title(result.original_title),
        }
        year_matches = known_year is None or (result.year is not None and result.year == known_year)
        if matches_title and year_matches:
            exact.setdefault((result.metadata_provider, result.external_id), result)

    candidates = tuple(exact.values())
    if len(candidates) == 1:
        return MatchDecision(EnrichmentDisposition.AUTOMATIC, candidates)
    if candidates or useful:
        return MatchDecision(EnrichmentDisposition.AMBIGUOUS, candidates or tuple(useful))
    return MatchDecision(EnrichmentDisposition.UNMATCHED)


def metadata_matches_film(film: dict[str, Any], metadata: MovieMetadata) -> bool:
    title = normalize_movie_title(film.get("title"))
    if not title or title not in {
        normalize_movie_title(metadata.title),
        normalize_movie_title(metadata.original_title),
    }:
        return False
    known_year = film.get("year")
    if isinstance(known_year, int) and not isinstance(known_year, bool):
        return metadata.year is not None and metadata.year == known_year
    return True


def apply_metadata_atomic(storage: Any, film_id: str, metadata: MovieMetadata) -> ApplyResult:
    """Update an existing film in place; never append or replace a film record."""

    def mutator(data: dict[str, Any]) -> ApplyResult:
        films = data.get("films", [])
        film = next((item for item in films if str(item.get("id")) == str(film_id)), None)
        if film is None:
            return ApplyResult(EnrichmentDisposition.DELETED)
        state = identity_state(film)
        if state == "complete":
            return ApplyResult(EnrichmentDisposition.SKIPPED, film=dict(film))
        if state == "partial":
            return ApplyResult(EnrichmentDisposition.CONFLICT, film=dict(film))

        for other in films:
            if other is film:
                continue
            if (
                str(other.get("metadata_provider") or "") == metadata.metadata_provider
                and str(other.get("external_id") or "") == metadata.external_id
            ):
                return ApplyResult(
                    EnrichmentDisposition.CONFLICT,
                    film=dict(film),
                    conflicting_film=dict(other),
                )

        film.update({
            "metadata_provider": metadata.metadata_provider,
            "external_id": metadata.external_id,
            "localized_title": metadata.title,
            "original_title": metadata.original_title,
            "year": metadata.year,
            "genres": list(metadata.genres),
            "description": metadata.description,
            "external_rating": metadata.external_rating,
        })
        return ApplyResult(EnrichmentDisposition.ENRICHED, film=dict(film))

    result, _ = storage.update(mutator)
    return result
