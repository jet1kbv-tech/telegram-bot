import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.handlers import films
from bot.keyboards.common import item_keyboard
from bot.services.movie_metadata import MediaSearchResults, MovieMetadata, MovieSearchResult
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
    assert film["media_type"] == ""
    assert film["localized_title"] == ""
    assert film["year"] is None
    assert film["genres"] == []
    assert film["description"] == ""
    assert film["external_rating"] is None


def test_historical_tmdb_linked_type_normalization_is_movie_only() -> None:
    historical = normalize_film({"title": "Old", "metadata_provider": "tmdb", "external_id": "13"})
    other = normalize_film({"title": "Other", "metadata_provider": "other", "external_id": "13"})
    assert historical["media_type"] == "movie"
    assert other["media_type"] == ""


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
    async def search_titles(self, query):
        return [MovieSearchResult("tmdb", "13", "Игры разума", "A Beautiful Mind", 2001, media_type="movie")]

    async def get_title_details(self, media_type, external_id):
        return MovieMetadata("tmdb", external_id, "Игры разума", "A Beautiful Mind", 2001, ("Драма",), "Описание", 8.2, media_type)


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
    assert created["media_type"] == "movie"
    assert created["genres"] == ["Драма"]
    assert created["title"] == "Игры разума"
    assert created["localized_title"] == "Игры разума"
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
    candidate = MovieMetadata("tmdb", "13", "Arrival", year=2016, media_type="movie")
    context = SimpleNamespace(user_data={films.FILM_DRAFT_KEY: {"query": "Arrival", "results": [], "candidate": candidate, "comment": "", "possible_duplicate_id": ""}})
    store.data["films"].append({"id": "new", "title": "Arrival", "year": 2016, "metadata_provider": "tmdb", "media_type": "movie", "external_id": "13", "status": "watched"})

    result = asyncio.run(films.film_metadata_callback_router(callback_update("filmmeta:save"), context))
    assert result == CONFIRMING_FILM_ADD
    assert len(store.data["films"]) == 1
    assert "уже есть" in safe_edit.await_args.args[1]


def test_double_click_and_stale_callback_do_not_append_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    store = FakeStorage()
    safe_edit = configure(monkeypatch, store)
    candidate = MovieMetadata("tmdb", "13", "Arrival", year=2016, media_type="movie")
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


class ResultsProvider:
    def __init__(self, results, *, complete=True):
        self.results = MediaSearchResults(results, complete=complete)

    async def search_titles(self, query):
        return self.results

    async def get_title_details(self, media_type, external_id):
        selected = next(item for item in self.results if item.external_id == external_id and item.media_type == media_type)
        return MovieMetadata("tmdb", external_id, selected.title, selected.original_title, selected.year, ("Драма",), "Описание", 8.0, media_type)


def search_result(media_type, title, external_id="1", original_title="", year=2000):
    return MovieSearchResult("tmdb", external_id, title, original_title, year, media_type=media_type)


def test_exact_tv_auto_opens_and_persists_typed_primary_title(monkeypatch) -> None:
    provider = ResultsProvider([search_result("tv", "Властелин колец: Кольца власти", "tv1", year=2022)])
    store = FakeStorage()
    configure(monkeypatch, store, provider)
    message = SimpleNamespace(text="Властелин колец: Кольца власти", reply_text=AsyncMock())
    context = SimpleNamespace(user_data={})
    assert asyncio.run(films.add_film_title(SimpleNamespace(message=message), context)) == CONFIRMING_FILM_ADD
    asyncio.run(films.film_metadata_callback_router(callback_update("filmmeta:save"), context))
    assert store.data["films"][0]["media_type"] == "tv"


