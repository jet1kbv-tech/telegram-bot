import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot import runtime
from bot.handlers.film_operations import FilmOperationResult
from bot.keyboards.common import section_menu_keyboard


def film(item_id, status="watched", reactions=None, **extra):
    item = {"id": item_id, "title": f"Film {item_id}", "status": status, **extra}
    if reactions is not None:
        item["reactions"] = reactions
    return item


def callbacks(markup):
    return [button.callback_data for row in markup.inline_keyboard for button in row]


def update(data, username="wp_bvv"):
    query = SimpleNamespace(data=data, answer=AsyncMock())
    return SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(username=username),
        effective_chat=SimpleNamespace(id=1),
    )


class Store:
    def __init__(self, films):
        self.films = films

    def load(self):
        return {"films": self.films}


@pytest.fixture
def configured(monkeypatch):
    films = [
        film("a", reactions={"vova": "like"}),
        film("b", reactions={"sasha": "neutral"}),
        film("c", rating=9),
        film("want", status="want"),
    ]
    store = Store(films)
    safe_edit = AsyncMock()
    monkeypatch.setattr(runtime, "storage", store)
    monkeypatch.setattr(runtime, "safe_edit_message", safe_edit)
    monkeypatch.setattr(runtime, "ensure_access", AsyncMock(return_value=True))
    monkeypatch.setattr(runtime, "remember_current_chat", AsyncMock())

    def react(item_id, actor, reaction):
        target = next((item for item in films if item["id"] == item_id), None)
        if target is None:
            return FilmOperationResult(False)
        target.setdefault("reactions", {})[actor] = reaction
        return FilmOperationResult(True, dict(target))

    monkeypatch.setattr(runtime, "set_film_reaction", react)
    return films, safe_edit


def run(data, context, username="wp_bvv"):
    return asyncio.run(runtime.section_router(update(data, username), context))


def test_actor_specific_unknown_count_and_legacy_numeric_is_unknown():
    films = [
        film("v", reactions={"vova": "like"}),
        film("s", reactions={"sasha": "neutral"}),
        film("both", reactions={"vova": "dislike", "sasha": "like"}),
        film("legacy", vova_rating=10),
        film("want", status="want"),
    ]
    assert [item["id"] for item in runtime.unrated_watched_films(films, "vova")] == ["s", "legacy"]
    assert [item["id"] for item in runtime.unrated_watched_films(films, "sasha")] == ["v", "legacy"]
    label = section_menu_keyboard("films", unrated_watched_count=2).inline_keyboard[6][0].text
    assert label == "⭐ Оценить просмотренные · 2"


def test_session_reactions_advance_in_order_and_isolate_actor(configured):
    films, safe_edit = configured
    context = SimpleNamespace(user_data={})
    run("film_backlog|start", context)
    assert "Film b" in safe_edit.await_args.args[1]

    run("film_backlog|react|b|neutral", context)
    assert films[1]["reactions"] == {"sasha": "neutral", "vova": "neutral"}
    assert "Film c" in safe_edit.await_args.args[1]
    assert films[0] in runtime.unrated_watched_films(films, "sasha")

    run("film_backlog|react|c|dislike", context)
    assert safe_edit.await_args.args[1] == "Готово. Оценено: 2"
    assert runtime.FILM_RATING_SESSION_KEY not in context.user_data


@pytest.mark.parametrize("reaction", ["like", "neutral", "dislike"])
def test_each_reaction_advances_to_next_film(configured, reaction):
    films, safe_edit = configured
    context = SimpleNamespace(user_data={})
    run("film_backlog|start", context)
    run(f"film_backlog|react|b|{reaction}", context)
    assert films[1]["reactions"]["vova"] == reaction
    assert "Film c" in safe_edit.await_args.args[1]


def test_skip_does_not_mutate_or_repeat_and_finish_clears(configured):
    films, safe_edit = configured
    context = SimpleNamespace(user_data={})
    run("film_backlog|start", context)
    run("film_backlog|skip|b", context)
    assert "reactions" in films[1] and "vova" not in films[1]["reactions"]
    assert "Film c" in safe_edit.await_args.args[1]
    run("film_backlog|finish", context)
    assert safe_edit.await_args.args[1] == "Готово. Оценено: 0"
    assert runtime.FILM_RATING_SESSION_KEY not in context.user_data

    run("film_backlog|start", context)
    assert "Film b" in safe_edit.await_args.args[1]


def test_empty_stale_deleted_and_status_changed_are_safe(configured):
    films, safe_edit = configured
    for item in films:
        if item["status"] == "watched":
            item.setdefault("reactions", {})["vova"] = "like"
    context = SimpleNamespace(user_data={})
    run("film_backlog|start", context)
    assert safe_edit.await_args.args[1] == "Ты уже оценил все просмотренные фильмы."

    films[1]["reactions"].pop("vova")
    films[2]["reactions"].pop("vova")
    run("film_backlog|start", context)
    films[1]["status"] = "want"
    run("film_backlog|react|b|like", context)
    assert "Film c" in safe_edit.await_args.args[1]
    films.remove(films[2])
    run("film_backlog|react|c|like", context)
    assert safe_edit.await_args.args[1] == "Готово. Оценено: 0"

    run("film_backlog|react|c|like", context)
    assert safe_edit.await_args.args[1] == "Сессия оценки устарела."


def test_callback_for_non_current_film_never_mutates_it(configured):
    films, safe_edit = configured
    context = SimpleNamespace(user_data={})
    run("film_backlog|start", context)
    run("film_backlog|react|c|like", context)
    assert "vova" not in films[2].get("reactions", {})
    assert "Film b" in safe_edit.await_args.args[1]


def test_menu_clears_session_and_fresh_start_recalculates(configured, monkeypatch):
    films, _ = configured
    context = SimpleNamespace(user_data={})
    run("film_backlog|start", context)
    assert runtime.FILM_RATING_SESSION_KEY in context.user_data
    menu_update = update("menu|films")
    monkeypatch.setattr(runtime, "show_section_menu", AsyncMock(return_value=runtime.SECTION))
    asyncio.run(runtime.menu_router(menu_update, context))
    assert runtime.FILM_RATING_SESSION_KEY not in context.user_data

    films[1].setdefault("reactions", {})["vova"] = "like"
    run("film_backlog|start", context)
    assert context.user_data[runtime.FILM_RATING_SESSION_KEY]["visited_ids"] == []
