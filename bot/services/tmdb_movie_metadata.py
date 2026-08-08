from __future__ import annotations

import logging
import math
from datetime import date
from typing import Any

import httpx

from bot.services.movie_metadata import (
    MovieMetadata,
    MovieMetadataNotFound,
    MovieMetadataUnavailable,
    MovieSearchResult,
)

logger = logging.getLogger(__name__)


class TmdbMovieMetadataProvider:
    PROVIDER_NAME = "tmdb"
    BASE_URL = "https://api.themoviedb.org/3"

    def __init__(self, token: str, *, client: httpx.AsyncClient | None = None) -> None:
        self._token = token.strip()
        self._client = client
        self._timeout = httpx.Timeout(8.0, connect=3.0, read=6.0, write=3.0, pool=3.0)

    async def search_movies(self, query: str) -> list[MovieSearchResult]:
        payload = await self._request(
            "/search/movie",
            params={"query": query, "language": "ru-RU", "region": "RU", "include_adult": "false"},
        )
        raw_results = payload.get("results")
        if not isinstance(raw_results, list):
            raise MovieMetadataUnavailable("TMDB returned malformed search results")

        results: list[MovieSearchResult] = []
        for raw in raw_results:
            if not isinstance(raw, dict):
                continue
            external_id = _external_id(raw.get("id"))
            title = _text(raw.get("title"))
            if not external_id or not title:
                continue
            results.append(MovieSearchResult(
                metadata_provider=self.PROVIDER_NAME,
                external_id=external_id,
                title=title,
                original_title=_text(raw.get("original_title")),
                year=_year_from_date(raw.get("release_date")),
                description=_text(raw.get("overview")),
                external_rating=_rating(raw.get("vote_average")),
            ))
        return results

    async def get_movie_details(self, external_id: str) -> MovieMetadata:
        payload = await self._request(f"/movie/{external_id}", params={"language": "ru-RU"})
        movie_id = _external_id(payload.get("id"))
        title = _text(payload.get("title"))
        if not movie_id or not title:
            raise MovieMetadataUnavailable("TMDB returned malformed movie details")
        raw_genres = payload.get("genres")
        genres = tuple(
            name
            for item in raw_genres if isinstance(item, dict) and (name := _text(item.get("name")))
        ) if isinstance(raw_genres, list) else ()
        return MovieMetadata(
            metadata_provider=self.PROVIDER_NAME,
            external_id=movie_id,
            title=title,
            original_title=_text(payload.get("original_title")),
            year=_year_from_date(payload.get("release_date")),
            genres=genres,
            description=_text(payload.get("overview")),
            external_rating=_rating(payload.get("vote_average")),
        )

    async def _request(self, path: str, *, params: dict[str, str]) -> dict[str, Any]:
        if not self._token:
            raise MovieMetadataUnavailable("TMDB token is not configured")
        headers = {"Authorization": f"Bearer {self._token}", "accept": "application/json"}
        try:
            if self._client is not None:
                response = await self._client.get(path, params=params, headers=headers, timeout=self._timeout)
            else:
                async with httpx.AsyncClient(base_url=self.BASE_URL) as client:
                    response = await client.get(path, params=params, headers=headers, timeout=self._timeout)
        except httpx.HTTPError as error:
            logger.warning("TMDB request failed for %s: %s", path, type(error).__name__)
            raise MovieMetadataUnavailable("TMDB request failed") from error

        if response.status_code == 404:
            raise MovieMetadataNotFound("Movie is no longer available at TMDB")
        if response.status_code >= 400:
            logger.warning("TMDB request returned HTTP %s for %s", response.status_code, path)
            raise MovieMetadataUnavailable(f"TMDB HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as error:
            raise MovieMetadataUnavailable("TMDB returned invalid JSON") from error
        if not isinstance(payload, dict):
            raise MovieMetadataUnavailable("TMDB returned an unexpected response")
        return payload


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _external_id(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return ""
    return str(value).strip()


def _year_from_date(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value).year
    except ValueError:
        return None


def _rating(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    rating = float(value)
    return rating if math.isfinite(rating) and rating > 0 else None
