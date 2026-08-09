import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.handlers import films
from bot.keyboards.common import item_keyboard
from bot.services.movie_metadata import MovieMetadata, MovieSearchResult
from bot.states import ADDING_FILM_COMMENT, CONFIRMING_FILM_ADD, SECTION, SELECTING_FILM_METADATA
from bot.storage import JsonStorage, normalize_film
from bot.ui.common import build_item_text


def test_normalize_film_adds_metadata_defaults_and_retains_ratings() -> None:
    film = normalize_film({
        "id": "film-1",
        "title": "Arrival",
        "status": "watched",
        "sasha_rating": 8,
        "vova_rating": 9,
        "rating": 7,
    })

    assert film is not None
    assert film["sasha_rating"] == 8
    assert film["vova_rating"] == 9
    assert film["rating"] == 7
    assert film["external_id"] == ""
    assert film["localized_title"] == ""
    assert film["year"] is None
    assert film["genres"] == []
    assert film["description"] == ""
    assert film["external_rating"] is None


def test_normalize_film_validates_optional_metadata() -> None:
    film = normalize_film({
        "title": "Arrival",
        "external_id": 123,
        "year": "2016",
        "genres": ["Drama", 4, "Sci-Fi"],
        "description": None,
        "external_rating": float("nan"),
    })

    assert film is not None
    assert film["external_id"] == "123"
    assert film["year"] is None
    assert film["genres"] == ["Drama", "Sci-Fi"]
    assert film["description"] == ""
    assert film["external_rating"] is None


def test_storage_round_trip_preserves_legacy_and_metadata(tmp_path: Path) -> None:
    store = JsonStorage(tmp_path / "data.json")
    data = store.default_data()
    data["films"] = [{"title": "Old film", "status": "watched", "rating": 6}]

    store.save(data)
    film = store.load()["films"][0]

    assert film["rating"] == 6
    assert film["legacy_rating"] == 6
    assert film["external_id"] == ""
    assert film["genres"] == []


def test_film_card_and_status_button_have_no_personal_rating_ui() -> None:
    film = {
        "id": "film-1",
        "title": "Arrival",
        "status": "watched",
        "added_by": "user",
        "comment": "Great",
        "sasha_rating": 8,
        "vova_rating": 9,
        "legacy_rating": 7,
    }

    text = build_item_text("films", film)
    keyboard = item_keyboard("films", {**film, "status": "want"}, 0, status_filter="want")
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]

    assert "Оценка" not in text
    assert "рейтинг" not in text.lower()
    assert "status|films|film-1|watched|want|0" in callbacks
    assert not any(callback.startswith("rate_start|") for callback in callbacks)


class FakeStorage:
    def __init__(self, films=None):
        self.data = {"films": list(films or [])}
        self.update_calls = 0

    def load(self):
        return self.data

    def update(self, mutator):
        self.update_calls += 1
        result = mutator(self.data)
        return result, self.data


class FakeProvider:
    async def search_movies(self, query):
        return [MovieSearchResult("tmdb", "13", "Игры разума", "A Beautiful Mind", 2001)]

    async def get_movie_details(self, external_id):
        return MovieMetadata("tmdb", external_id, "Игры разума", "A Beautiful Mind", 2001, ("Драма",), "Описание", 8.2)


def configure(monkeypatch, store, provider=None):
    monkeypatch.setattr(films, "storage", store)
    safe_edit = AsyncMock()
    films.configure_films_handlers(
        safe_edit_message=safe_edit,
        build_item_text=lambda section, item: item["title"],
        item_keyboard=lambda *args, **kwargs: None,
        main_menu_keyboard=lambda: None,
        metadata_provider=provider,
    )
    monkeypatch.setattr(films, "ensure_access", AsyncMock(return_value=True))
    monkeypatch.setattr(films, "get_user_name", lambda update: "user")
    return safe_edit


def callback_update(data):
    query = SimpleNamespace(data=data, answer=AsyncMock(), edit_message_text=AsyncMock())
    return SimpleNamespace(callback_query=query)


def test_comment_is_draft_only_and_returns_to_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    store = FakeStorage()
    configure(monkeypatch, store)
    candidate = MovieMetadata.manual("Arrival")
    message = SimpleNamespace(text="Great", reply_text=AsyncMock())
    update = SimpleNamespace(message=message)
    context = SimpleNamespace(user_data={films.FILM_DRAFT_KEY: {"query": "Arrival", "results": [], "candidate": candidate, "comment": ""}})

    result = asyncio.run(films.add_film_comment(update, context))

    assert result == CONFIRMING_FILM_ADD
    assert store.data["films"] == []
    assert store.update_calls == 0
    assert context.user_data[films.FILM_DRAFT_KEY]["comment"] == "Great"


