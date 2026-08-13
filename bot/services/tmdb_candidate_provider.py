"""Bounded TMDB discovery adapter. Personal history never leaves this module's caller."""
from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from typing import Any

import httpx

from bot.services.film_recommendations import RecommendationCandidate, RecommendationConstraints
from bot.services.movie_metadata import MovieMetadataUnavailable


class TmdbCandidateProvider:
    BASE_URL = "https://api.themoviedb.org/3"
    GENRES = {12: "adventure", 14: "fantasy", 16: "animation", 18: "drama", 27: "horror",
              28: "action", 35: "comedy", 36: "history", 37: "western", 53: "thriller",
              80: "crime", 99: "documentary", 878: "science_fiction", 9648: "mystery",
              10402: "music", 10749: "romance", 10751: "family", 10752: "war",
              10759: "action_adventure", 10762: "kids", 10763: "news", 10764: "reality",
              10765: "science_fiction_fantasy", 10766: "soap", 10767: "talk", 10768: "war_politics"}

    def __init__(self, token: str, *, client: httpx.AsyncClient | None = None, cache_ttl: float = 300,
                 cache_size: int = 64, max_pages: int = 3) -> None:
        self._token, self._client = token.strip(), client
        self._ttl, self._cache_size, self._max_pages = cache_ttl, cache_size, max_pages
        self._cache: OrderedDict[tuple[Any, ...], tuple[float, list[RecommendationCandidate]]] = OrderedDict()
        self._timeout = httpx.Timeout(8.0, connect=3.0, read=6.0, write=3.0, pool=3.0)

    async def discover_movies(self, constraints: RecommendationConstraints, *, pages: int = 1,
                              start_page: int = 1, sort_by: str = "popularity.desc") -> list[RecommendationCandidate]:
        return await self._discover("movie", constraints, pages, start_page, sort_by)

    async def discover_tv(self, constraints: RecommendationConstraints, *, pages: int = 1,
                          start_page: int = 1, sort_by: str = "popularity.desc") -> list[RecommendationCandidate]:
        return await self._discover("tv", constraints, pages, start_page, sort_by)

    async def _discover(self, media_type: str, constraints: RecommendationConstraints, pages: int,
                        start_page: int, sort_by: str) -> list[RecommendationCandidate]:
        pages = max(1, min(pages, self._max_pages))
        start_page = max(1, min(start_page, 500))
        if sort_by not in {"popularity.desc", "vote_average.desc"}:
            raise ValueError("unsupported deterministic discovery sort")
        key = (media_type, constraints, pages, start_page, sort_by)
        cached = self._cache.get(key)
        if cached and time.monotonic() - cached[0] < self._ttl:
            self._cache.move_to_end(key); return list(cached[1])
        values: list[RecommendationCandidate] = []
        for page in range(start_page, start_page + pages):
            params = self._params(media_type, constraints, page, sort_by)
            payload = await self._request(f"/discover/{media_type}", params)
            raw_results = payload.get("results")
            if not isinstance(raw_results, list): raise MovieMetadataUnavailable("TMDB returned malformed discovery results")
            values.extend(filter(None, (self._parse(media_type, raw) for raw in raw_results)))
        unique = list({(item.media_type, item.external_id): item for item in values}.values())
        self._cache[key] = (time.monotonic(), unique); self._cache.move_to_end(key)
        while len(self._cache) > self._cache_size: self._cache.popitem(last=False)
        return list(unique)

    def _params(self, media_type: str, c: RecommendationConstraints, page: int,
                sort_by: str = "popularity.desc") -> dict[str, str]:
        params = {"language": "ru-RU", "sort_by": sort_by, "page": str(page),
                  "vote_count.gte": str(c.min_vote_count), "include_adult": "false"}
        if c.min_rating is not None: params["vote_average.gte"] = str(c.min_rating)
        if c.language: params["with_original_language"] = c.language
        if c.country: params["with_origin_country"] = c.country
        if c.max_runtime is not None: params["with_runtime.lte"] = str(c.max_runtime)
        date_key = "primary_release_date" if media_type == "movie" else "first_air_date"
        if c.min_year is not None: params[f"{date_key}.gte"] = f"{c.min_year}-01-01"
        if c.max_year is not None: params[f"{date_key}.lte"] = f"{c.max_year}-12-31"
        reverse = {name: str(identifier) for identifier, name in self.GENRES.items()}
        numeric = sorted(reverse.get(str(g), str(g)) for g in c.include_genres if str(g) in reverse or str(g).isdigit())
        if numeric: params["with_genres"] = ",".join(numeric)
        excluded = sorted(reverse.get(str(g), str(g)) for g in c.exclude_genres if str(g) in reverse or str(g).isdigit())
        if excluded: params["without_genres"] = ",".join(excluded)
        return params

    async def get_details(self, media_type: str, external_id: str) -> RecommendationCandidate:
        if media_type not in {"movie", "tv"}: raise MovieMetadataUnavailable("Unsupported media type")
        payload = await self._request(f"/{media_type}/{external_id}", {"language": "ru-RU"})
        candidate = self._parse(media_type, payload, details=True)
        if candidate is None: raise MovieMetadataUnavailable("TMDB returned malformed title details")
        return candidate

    async def _request(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        if not self._token: raise MovieMetadataUnavailable("TMDB recommendation token is not configured")
        headers = {"Authorization": f"Bearer {self._token}", "accept": "application/json"}
        try:
            if self._client: response = await self._client.get(path, params=params, headers=headers, timeout=self._timeout)
            else:
                async with httpx.AsyncClient(base_url=self.BASE_URL) as client:
                    response = await client.get(path, params=params, headers=headers, timeout=self._timeout)
        except (httpx.HTTPError, asyncio.TimeoutError) as error:
            raise MovieMetadataUnavailable("TMDB recommendation request failed") from error
        if response.status_code == 429: raise MovieMetadataUnavailable("TMDB recommendation rate limit reached")
        if response.status_code >= 400: raise MovieMetadataUnavailable(f"TMDB recommendation HTTP {response.status_code}")
        try: payload = response.json()
        except ValueError as error: raise MovieMetadataUnavailable("TMDB returned invalid JSON") from error
        if not isinstance(payload, dict): raise MovieMetadataUnavailable("TMDB returned an unexpected response")
        return payload

    @staticmethod
    def _parse(media_type: str, raw: Any, details: bool = False) -> RecommendationCandidate | None:
        if not isinstance(raw, dict) or not raw.get("id"): return None
        movie = media_type == "movie"; title = raw.get("title" if movie else "name")
        if not isinstance(title, str) or not title.strip(): return None
        date = raw.get("release_date" if movie else "first_air_date")
        try: year = int(date[:4]) if isinstance(date, str) and len(date) >= 4 else None
        except ValueError: year = None
        raw_genres = raw.get("genres") if details else raw.get("genre_ids")
        genres = tuple(str(x.get("name")) for x in raw_genres if isinstance(x, dict) and x.get("name")) if details and isinstance(raw_genres, list) else tuple(TmdbCandidateProvider.GENRES.get(x, str(x)) for x in raw_genres if isinstance(x, int)) if isinstance(raw_genres, list) else ()
        runtime = raw.get("runtime") if movie else (raw.get("episode_run_time") or [None])[0]
        return RecommendationCandidate(str(raw["id"]), media_type, title.strip(), str(raw.get("original_title" if movie else "original_name") or ""),
            year, genres, str(raw.get("overview") or ""), _number(raw.get("vote_average")), _integer(raw.get("vote_count")),
            _number(raw.get("popularity")) or 0.0, _integer(runtime) or None, str(raw.get("original_language") or ""),
            tuple(x for x in raw.get("origin_country", []) if isinstance(x, str)))


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _integer(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else 0
