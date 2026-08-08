import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.handlers import films
from bot.keyboards.common import item_keyboard
from bot.states import SECTION
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


def test_successful_creation_shows_add_again_and_clears_temporary_data(monkeypatch: pytest.MonkeyPatch) -> None:
    saved = {"films": []}
    monkeypatch.setattr(films.storage, "load", lambda: saved)
    monkeypatch.setattr(films.storage, "save", lambda data: None)
    films.configure_films_handlers(
        safe_edit_message=AsyncMock(),
        build_item_text=lambda section, item: item["title"],
        item_keyboard=lambda *args, **kwargs: None,
        main_menu_keyboard=lambda: None,
    )
    message = SimpleNamespace(text="-", reply_text=AsyncMock())
    update = SimpleNamespace(message=message)
    context = SimpleNamespace(user_data={
        "film_title": "Arrival",
        "film_comment": "stale",
        "pending_sasha_rating": 10,
    })
    monkeypatch.setattr(films, "ensure_access", AsyncMock(return_value=True))
    monkeypatch.setattr(films, "remember_current_chat", AsyncMock())
    monkeypatch.setattr(films, "get_user_name", lambda update: "user")

    result = asyncio.run(films.add_film_comment(update, context))

    assert result == SECTION
    assert context.user_data == {"active_section": "films"}
    created = saved["films"][0]
    assert created["external_id"] == ""
    assert created["year"] is None
    markup = message.reply_text.await_args.kwargs["reply_markup"]
    assert [(button.text, button.callback_data) for row in markup.inline_keyboard for button in row] == [
        ("➕ Добавить ещё", "add|films"),
        ("🎬 К фильмам", "menu|films"),
        ("🏠 В меню", "menu:main"),
    ]
