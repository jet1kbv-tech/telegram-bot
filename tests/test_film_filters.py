import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock

import pytest

from bot.handlers import film_filters as filters
from bot.handlers.film_operations import FilmOperationResult
from bot.keyboards.common import section_menu_keyboard
from bot import runtime


def film(item_id, title, *, status="want", genres=None):
    return {"id": item_id, "title": title, "status": status, "genres": list(genres or [])}


class Store:
    def __init__(self, films):
        self.data = {"films": films}

    def load(self):
        return self.data


def callbacks(markup):
    return [button.callback_data for row in markup.inline_keyboard for button in row]


def update(data):
    return SimpleNamespace(callback_query=SimpleNamespace(data=data, answer=AsyncMock()))


def configured(monkeypatch, films):
    store = Store(films)
    safe_edit = AsyncMock()
    monkeypatch.setattr(filters, "storage", store)
    monkeypatch.setattr(filters, "ensure_access", AsyncMock(return_value=True))
    filters.configure_film_filter_handlers(
        safe_edit_message=safe_edit,
        build_item_text=lambda section, item: f"CARD:{item['title']}",
    )
    return store, safe_edit


def test_dynamic_want_counts_normalization_multigenre_and_genreless():
    films = [
        film("1", "A", genres=[" Драма ", "Комедия", "драма"]),
        film("2", "B", genres=["Комедия"]),
        film("3", "C", status="watched", genres=["Ужасы"]),
        film("4", "D"),
        film("5", "E", genres=["  "]),
        film("6", "F", genres=["Авторское   кино"]),
    ]
    before = deepcopy(films)

    entries = filters.collect_genres(films)

    assert [(entry.label, entry.count) for entry in entries] == [
        ("Комедия", 2), ("Авторское кино", 1), ("Драма", 1), ("Без жанра", 2),
    ]
    assert not any(entry.label == "Ужасы" for entry in entries)
    assert films == before


def test_exact_filtering_and_unicode_identity():
    entry = filters.GenreEntry(filters.genre_identity("Комедия"), "Комедия", 1, "key")
    films = [
        film("1", "yes", genres=[" Ｋомедия "]),
        film("2", "also", genres=["КОМЕДИЯ"]),
        film("3", "no", genres=["Комедия положений"]),
        film("4", "watched", status="watched", genres=["Комедия"]),
    ]
    # Cyrillic and Latin K remain distinct even under NFKC.
    assert [item["title"] for item in filters.filter_want_films(films, entry)] == ["also"]
    assert filters.normalize_genre_display(" Авторское   кино ") == "Авторское кино"


def test_selector_order_pagination_unknown_emoji_and_callbacks_fit():
    entries = [filters.GenreEntry(str(index), f"Custom {index}", 20 - index, filters.genre_key(str(index))) for index in range(12)]
    entries.append(filters.GenreEntry(None, "Без жанра", 99, filters.GENRELESS_KEY))
    markup = filters.genre_selector_keyboard(entries, 0, "browse")
    assert len(markup.inline_keyboard) == 13  # ten genres, pager, and two navigation rows
    assert markup.inline_keyboard[0][0].text.startswith("🎞 Custom 0")
    assert "filmfilter:g:b:1" in callbacks(markup)
    assert all(len(value.encode("utf-8")) <= 64 for value in callbacks(markup))


def test_digest_is_deterministic_and_collision_is_rejected(monkeypatch):
    films = [film("1", "A", genres=["Драма"]), film("2", "B", genres=["Комедия"])]
    assert filters.genre_key("драма") == filters.genre_key("драма")
    monkeypatch.setattr(filters, "genre_key", lambda identity: "collision")
    assert filters.resolve_genre(films, "collision") is None


def test_genreless_resolution_does_not_depend_on_provider():
    item = film("1", "A")
    item["metadata_provider"] = "tmdb"
    entry = filters.resolve_genre([item], filters.GENRELESS_KEY)
    assert entry is not None
    assert filters.filter_want_films([item], entry) == [item]


def test_menu_order_and_new_random_callback():
    markup = section_menu_keyboard("films")
    assert [(row[0].text, row[0].callback_data) for row in markup.inline_keyboard] == [
        ("➕ Добавить фильм", "add|films"),
        ("📋 Хотим посмотреть", "list|films|want|0"),
        ("🎭 По жанрам", "filmfilter:g:b:0"),
        ("🎲 Выбрать случайный", "filmfilter:r"),
        ("✅ Просмотренные", "list|films|watched|0"),
        ("🔄 Обновить данные фильмов", "filmenrich:open"),
        ("🏠 В меню", "menu:main"),
    ]


