import asyncio

import httpx
import pytest

from bot.services.movie_metadata import MediaMetadataUnavailable
from bot.services.tmdb_movie_metadata import TmdbMovieMetadataProvider


def run(coro):
    return asyncio.run(coro)


def test_tmdb_combined_search_and_details_are_normalized() -> None:
    paths = []

    async def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        assert request.headers["authorization"] == "Bearer secret"
        assert request.url.params["language"] == "ru-RU"
        if request.url.path.endswith("/search/movie"):
            assert request.url.params["region"] == "RU"
            return httpx.Response(200, json={"results": [{
                "id": 13, "title": "Офис", "original_title": "The Office",
                "release_date": "2015-01-01", "overview": "Фильм", "vote_average": 6.2,
            }]})
        if request.url.path.endswith("/search/tv"):
            assert "region" not in request.url.params
            return httpx.Response(200, json={"results": [{
                "id": 2316, "name": "Офис", "original_name": "The Office",
                "first_air_date": "2005-03-24", "overview": "Русское описание сериала", "vote_average": 8.6,
            }]})
        if request.url.path.endswith("/movie/13"):
            return httpx.Response(200, json={
                "id": 13, "title": "Офис", "original_title": "The Office", "release_date": "2015-01-01",
                "overview": "Фильм", "vote_average": 6.2, "genres": [{"name": "Комедия"}],
            })
        return httpx.Response(200, json={
            "id": 2316, "name": "Офис", "original_name": "The Office", "first_air_date": "2005-03-24",
            "overview": "Русское описание сериала", "vote_average": 8.6,
            "genres": [{"name": "Комедия"}, {"name": "Драма"}],
        })

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.test") as client:
            provider = TmdbMovieMetadataProvider("secret", client=client)
            results = await provider.search_titles("Офис")
            movie = await provider.get_title_details("movie", "13")
            tv = await provider.get_title_details("tv", "2316")
            return results, movie, tv

    results, movie, tv = run(scenario())
    assert [(item.media_type, item.year) for item in results] == [("movie", 2015), ("tv", 2005)]
    assert movie.genres == ("Комедия",)
    assert tv.genres == ("Комедия", "Драма")
    assert tv.description == "Русское описание сериала"
    assert tv.external_rating == 8.6
    assert "/movie/13" in paths and "/tv/2316" in paths


def test_tmdb_partial_search_failures_return_incomplete_successes() -> None:
    async def scenario(failing_path):
        def handler(request):
            if request.url.path.endswith(failing_path):
                return httpx.Response(503)
            key = "title" if request.url.path.endswith("movie") else "name"
            return httpx.Response(200, json={"results": [{"id": 1, key: "Найдено"}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.test") as client:
            return await TmdbMovieMetadataProvider("secret", client=client).search_titles("Найдено")

    movie_failed = run(scenario("/search/movie"))
    tv_failed = run(scenario("/search/tv"))
    assert movie_failed.complete is False and [item.media_type for item in movie_failed] == ["tv"]
    assert tv_failed.complete is False and [item.media_type for item in tv_failed] == ["movie"]


def test_combined_order_interleaves_endpoint_rank_deterministically() -> None:
    def handler(request):
        if request.url.path.endswith("/search/movie"):
            return httpx.Response(200, json={"results": [
                {"id": 1, "title": "Movie one"}, {"id": 2, "title": "Movie two"},
            ]})
        return httpx.Response(200, json={"results": [
            {"id": 1, "name": "TV one"}, {"id": 2, "name": "TV two"},
        ]})

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.test") as client:
            return await TmdbMovieMetadataProvider("secret", client=client).search_titles("unmatched")

    results = run(scenario())
    assert [(item.media_type, item.external_id) for item in results] == [
        ("movie", "1"), ("tv", "1"), ("movie", "2"), ("tv", "2"),
    ]


def test_tmdb_both_searches_fail() -> None:
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(503)), base_url="https://api.test") as client:
            with pytest.raises(MediaMetadataUnavailable):
                await TmdbMovieMetadataProvider("secret", client=client).search_titles("Фильм")
    run(scenario())


def test_invalid_media_type_is_rejected_without_request() -> None:
    calls = 0
    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={})

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.test") as client:
            with pytest.raises(MediaMetadataUnavailable):
                await TmdbMovieMetadataProvider("secret", client=client).get_title_details("show", "1")
    run(scenario())
    assert calls == 0


def test_compatibility_movie_wrappers_and_malformed_payload() -> None:
    responses = iter([
        httpx.Response(200, json={"results": [{"id": 1, "title": "Фильм", "release_date": "", "vote_average": 0}]}),
        httpx.Response(200, json=[]),
    ])

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: next(responses)), base_url="https://api.test") as client:
            provider = TmdbMovieMetadataProvider("secret", client=client)
            result = (await provider.search_movies("Фильм"))[0]
            assert result.media_type == "movie" and result.year is None and result.external_rating is None
            with pytest.raises(MediaMetadataUnavailable):
                await provider.get_movie_details("1")
    run(scenario())
