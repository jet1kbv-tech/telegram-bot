import asyncio

from bot.services.film_recommendations import RecommendationCandidate, RecommendationConstraints
from bot.services.movie_recommendation_service import MAX_DISCOVERY_REQUESTS, MovieRecommendationService


class Provider:
    def __init__(self):
        self.calls = []

    async def discover_movies(self, constraints, **kwargs):
        return self._items("movie", constraints, kwargs)

    async def discover_tv(self, constraints, **kwargs):
        return self._items("tv", constraints, kwargs)

    def _items(self, media_type, constraints, kwargs):
        self.calls.append((media_type, constraints, kwargs))
        page = kwargs["start_page"]
        genre = next(iter(constraints.include_genres), "comedy")
        # Deliberately repeat one identity across diversified pools.
        ids = [f"{media_type}-{page}-shared", f"{media_type}-{page}-{len(self.calls)}"]
        return [RecommendationCandidate(identifier, media_type, identifier, genres=(genre,),
                    external_rating=8, vote_count=1000, popularity=100) for identifier in ids]


def test_broad_discovery_is_bounded_diversified_deduplicated_and_more_pages():
    async def run():
        provider = Provider()
        films = [{"status": "watched", "genres": ["fantasy"], "media_type": "movie", "reactions": {"vova": "like"}},
                 {"status": "want", "genres": ["comedy"], "media_type": "movie", "added_by": "Вова"}]
        service = MovieRecommendationService(provider)
        first = await service.recommend(films, actor="vova", constraints=RecommendationConstraints(limit=20))
        assert len(provider.calls) <= MAX_DISCOVERY_REQUESTS
        assert {call[2]["sort_by"] for call in provider.calls} == {"popularity.desc", "vote_average.desc"}
        assert {next(iter(call[1].include_genres), None) for call in provider.calls} >= {None, "fantasy", "comedy"}
        identities = {(x.candidate.provider, x.candidate.media_type, x.candidate.external_id) for x in first}
        more = await service.recommend(films, actor="vova", constraints=RecommendationConstraints(limit=20),
                                       shown=identities, generation=1)
        assert identities.isdisjoint({(x.candidate.provider, x.candidate.media_type, x.candidate.external_id) for x in more})
        assert all(call[2]["start_page"] == 2 for call in provider.calls[-MAX_DISCOVERY_REQUESTS:])
    asyncio.run(run())


def test_explicit_genre_does_not_inject_profile_genres():
    async def run():
        provider = Provider()
        films = [{"status": "watched", "genres": ["fantasy"], "reactions": {"vova": "like"}}]
        await MovieRecommendationService(provider).recommend(
            films, actor="vova", constraints=RecommendationConstraints(media_type="movie", include_genres=frozenset({"horror"})))
        assert len(provider.calls) == 1
        assert all(call[1].include_genres == frozenset({"horror"}) for call in provider.calls)
    asyncio.run(run())