def test_filtered_list_view_and_back_preserve_key_and_clamped_page(monkeypatch):
    films = [film(str(index), f"Film {index}", genres=["Драма"]) for index in range(11)]
    _, safe_edit = configured(monkeypatch, films)
    key = filters.genre_key(filters.genre_identity("Драма"))

    asyncio.run(filters.film_filter_callback_router(update(f"filmfilter:l:{key}:9"), SimpleNamespace()))
    markup = safe_edit.await_args.kwargs["reply_markup"]
    assert f"filmfilter:v:{key}:1:10" in callbacks(markup)

    asyncio.run(filters.film_filter_callback_router(update(f"filmfilter:v:{key}:1:10"), SimpleNamespace()))
    assert f"filmfilter:l:{key}:1" in callbacks(safe_edit.await_args.kwargs["reply_markup"])


def test_status_recomputes_clamps_and_last_film_is_empty(monkeypatch):
    items = [film(str(index), f"Film {index}", genres=["Драма"]) for index in range(11)]
    store, safe_edit = configured(monkeypatch, items)
    key = filters.genre_key("драма")

    def mark(item_id, status):
        target = next(item for item in store.data["films"] if item["id"] == item_id)
        target["status"] = status
        return FilmOperationResult(True, dict(target))

    monkeypatch.setattr(filters, "set_film_status", mark)
    asyncio.run(filters.film_filter_callback_router(update(f"filmfilter:s:{key}:1:10"), SimpleNamespace()))
    assert "Показаны 1–10" in safe_edit.await_args.args[1]
    assert store.data["films"][-1]["status"] == "watched"

    store.data["films"] = [film("only", "Only", genres=["Драма"])]
    asyncio.run(filters.film_filter_callback_router(update(f"filmfilter:s:{key}:0:only"), SimpleNamespace()))
    assert "больше нет фильмов" in safe_edit.await_args.args[1]


def test_delete_confirmation_context_reclamps_and_last_is_empty(monkeypatch):
    items = [film(str(index), f"Film {index}", genres=["Драма"]) for index in range(11)]
    store, safe_edit = configured(monkeypatch, items)
    key = filters.genre_key("драма")

    asyncio.run(filters.film_filter_callback_router(update(f"filmfilter:c:{key}:1:10"), SimpleNamespace()))
    assert f"filmfilter:d:{key}:1:10" in callbacks(safe_edit.await_args.kwargs["reply_markup"])

    def remove(item_id):
        target = next(item for item in store.data["films"] if item["id"] == item_id)
        store.data["films"].remove(target)
        return FilmOperationResult(True, target)

    monkeypatch.setattr(filters, "delete_film", remove)
    asyncio.run(filters.film_filter_callback_router(update(f"filmfilter:d:{key}:1:10"), SimpleNamespace()))
    assert "Показаны 1–10" in safe_edit.await_args.args[1]

    store.data["films"] = [film("only", "Only", genres=["Драма"])]
    asyncio.run(filters.film_filter_callback_router(update(f"filmfilter:d:{key}:0:only"), SimpleNamespace()))
    assert "больше нет фильмов" in safe_edit.await_args.args[1]


def test_random_any_and_genre_recompute_candidates(monkeypatch):
    items = [
        film("want", "Want", genres=["Драма"]),
        film("other", "Other", genres=["Комедия"]),
        film("watched", "Watched", status="watched", genres=["Драма"]),
    ]
    store, safe_edit = configured(monkeypatch, items)
    monkeypatch.setattr(filters.random, "choice", lambda candidates: candidates[-1])

    asyncio.run(filters.film_filter_callback_router(update("filmfilter:a"), SimpleNamespace()))
    assert "CARD:Other" in safe_edit.await_args.args[1]

    key = filters.genre_key("драма")
    asyncio.run(filters.film_filter_callback_router(update(f"filmfilter:x:{key}"), SimpleNamespace()))
    assert "CARD:Want" in safe_edit.await_args.args[1]
    assert f"filmfilter:x:{key}" in callbacks(safe_edit.await_args.kwargs["reply_markup"])

    store.data["films"][0]["status"] = "watched"
    asyncio.run(filters.film_filter_callback_router(update(f"filmfilter:x:{key}"), SimpleNamespace()))
    assert "больше нет фильмов" in safe_edit.await_args.args[1]


