import asyncio

import httpx
import pytest

from bot.services.movie_metadata import MovieMetadataUnavailable
from bot.services.tmdb_movie_metadata import TmdbMovieMetadataProvider


def test_tmdb_search_and_details_are_normalized() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer secret"
        if request.url.path.endswith("/search/movie"):
            assert request.url.params["language"] == "ru-RU"
            return httpx.Response(200, json={"results": [{
                "id": 13, "title": "Игры разума", "original_title": "A Beautiful Mind",
                "release_date": "2001-12-11", "overview": "Описание", "vote_average": 8.2,
            }]})
        return httpx.Response(200, json={
            "id": 13, "title": "Игры разума", "original_title": "A Beautiful Mind",
            "release_date": "2001-12-11", "overview": "Описание", "vote_average": 8.2,
            "genres": [{"id": 18, "name": "Драма"}, {"id": 36, "name": "История"}],
        })

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.test") as client:
            provider = TmdbMovieMetadataProvider("secret", client=client)
            results = await provider.search_movies("Игры разума")
            metadata = await provider.get_movie_details(results[0].external_id)
        assert results[0].year == 2001
        assert metadata.external_id == "13"
        assert metadata.genres == ("Драма", "История")
        assert metadata.external_rating == 8.2

    asyncio.run(scenario())


def test_tmdb_missing_fields_and_malformed_payload() -> None:
    responses = iter([
        httpx.Response(200, json={"results": [{"id": 1, "title": "Фильм", "release_date": "", "vote_average": 0}]}),
        httpx.Response(200, json=[]),
    ])

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: next(responses)), base_url="https://api.test") as client:
            provider = TmdbMovieMetadataProvider("secret", client=client)
            result = (await provider.search_movies("Фильм"))[0]
            assert result.year is None
            assert result.external_rating is None
            with pytest.raises(MovieMetadataUnavailable):
                await provider.get_movie_details("1")

    asyncio.run(scenario())
