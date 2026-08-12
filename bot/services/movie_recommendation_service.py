"""Application-level composition for discovery followed by entirely local ranking."""
from __future__ import annotations

from bot.services.film_recommendations import (
    CandidateScore, MovieCandidateProvider, RecommendationConstraints,
    profiles_for_actor, rank_candidates,
)


class RecommendationUnavailable(RuntimeError):
    pass


class MovieRecommendationService:
    def __init__(self, provider: MovieCandidateProvider | None, *, discovery_pages: int = 2) -> None:
        self._provider = provider
        self._pages = max(1, min(discovery_pages, 3))

    async def recommend(self, films: list[dict], *, actor: str,
                        constraints: RecommendationConstraints | None = None) -> list[CandidateScore]:
        if self._provider is None:
            raise RecommendationUnavailable("Movie recommendations are not configured")
        constraints = constraints or RecommendationConstraints()
        candidates = []
        if constraints.media_type in {"any", "movie"}:
            candidates.extend(await self._provider.discover_movies(constraints, pages=self._pages))
        if constraints.media_type in {"any", "tv"}:
            candidates.extend(await self._provider.discover_tv(constraints, pages=self._pages))
        watched = [film for film in films if film.get("status") == "watched"]
        want = [film for film in films if film.get("status") == "want"]
        return rank_candidates(candidates, profiles_for_actor(films, actor), watched, want, constraints)
