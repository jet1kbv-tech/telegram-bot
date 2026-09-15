from types import SimpleNamespace
from unittest.mock import AsyncMock
from datetime import UTC, datetime

from bot.handlers import upcoming
from bot.keyboards.common import activity_menu_keyboard, main_menu_keyboard, more_menu_keyboard
from bot.keyboards.places import places_menu_keyboard
from bot.runtime import section_router


def _buttons(markup):
    return [(button.text, button.callback_data) for row in markup.inline_keyboard for button in row]


def _button_rows(markup):
    return [[(button.text, button.callback_data) for button in row] for row in markup.inline_keyboard]


def test_main_menu_has_exactly_the_six_menu_v2_destinations():
    assert _buttons(main_menu_keyboard()) == [
        ("📆 Ближайшее", "upcoming:7"),
        ("🗓 Афиша", "menu|afisha"),
        ("📅 Календарь", "calendar_menu"),
        ("🎬 Развлечения", "activity:menu"),
        ("🤖 AI-фичи", "aif:menu"),
        ("••• Ещё", "more:menu"),
    ]


def test_entertainment_hub_reuses_domain_callbacks_and_places_hub():
    assert _button_rows(activity_menu_keyboard()) == [
        [("🎬 Фильмы и сериалы", "menu|films")],
        [("✨ Досуг", "menu|leisure"), ("📍 Места", "places:menu")],
        [("🏠 В меню", "menu:main")],
    ]
    assert _buttons(places_menu_keyboard()) == [
        ("📍 Локации в Москве", "places:moscow"),
        ("🌍 Города", "places:cities:0"),
        ("🏠 В меню", "menu:main"),
    ]


def test_more_hub_reuses_all_existing_domain_callbacks():
    assert _button_rows(more_menu_keyboard()) == [
        [("🎁 Вишлист", "menu|wishlist"), ("🛒 Покупки", "purchases:menu")],
        [("🎂 Дни рождения", "birthday:list"), ("🧩 Бэклог", "menu|backlog")],
        [("🔥 Искра", "spark:menu"), ("🎟 Билеты", "tickets:menu")],
        [("🏠 В меню", "menu:main")],
    ]


def test_upcoming_no_longer_exposes_birthday_management():
    assert ("🎂 Дни рождения", "birthday:list") not in _buttons(upcoming.upcoming_keyboard())


async def test_legacy_activity_callback_opens_entertainment(monkeypatch):
    edit = AsyncMock()
    monkeypatch.setattr("bot.runtime.ensure_access", AsyncMock(return_value=True))
    monkeypatch.setattr("bot.runtime.remember_current_chat", AsyncMock())
    monkeypatch.setattr("bot.runtime.safe_edit_message", edit)
    query = SimpleNamespace(data="activity:menu", answer=AsyncMock())
    update = SimpleNamespace(callback_query=query)

    await section_router(update, SimpleNamespace(user_data={}))

    assert edit.await_args.args[1] == "🎬 Развлечения"
    assert edit.await_args.kwargs["reply_markup"] == activity_menu_keyboard()


async def test_more_callback_opens_hub_without_loading_storage(monkeypatch):
    edit = AsyncMock()
    monkeypatch.setattr("bot.runtime.ensure_access", AsyncMock(return_value=True))
    monkeypatch.setattr("bot.runtime.remember_current_chat", AsyncMock())
    monkeypatch.setattr("bot.runtime.safe_edit_message", edit)
    monkeypatch.setattr("bot.runtime.storage.load", lambda: (_ for _ in ()).throw(AssertionError("storage read")))
    query = SimpleNamespace(data="more:menu", answer=AsyncMock())

    await section_router(SimpleNamespace(callback_query=query), SimpleNamespace(user_data={}))

    assert edit.await_args.kwargs["reply_markup"] == more_menu_keyboard()


async def test_upcoming_still_renders_birthday_occurrences(monkeypatch):
    edit = AsyncMock()
    snapshot = {
        "calendars": {}, "afisha": [], "event_attachments": [],
        "important_dates": [{
            "id": "birthday-1", "title": "Аня", "month": 9, "day": 18,
            "year": 1990, "visibility": "shared",
        }],
    }
    monkeypatch.setattr(upcoming.storage, "load", lambda: snapshot)
    monkeypatch.setattr(upcoming, "zoned_now", lambda timezone, now=None:
                        datetime(2026, 9, 15, 12, tzinfo=UTC))
    monkeypatch.setattr(upcoming, "ensure_access", AsyncMock(return_value=True))
    upcoming.configure_upcoming_handlers(safe_edit_message=edit)
    query = SimpleNamespace(data="upcoming:7", answer=AsyncMock())
    update = SimpleNamespace(callback_query=query,
                             effective_user=SimpleNamespace(username="wp_bvv"))

    await upcoming.upcoming_callback(update, SimpleNamespace())

    assert "Аня" in edit.await_args.args[1]
