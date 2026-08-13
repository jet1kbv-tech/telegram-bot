import asyncio
import httpx
import pytest

from bot.services.film_recommendations import RecommendationConstraints
from bot.services.movie_metadata import MovieMetadataUnavailable
from bot.services.tmdb_candidate_provider import TmdbCandidateProvider


def response(data, status=200):
    return httpx.Response(status, json=data)


def test_movie_tv_discovery_pagination_dedup_and_cache():
    async def run():
        calls = []
        async def handler(request):
            calls.append(request)
            media = request.url.path.rsplit("/", 1)[-1]
            item = {"id": 1, "title": "Film", "release_date": "2020-01-01", "genre_ids": [35], "vote_average": 8, "vote_count": 500, "popularity": 10} if media == "movie" else {"id": 2, "name": "Show", "first_air_date": "2021-01-01", "genre_ids": [18], "vote_average": 7, "vote_count": 400, "popularity": 9}
            return response({"results": [item]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://test") as client:
            provider = TmdbCandidateProvider("token", client=client, max_pages=2)
            movies = await provider.discover_movies(RecommendationConstraints(), pages=9)
            assert len(movies) == 1 and len(calls) == 2
            await provider.discover_movies(RecommendationConstraints(), pages=9)
            assert len(calls) == 2
            shows = await provider.discover_tv(RecommendationConstraints(), pages=1)
            assert shows[0].media_type == "tv"
    asyncio.run(run())

@pytest.mark.parametrize("kind", ["timeout", "429", "500", "bad_json", "malformed"])
def test_provider_failures(kind):
    async def run():
        async def handler(request):
            if kind == "timeout": raise httpx.ReadTimeout("late")
            if kind in {"429", "500"}: return response({}, int(kind))
            if kind == "bad_json": return httpx.Response(200, content=b"{")
            return response({"results": {}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://test") as client:
            with pytest.raises(MovieMetadataUnavailable):
                await TmdbCandidateProvider("token", client=client).discover_movies(RecommendationConstraints())
    asyncio.run(run())

def test_missing_configuration_and_details():
    async def run():
        with pytest.raises(MovieMetadataUnavailable):
            await TmdbCandidateProvider("").discover_movies(RecommendationConstraints())
        async def handler(request):
            return response({"id": 3, "title": "Film", "release_date": "2022-01-01", "genres": [{"id": 1, "name": "Drama"}], "runtime": 95, "vote_count": 200})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://test") as client:
            item = await TmdbCandidateProvider("token", client=client).get_details("movie", "3")
        assert item.runtime_minutes == 95 and item.genres == ("Drama",)
    asyncio.run(run())


def test_quality_sort_and_start_page_are_cache_keys():
    async def run():
        calls = []
        async def handler(request):
            calls.append(request)
            return response({"results": []})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://test") as client:
            provider = TmdbCandidateProvider("token", client=client)
            await provider.discover_movies(RecommendationConstraints(min_vote_count=500), start_page=3, sort_by="vote_average.desc")
            await provider.discover_movies(RecommendationConstraints(min_vote_count=500), start_page=3, sort_by="vote_average.desc")
        assert len(calls) == 1
        assert calls[0].url.params["page"] == "3" and calls[0].url.params["sort_by"] == "vote_average.desc"
        assert calls[0].url.params["vote_count.gte"] == "500"
    asyncio.run(run())