def test_user_query_remains_primary_and_localized_title_is_separate(monkeypatch) -> None:
    provider = ResultsProvider([search_result("movie", "Властелин колец: Братство Кольца", "m1", original_title="The Lord of the Rings")])
    store = FakeStorage()
    configure(monkeypatch, store, provider)
    message = SimpleNamespace(text="Властелин колец 1", reply_text=AsyncMock())
    context = SimpleNamespace(user_data={})
    assert asyncio.run(films.add_film_title(SimpleNamespace(message=message), context)) == SELECTING_FILM_METADATA
    asyncio.run(films.film_metadata_callback_router(callback_update("filmmeta:select:0"), context))
    asyncio.run(films.film_metadata_callback_router(callback_update("filmmeta:save"), context))
    created = store.data["films"][0]
    assert created["title"] == "Властелин колец 1"
    assert created["localized_title"] == "Властелин колец: Братство Кольца"


def test_movie_and_tv_exact_matches_require_selection_and_show_icons(monkeypatch) -> None:
    provider = ResultsProvider([search_result("movie", "Офис", "m"), search_result("tv", "Офис", "t")])
    store = FakeStorage()
    configure(monkeypatch, store, provider)
    message = SimpleNamespace(text="Офис", reply_text=AsyncMock())
    context = SimpleNamespace(user_data={})
    assert asyncio.run(films.add_film_title(SimpleNamespace(message=message), context)) == SELECTING_FILM_METADATA
    labels = [row[0].text for row in message.reply_text.await_args.kwargs["reply_markup"].inline_keyboard[:2]]
    assert labels == ["🎬 Офис · 2000", "📺 Офис · 2000"]


def test_fuzzy_single_and_incomplete_exact_do_not_auto_open(monkeypatch) -> None:
    for provider in (
        ResultsProvider([search_result("movie", "Другой")]),
        ResultsProvider([search_result("tv", "Офис")], complete=False),
    ):
        store = FakeStorage()
        configure(monkeypatch, store, provider)
        message = SimpleNamespace(text="Офис", reply_text=AsyncMock())
        assert asyncio.run(films.add_film_title(SimpleNamespace(message=message), SimpleNamespace(user_data={}))) == SELECTING_FILM_METADATA


def test_partial_title_preserves_provider_order_and_requires_selection(monkeypatch) -> None:
    provider = ResultsProvider([
        search_result("movie", "Ничего хорошего в отеле «Эль Рояль»", "el-royale", year=2018),
        search_result("movie", "Отель", "hotel", year=2022),
    ])
    store = FakeStorage()
    configure(monkeypatch, store, provider)
    message = SimpleNamespace(text="ничего хорошего в отеле", reply_text=AsyncMock())

    state = asyncio.run(films.begin_film_search(
        SimpleNamespace(effective_message=message), SimpleNamespace(user_data={}), "ничего хорошего в отеле",
    ))

    assert state == SELECTING_FILM_METADATA
    assert message.reply_text.await_args.args[0] == "Что именно добавить?"
    buttons = message.reply_text.await_args.kwargs["reply_markup"].inline_keyboard
    assert "Ничего хорошего в отеле «Эль Рояль»" in buttons[0][0].text
    assert buttons[0][0].callback_data == "filmmeta:select:0"
    assert store.data["films"] == []


def test_manual_fallback_persists_unknown_type(monkeypatch) -> None:
    store = FakeStorage()
    configure(monkeypatch, store)
    context = SimpleNamespace(user_data={films.FILM_DRAFT_KEY: {"query": "Неизвестное", "results": [], "candidate": None, "comment": "", "possible_duplicate_id": ""}})
    asyncio.run(films.film_metadata_callback_router(callback_update("filmmeta:manual"), context))
    asyncio.run(films.film_metadata_callback_router(callback_update("filmmeta:save"), context))
    assert store.data["films"][0]["media_type"] == ""
    assert store.data["films"][0]["localized_title"] == ""


def test_cards_show_known_type_and_omit_unknown_type() -> None:
    base = {"title": "Title", "status": "want", "added_by": "user", "genres": ["Драма"]}
    movie = build_item_text("films", {**base, "media_type": "movie", "year": 2000})
    tv = build_item_text("films", {**base, "media_type": "tv", "year": 2001})
    unknown = build_item_text("films", {**base, "media_type": "", "year": 2002})
    assert movie.startswith("🎬") and "Фильм · 2000" in movie
    assert tv.startswith("📺") and "Сериал · 2001" in tv
    assert "Фильм" not in unknown and "Сериал" not in unknown


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
