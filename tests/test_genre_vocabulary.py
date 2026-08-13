import asyncio

import httpx
import pytest

from bot.services.film_recommendations import (
    RecommendationCandidate, RecommendationConstraints, build_film_preference_profile, rank_candidates,
)
from bot.services.genre_vocabulary import canonicalize_genre, canonicalize_genres, genre_display_label
from bot.services.movie_recommendation_service import MovieRecommendationService
from bot.services.nl_intent_decoder import normalize_recommendation_genres
from bot.services.tmdb_candidate_provider import TmdbCandidateProvider


@pytest.mark.parametrize(("source", "expected"), [
    ("боевик", "action"), ("приключения", "adventure"), ("мультфильм", "animation"),
    ("комедия", "comedy"), ("криминал", "crime"), ("документальный", "documentary"),
    ("драма", "drama"), ("семейный", "family"), ("фэнтези", "fantasy"),
    ("история", "history"), ("ужасы", "horror"), ("музыка", "music"),
    ("детектив", "mystery"), ("мелодрама", "romance"), ("романтика", "romance"),
    ("фантастика", "science_fiction"), ("научная фантастика", "science_fiction"),
    ("нф и фэнтези", "science_fiction_fantasy"), ("триллер", "thriller"),
    ("военный", "war"), ("вестерн", "western"),
    ("drama", "drama"), ("science_fiction", "science_fiction"), ("action", "action"),
    ("  Science--Fiction! ", "science_fiction"), ("СЕМЁЙНЫЙ!!!", "family"),
])
def test_genre_canonicalization(source, expected):
    assert canonicalize_genre(source) == expected


def test_genre_canonicalization_is_safe_and_deduplicates_aliases():
    assert canonicalize_genre("unknown") is None
    assert canonicalize_genre("ВСЁ") is None  # ё/е normalization is safe, not a guess.
    assert canonicalize_genres(["фантастика", "Science Fiction", "science_fiction", "?"]) == ("science_fiction",)
    assert normalize_recommendation_genres(["Комедию", "comedy", "unknown"]) == ["comedy"]


def _stored(status="watched", genres=None, reaction="like", added_by=None):
    item = {"status": status, "genres": genres or [], "media_type": "movie"}
    if reaction is not None:
        item["reactions"] = {"vova": reaction}
    if added_by:
        item["added_by"] = added_by
    return item


def _candidate(genres):
    return RecommendationCandidate("1", "movie", "Candidate", genres=tuple(genres), vote_count=1000)


def test_production_russian_profile_matches_english_candidate_and_localizes_reason():
    profile = build_film_preference_profile([
        _stored(genres=["драма", "приключения", "фантастика", "Science Fiction"]),
    ], "vova")
    assert set(profile.genres) == {"drama", "adventure", "science_fiction"}
    score = rank_candidates([_candidate(("drama", "adventure", "science_fiction"))], profile)[0]
    assert score.genre_fit > 0 and score.taste_score > 0
    assert any("приключения" in reason or "драма" in reason for reason in map(str.lower, score.explanation_reasons))
    assert all("science_fiction" not in reason for reason in score.explanation_reasons)


def test_dislike_unknown_and_owned_want_signals_remain_distinct():
    disliked = build_film_preference_profile([_stored(genres=["драма"], reaction="dislike")], "vova")
    assert rank_candidates([_candidate(("drama",))], disliked)[0].genre_fit < 0
    unknown = build_film_preference_profile([_stored(genres=["драма"], reaction=None)], "vova")
    assert rank_candidates([_candidate(("drama",))], unknown)[0].genre_fit == 0
    item = _stored("want", ["комедия"], reaction=None, added_by="Вова")
    vova = build_film_preference_profile([item], "vova")
    sasha = build_film_preference_profile([item], "sasha")
    weak = rank_candidates([_candidate(("comedy",))], vova)[0]
    assert weak.genre_fit > 0 and weak.genre_fit < build_film_preference_profile([_stored(genres=["комедия"])], "vova").genres["comedy"].score
    assert rank_candidates([_candidate(("comedy",))], sasha)[0].genre_fit == 0


def test_russian_profile_guides_tmdb_with_numeric_genres_and_ignores_unsupported():
    async def run():
        requests = []
        async def handler(request):
            requests.append(request)
            return httpx.Response(200, json={"results": []})
        films = [_stored(genres=["драма", "фантастика"]), _stored(genres=["драма"])]
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://test") as client:
            provider = TmdbCandidateProvider("token", client=client)
            await MovieRecommendationService(provider).recommend(films, actor="vova", constraints=RecommendationConstraints(media_type="movie"))
            await provider.discover_movies(RecommendationConstraints(include_genres=frozenset({"unsupported"})))
        guided = [r.url.params.get("with_genres") for r in requests]
        assert "18" in guided and "878" in guided
        assert guided[-1] is None
    asyncio.run(run())


def test_russian_display_never_leaks_machine_key():
    assert genre_display_label("science_fiction") == "фантастика"
    assert genre_display_label("crime") == "криминал"
