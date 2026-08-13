"""Application-level composition for discovery followed by entirely local ranking."""
from __future__ import annotations

import logging
from dataclasses import replace

from bot.services.film_recommendations import (
    CandidateScore, MovieCandidateProvider, RecommendationConstraints,
    profiles_for_actor, rank_candidates,
)

logger = logging.getLogger(__name__)
MAX_DISCOVERY_REQUESTS = 6
QUALITY_VOTE_FLOOR = 500


class RecommendationUnavailable(RuntimeError):
    pass


class MovieRecommendationService:
    def __init__(self, provider: MovieCandidateProvider | None, *, discovery_pages: int = 2) -> None:
        self._provider = provider
        self._pages = max(1, min(discovery_pages, 3))

    async def recommend(self, films: list[dict], *, actor: str,
                        constraints: RecommendationConstraints | None = None,
                        shown: set[tuple[str, str, str]] | None = None,
                        generation: int = 0) -> list[CandidateScore]:
        if self._provider is None:
            raise RecommendationUnavailable("Movie recommendations are not configured")
        constraints = constraints or RecommendationConstraints()
        profiles = profiles_for_actor(films, actor)
        profile_list = profiles if isinstance(profiles, tuple) else (profiles,)
        media_types = [kind for kind in ("movie", "tv") if constraints.media_type in {"any", kind}]
        pools: list[tuple[str, RecommendationConstraints, str]] = []
        for kind in media_types:
            pools.append((kind, constraints, "popularity.desc"))
        # Broad requests get a quality pool and then up to two profile-guided
        # pools. Explicit genre constraints remain untouched and authoritative.
        if not constraints.include_genres:
            for kind in media_types:
                pools.append((kind, replace(constraints, min_vote_count=max(constraints.min_vote_count, QUALITY_VOTE_FLOOR)), "vote_average.desc"))
            explicit = sorted(((ev.score, genre) for p in profile_list for genre, ev in p.genres.items() if ev.score > 0), reverse=True)
            weak = sorted(((ev.score, genre) for p in profile_list for genre, ev in p.want_genres.items() if ev.score > 0), reverse=True)
            guided = []
            for _, genre in explicit + weak:
                if genre not in guided:
                    guided.append(genre)
            for genre in guided:
                if len(pools) >= MAX_DISCOVERY_REQUESTS:
                    break
                kind = media_types[(len(pools) - len(media_types) * 2) % len(media_types)]
                pools.append((kind, replace(constraints, include_genres=frozenset({genre})), "popularity.desc"))
        pools = pools[:MAX_DISCOVERY_REQUESTS]
        candidates = []
        page = max(1, generation + 1)
        for kind, pool_constraints, sort_by in pools:
            method = self._provider.discover_movies if kind == "movie" else self._provider.discover_tv
            candidates.extend(await method(pool_constraints, pages=1, start_page=page, sort_by=sort_by))
        raw_count = len(candidates)
        unique = {(candidate.provider, candidate.media_type, candidate.external_id): candidate for candidate in candidates}
        candidates = list(unique.values())
        shown = shown or set()
        candidates = [candidate for candidate in candidates if (candidate.provider, candidate.media_type, candidate.external_id) not in shown]
        watched = [film for film in films if film.get("status") == "watched"]
        want = [film for film in films if film.get("status") == "want"]
        scores = rank_candidates(candidates, profiles, watched, want, constraints)
        logger.info("recommendation discovery pools=%d raw=%d unique=%d unseen=%d shown=%d results=%d actor_mode=%s",
                    len(pools), raw_count, len(unique), len(candidates), len(shown), len(scores), actor)
        logger.info("recommendation profile reacted_count=%d want_interest_count=%d",
                    sum(p.reacted_count for p in profile_list), sum(p.want_interest_count for p in profile_list))
        return scores