def test_provider_creation_requires_save_and_preserves_success_buttons(monkeypatch: pytest.MonkeyPatch) -> None:
    store = FakeStorage()
    safe_edit = configure(monkeypatch, store, FakeProvider())
    message = SimpleNamespace(text="Игры разума", reply_text=AsyncMock())
    update = SimpleNamespace(message=message)
    context = SimpleNamespace(user_data={})

    result = asyncio.run(films.add_film_title(update, context))
    assert result == CONFIRMING_FILM_ADD
    assert store.data["films"] == []

    result = asyncio.run(films.film_metadata_callback_router(callback_update("filmmeta:save"), context))
    assert result == SECTION
    assert len(store.data["films"]) == 1
    created = store.data["films"][0]
    assert created["metadata_provider"] == "tmdb"
    assert created["external_id"] == "13"
    assert created["genres"] == ["Драма"]
    markup = safe_edit.await_args.kwargs["reply_markup"]
    assert [(button.text, button.callback_data) for row in markup.inline_keyboard for button in row] == [
        ("➕ Добавить ещё", "add|films"),
        ("🎬 К фильмам", "menu|films"),
        ("🏠 В меню", "menu:main"),
    ]


def test_missing_provider_offers_manual_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    store = FakeStorage()
    configure(monkeypatch, store)
    context = SimpleNamespace(user_data={})
    message = SimpleNamespace(text="Arrival", reply_text=AsyncMock())
    result = asyncio.run(films.add_film_title(SimpleNamespace(message=message), context))
    assert result == SELECTING_FILM_METADATA
    callbacks = [button.callback_data for row in message.reply_text.await_args.kwargs["reply_markup"].inline_keyboard for button in row]
    assert "filmmeta:manual" in callbacks
    assert store.data["films"] == []


def test_duplicate_appearing_before_save_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    store = FakeStorage()
    safe_edit = configure(monkeypatch, store)
    candidate = MovieMetadata("tmdb", "13", "Arrival", year=2016)
    context = SimpleNamespace(user_data={films.FILM_DRAFT_KEY: {"query": "Arrival", "results": [], "candidate": candidate, "comment": "", "possible_duplicate_id": ""}})
    store.data["films"].append({"id": "new", "title": "Arrival", "year": 2016, "metadata_provider": "tmdb", "external_id": "13", "status": "watched"})

    result = asyncio.run(films.film_metadata_callback_router(callback_update("filmmeta:save"), context))
    assert result == CONFIRMING_FILM_ADD
    assert len(store.data["films"]) == 1
    assert "уже есть" in safe_edit.await_args.args[1]


def test_double_click_and_stale_callback_do_not_append_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    store = FakeStorage()
    safe_edit = configure(monkeypatch, store)
    candidate = MovieMetadata("tmdb", "13", "Arrival", year=2016)
    context = SimpleNamespace(user_data={films.FILM_DRAFT_KEY: {"query": "Arrival", "results": [], "candidate": candidate, "comment": "", "possible_duplicate_id": ""}})

    asyncio.run(films.film_metadata_callback_router(callback_update("filmmeta:save"), context))
    asyncio.run(films.film_metadata_callback_router(callback_update("filmmeta:save"), context))
    assert len(store.data["films"]) == 1
    assert "устарел" in safe_edit.await_args.args[1]


def test_stale_result_selection_is_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    store = FakeStorage()
    safe_edit = configure(monkeypatch, store, FakeProvider())
    context = SimpleNamespace(user_data={films.FILM_DRAFT_KEY: {"query": "Arrival", "results": []}})
    result = asyncio.run(films.film_metadata_callback_router(callback_update("filmmeta:select:7"), context))
    assert result == SECTION
    assert "устарел" in safe_edit.await_args.args[1]


def test_possible_duplicate_requires_exact_override(monkeypatch: pytest.MonkeyPatch) -> None:
    existing = {"id": "legacy", "title": "Arrival", "year": None, "external_id": "", "status": "want"}
    store = FakeStorage([existing])
    configure(monkeypatch, store)
    candidate = MovieMetadata("tmdb", "13", "Arrival", year=2016)
    draft = {"query": "Arrival", "results": [], "candidate": candidate, "comment": "", "possible_duplicate_id": ""}
    context = SimpleNamespace(user_data={films.FILM_DRAFT_KEY: draft})

    asyncio.run(films.film_metadata_callback_router(callback_update("filmmeta:save"), context))
    assert len(store.data["films"]) == 1

    asyncio.run(films.film_metadata_callback_router(callback_update("filmmeta:force:legacy"), context))
    asyncio.run(films.film_metadata_callback_router(callback_update("filmmeta:save"), context))
    assert len(store.data["films"]) == 2