def test_random_zero_one_and_random_mutation_navigation(monkeypatch):
    store, safe_edit = configured(monkeypatch, [])
    monkeypatch.setattr(filters.random, "choice", lambda candidates: pytest.fail("choice called for empty candidates"))
    asyncio.run(filters.film_filter_callback_router(update("filmfilter:a"), SimpleNamespace()))
    assert "пока нет" in safe_edit.await_args.args[1]

    store.data["films"] = [film("one", "One", genres=["Драма"])]
    monkeypatch.setattr(filters.random, "choice", lambda candidates: candidates[0])
    asyncio.run(filters.film_filter_callback_router(update("filmfilter:a"), SimpleNamespace()))
    assert "CARD:One" in safe_edit.await_args.args[1]

    monkeypatch.setattr(filters, "set_film_status", lambda item_id, status: FilmOperationResult(True))
    asyncio.run(filters.film_filter_callback_router(update("filmfilter:rs:any:one"), SimpleNamespace()))
    assert "отмечен" in safe_edit.await_args.args[1]
    assert "filmfilter:x:any" in callbacks(safe_edit.await_args.kwargs["reply_markup"])


def test_stale_genre_and_deleted_film_recover_safely(monkeypatch):
    _, safe_edit = configured(monkeypatch, [film("1", "A", genres=["Драма"])])
    asyncio.run(filters.film_filter_callback_router(update("filmfilter:l:missing:0"), SimpleNamespace()))
    assert "больше недоступен" in safe_edit.await_args.args[1]

    key = filters.genre_key("драма")
    asyncio.run(filters.film_filter_callback_router(update(f"filmfilter:v:{key}:0:deleted"), SimpleNamespace()))
    assert "уже удалён" in safe_edit.await_args.args[1]
    assert f"filmfilter:l:{key}:0" in callbacks(safe_edit.await_args.kwargs["reply_markup"])


def test_worst_case_callbacks_fit_telegram_limit():
    item = film("12345678-1234-1234-1234-123456789012", "A", genres=["Драма"])
    entry = filters.GenreEntry("драма", "Драма", 1, filters.genre_key("драма"))
    markups = [
        filters.filtered_list_keyboard([item], entry, 999),
        filters.filtered_item_keyboard(item, entry, 999),
        filters.random_result_keyboard(item, entry.key),
    ]
    for markup in markups:
        assert all(len(value.encode("utf-8")) <= 64 for value in callbacks(markup))


def test_legacy_list_and_random_callbacks_still_route(monkeypatch):
    monkeypatch.setattr(runtime, "ensure_access", AsyncMock(return_value=True))
    monkeypatch.setattr(runtime, "remember_current_chat", AsyncMock())
    show_list = AsyncMock(return_value=runtime.SECTION)
    show_random = AsyncMock(return_value=runtime.SECTION)
    monkeypatch.setattr(runtime, "show_list", show_list)
    monkeypatch.setattr(runtime, "show_random_film", show_random)

    asyncio.run(runtime.section_router(update("list|films|want|0"), SimpleNamespace(user_data={})))
    show_list.assert_awaited_once_with(ANY, "films", 0, status_filter="want")

    asyncio.run(runtime.section_router(update("random|films"), SimpleNamespace(user_data={})))
    show_random.assert_awaited_once()


def test_historical_rate_start_uses_shared_operation(monkeypatch):
    target = film("legacy", "Legacy", genres=[])
    monkeypatch.setattr(runtime, "ensure_access", AsyncMock(return_value=True))
    monkeypatch.setattr(runtime, "remember_current_chat", AsyncMock())
    monkeypatch.setattr(runtime.storage, "load", lambda: {"films": [target]})
    monkeypatch.setattr(runtime, "set_film_status", lambda item_id, status: FilmOperationResult(True, {**target, "status": status}))
    safe_edit = AsyncMock()
    monkeypatch.setattr(runtime, "safe_edit_message", safe_edit)

    asyncio.run(runtime.section_router(update("rate_start|films|legacy|want|0"), SimpleNamespace(user_data={})))

    assert "Legacy" in safe_edit.await_args.args[1]
    assert "list|films|watched|0" in callbacks(safe_edit.await_args.kwargs["reply_markup"])
