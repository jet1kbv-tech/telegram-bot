from copy import deepcopy

import pytest

from bot.handlers import film_operations
from bot.keyboards.common import item_keyboard
from bot.storage import JsonStorage, normalize_film
from bot.ui.common import build_item_text


def callbacks(markup):
    return [button.callback_data for row in markup.inline_keyboard for button in row]


def test_normalization_supports_legacy_one_both_and_rejects_invalid_reactions():
    legacy = normalize_film({"id": "a", "title": "Legacy", "rating": 7})
    one = normalize_film({"title": "One", "reactions": {"vova": "like"}})
    both = normalize_film({"title": "Both", "reactions": {"vova": "dislike", "sasha": "neutral"}})
    invalid = normalize_film({"title": "Bad", "reactions": {"vova": "excellent", "hacker": "like", "sasha": "like"}})
    assert "reactions" not in legacy
    assert legacy["rating"] == legacy["legacy_rating"] == 7
    assert one["reactions"] == {"vova": "like"}
    assert both["reactions"] == {"vova": "dislike", "sasha": "neutral"}
    assert invalid["reactions"] == {"sasha": "like"}


def test_reaction_domain_is_atomic_isolated_replace_idempotent_and_clear(monkeypatch, tmp_path):
    store = JsonStorage(tmp_path / "data.json")
    data = store.default_data()
    original = {"id": "f", "title": "Film", "status": "watched", "genres": ["Drama"], "year": 2020}
    data["films"] = [original]
    store.save(data)
    monkeypatch.setattr(film_operations, "storage", store)

    assert film_operations.set_film_reaction("f", "vova", "like").found
    assert film_operations.set_film_reaction("f", "sasha", "neutral").found
    assert film_operations.set_film_reaction("f", "vova", "dislike").film["reactions"] == {
        "vova": "dislike", "sasha": "neutral"
    }
    before = deepcopy(store.load())
    assert film_operations.set_film_reaction("f", "vova", "dislike").found
    assert store.load() == before
    result = film_operations.clear_film_reaction("f", "vova")
    assert result.film["reactions"] == {"sasha": "neutral"}
    assert result.film["status"] == "watched"
    assert result.film["genres"] == ["Drama"]
    assert not film_operations.set_film_reaction("missing", "vova", "like").found

    with pytest.raises(ValueError):
        film_operations.set_film_reaction("f", "other", "like")
    with pytest.raises(ValueError):
        film_operations.set_film_reaction("f", "vova", "ten")
    with pytest.raises(ValueError):
        film_operations.clear_film_reaction("f", "other")


def test_watched_card_and_actor_controls_are_compact_but_want_has_none():
    watched = {"id": "f", "title": "Film", "status": "watched", "reactions": {"vova": "like"}}
    text = build_item_text("films", watched)
    assert "Оценки:" in text
    assert "Вова: ❤️ Понравилось" in text
    assert "Саша: —" in text
    watched_callbacks = callbacks(item_keyboard("films", watched, 0, status_filter="watched", actor_key="vova"))
    assert "film_reaction|f|like|watched|0" in watched_callbacks
    assert "film_reaction|f|clear|watched|0" in watched_callbacks

    want = {**watched, "status": "want"}
    assert "Оценки:" not in build_item_text("films", want)
    assert not any(value.startswith("film_reaction|") for value in callbacks(
        item_keyboard("films", want, 0, status_filter="want", actor_key="vova")
    ))
