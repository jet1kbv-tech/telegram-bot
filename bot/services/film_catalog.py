"""Canonical persistence and local-candidate adapters for Films."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bot.services.film_duplicates import normalize_movie_title
from bot.services.film_recommendations import RecommendationCandidate
from bot.storage import make_id, storage


@dataclass(frozen=True)
class CreateFilmResult:
    created: bool
    film: dict[str, Any]


def candidate_identity(candidate: RecommendationCandidate) -> tuple[str, ...]:
    if candidate.external_id:
        return (candidate.provider, candidate.media_type, candidate.external_id)
    return (normalize_movie_title(candidate.title), str(candidate.year or ""), candidate.media_type)


def _film_identity(film: dict[str, Any]) -> tuple[str, ...]:
    if film.get("external_id"):
        return (str(film.get("metadata_provider") or ""), str(film.get("media_type") or ""), str(film["external_id"]))
    return (normalize_movie_title(str(film.get("localized_title") or film.get("title") or "")),
            str(film.get("year") or ""), str(film.get("media_type") or ""))


def create_want_film(candidate: RecommendationCandidate, *, added_by: str) -> CreateFilmResult:
    """Atomically create a canonical want film, or return its existing duplicate."""
    identity = candidate_identity(candidate)
    def mutate(data: dict[str, Any]) -> CreateFilmResult:
        for film in data.setdefault("films", []):
            if _film_identity(film) == identity:
                return CreateFilmResult(False, dict(film))
        film = {"id": make_id(), "title": candidate.title, "status": "want", "added_by": added_by,
                "comment": "", "sasha_rating": None, "vova_rating": None, "legacy_rating": None,
                "metadata_provider": candidate.provider, "media_type": candidate.media_type,
                "external_id": candidate.external_id, "localized_title": candidate.title,
                "original_title": candidate.original_title, "year": candidate.year,
                "genres": list(candidate.genres), "description": candidate.overview,
                "external_rating": candidate.external_rating}
        data["films"].append(film)
        return CreateFilmResult(True, dict(film))
    result, _ = storage.update(mutate)
    return result


def stored_film_to_candidate(film: dict[str, Any]) -> RecommendationCandidate | None:
    title = str(film.get("localized_title") or film.get("title") or "").strip()
    if not title:
        return None
    media_type = str(film.get("media_type") or "movie")
    if media_type not in {"movie", "tv"}: media_type = "movie"
    return RecommendationCandidate(str(film.get("external_id") or film.get("id") or ""), media_type, title,
        str(film.get("original_title") or ""), film.get("year") if isinstance(film.get("year"), int) else None,
        tuple(x for x in film.get("genres", []) if isinstance(x, str)), str(film.get("description") or ""),
        film.get("external_rating") if isinstance(film.get("external_rating"), (int, float)) else None,
        vote_count=0, provider=str(film.get("metadata_provider") or "local"))
