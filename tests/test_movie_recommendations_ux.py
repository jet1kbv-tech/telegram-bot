from pathlib import Path

from bot.keyboards.common import section_menu_keyboard
from bot.services import film_catalog
from bot.services.film_recommendations import RecommendationCandidate, RecommendationConstraints
from bot.handlers.film_recommendations import relax_constraints, recommendation_menu_keyboard
from bot.services.nl_intent import IntentKind
from bot.services.nl_intent_decoder import decode_intent, decode_provider_envelope, normalize_recommendation_genres
from bot.storage import JsonStorage


def test_films_menu_and_recommendation_start_menu_keep_backlog():
    labels = [row[0].text for row in section_menu_keyboard("films", unrated_watched_count=4).inline_keyboard]
    assert "✨ Что посмотреть?" in labels
    assert "⭐ Оценить просмотренные · 4" in labels
    callbacks = [row[0].callback_data for row in recommendation_menu_keyboard().inline_keyboard]
    assert callbacks == ["filmrec:actor:both", "filmrec:actor:self", "filmrec:want", "menu:main"]


def test_recommendation_decoder_normalizes_and_bounds():
    parsed = decode_provider_envelope('{"intent":"recommend_film","arguments":[{"name":"actor","value":"both"},{"name":"source","value":"want"},{"name":"media_type","value":"movie"},{"name":"include_genres","value":"комедия,unknown"},{"name":"exclude_genres","value":"романтика"},{"name":"min_year","value":"2020"},{"name":"max_runtime","value":"120"}]}')
    assert parsed.intent is IntentKind.RECOMMEND_FILM
    assert parsed.arguments["include_genres"] == ["comedy"]
    assert parsed.arguments["exclude_genres"] == ["romance"]
    assert parsed.arguments["min_year"] == 2020
    assert parsed.arguments["max_runtime"] == 120
    assert normalize_recommendation_genres(["ужасы", "nonsense"]) == ["horror"]


def test_recommendation_direct_contract_defaults_and_relaxation():
    args = {"actor": None, "source": None, "media_type": None, "include_genres": [], "exclude_genres": [],
            "min_year": None, "max_year": None, "min_rating": None, "max_runtime": None,
            "language": None, "country": None}
    assert decode_intent({"intent": "recommend_film", "arguments": args}).intent is IntentKind.RECOMMEND_FILM
    c = RecommendationConstraints(min_rating=8, max_runtime=100, min_year=2020)
    assert relax_constraints(c).min_rating is None
    assert relax_constraints(c).max_runtime == 100


def test_candidate_create_is_canonical_idempotent_and_has_no_reactions(tmp_path, monkeypatch):
    store = JsonStorage(Path(tmp_path / "data.json"))
    monkeypatch.setattr(film_catalog, "storage", store)
    candidate = RecommendationCandidate("42", "tv", "Название", "Original", 2024, ("comedy",),
                                        "Overview", 7.6, 1234)
    first = film_catalog.create_want_film(candidate, added_by="Вова")
    second = film_catalog.create_want_film(candidate, added_by="Саша")
    assert first.created is True and second.created is False
    assert first.film["status"] == "want" and first.film["media_type"] == "tv"
    assert first.film["external_id"] == "42" and first.film["added_by"] == "Вова"
    assert "reactions" not in first.film
    assert len(store.load()["films"]) == 1


def test_fallback_identity_blocks_duplicate_without_external_id(tmp_path, monkeypatch):
    store = JsonStorage(Path(tmp_path / "data.json")); monkeypatch.setattr(film_catalog, "storage", store)
    one = RecommendationCandidate("", "movie", "  Ёлки! ", year=2010, provider="")
    two = RecommendationCandidate("", "movie", "елки", year=2010, provider="")
    assert film_catalog.create_want_film(one, added_by="Вова").created
    assert not film_catalog.create_want_film(two, added_by="Вова").created
