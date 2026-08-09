from __future__ import annotations

import asyncio
import logging
import math
import re
import unicodedata
from datetime import date
from typing import Any

import httpx

from bot.services.movie_metadata import (
    MEDIA_TYPE_MOVIE,
    MEDIA_TYPE_TV,
    MEDIA_TYPES,
    MediaMetadata,
    MediaSearchResult,
    MediaSearchResults,
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

    async def search_titles(self, query: str) -> list[MediaSearchResult]:
        searches = await asyncio.gather(
            self._search_catalog(MEDIA_TYPE_MOVIE, query),
            self._search_catalog(MEDIA_TYPE_TV, query),
            return_exceptions=True,
        )
        successful: list[list[MediaSearchResult]] = []
        complete = True
        for media_type, value in zip((MEDIA_TYPE_MOVIE, MEDIA_TYPE_TV), searches):
            if isinstance(value, BaseException):
                if not isinstance(value, Exception):
                    raise value
                complete = False
                logger.warning("TMDB %s catalog search failed: %s", media_type, type(value).__name__)
            else:
                successful.append(value)
        if not successful:
            raise MovieMetadataUnavailable("Both TMDB catalog searches failed")

        normalized_query = _normalize_title(query)
        ranked: list[tuple[tuple[Any, ...], MediaSearchResult]] = []
        for catalog in successful:
            for rank, result in enumerate(catalog):
                exact = normalized_query in {_normalize_title(result.title), _normalize_title(result.original_title)}
                type_order = 0 if result.media_type == MEDIA_TYPE_MOVIE else 1
                key = (0 if exact else 1, rank, type_order, _normalize_title(result.title), result.year or 0, result.external_id)
                ranked.append((key, result))
        ranked.sort(key=lambda item: item[0])
        return MediaSearchResults((result for _, result in ranked), complete=complete)

    async def _search_catalog(self, media_type: str, query: str) -> list[MediaSearchResult]:
        is_movie = media_type == MEDIA_TYPE_MOVIE
        params = {"query": query, "language": "ru-RU", "include_adult": "false"}
        if is_movie:
            params["region"] = "RU"
        payload = await self._request(f"/search/{media_type}", params=params)
        raw_results = payload.get("results")
        if not isinstance(raw_results, list):
            raise MovieMetadataUnavailable("TMDB returned malformed search results")

        results: list[MediaSearchResult] = []
        for raw in raw_results:
            if not isinstance(raw, dict):
                continue
            external_id = _external_id(raw.get("id"))
            title = _text(raw.get("title" if is_movie else "name"))
            if not external_id or not title:
                continue
            results.append(MediaSearchResult(
                metadata_provider=self.PROVIDER_NAME,
                external_id=external_id,
                title=title,
                original_title=_text(raw.get("original_title" if is_movie else "original_name")),
                year=_year_from_date(raw.get("release_date" if is_movie else "first_air_date")),
                description=_text(raw.get("overview")),
                external_rating=_rating(raw.get("vote_average")),
                media_type=media_type,
            ))
        return results

    async def get_title_details(self, media_type: str, external_id: str) -> MediaMetadata:
        if media_type not in MEDIA_TYPES:
            raise MovieMetadataUnavailable("Unsupported media type")
        is_movie = media_type == MEDIA_TYPE_MOVIE
        payload = await self._request(f"/{media_type}/{external_id}", params={"language": "ru-RU"})
        title_id = _external_id(payload.get("id"))
        title = _text(payload.get("title" if is_movie else "name"))
        if not title_id or not title:
            raise MovieMetadataUnavailable("TMDB returned malformed title details")
        raw_genres = payload.get("genres")
        genres = tuple(
            name
            for item in raw_genres if isinstance(item, dict) and (name := _text(item.get("name")))
        ) if isinstance(raw_genres, list) else ()
        return MediaMetadata(
            metadata_provider=self.PROVIDER_NAME,
            external_id=title_id,
            title=title,
            original_title=_text(payload.get("original_title" if is_movie else "original_name")),
            year=_year_from_date(payload.get("release_date" if is_movie else "first_air_date")),
            genres=genres,
            description=_text(payload.get("overview")),
            external_rating=_rating(payload.get("vote_average")),
            media_type=media_type,
        )

    async def search_movies(self, query: str) -> list[MovieSearchResult]:
        return await self._search_catalog(MEDIA_TYPE_MOVIE, query)

    async def get_movie_details(self, external_id: str) -> MovieMetadata:
        return await self.get_title_details(MEDIA_TYPE_MOVIE, external_id)

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
            raise MovieMetadataNotFound("Title is no longer available at TMDB")
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


def _normalize_title(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    value = unicodedata.normalize("NFKC", value).strip().casefold().replace("ё", "е")
    return re.sub(r"\W+", " ", value, flags=re.UNICODE).strip()
