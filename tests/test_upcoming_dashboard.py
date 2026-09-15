from copy import deepcopy
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.handlers import upcoming
from bot.keyboards.common import main_menu_keyboard

NOW = datetime(2026, 9, 15, 21, tzinfo=timezone.utc)


def _buttons(markup):
    return [(button.text, button.callback_data) for row in markup.inline_keyboard for button in row]


def _snapshot():
    return {
        "calendars": {
            "vova": [{"id": "v", "title": "Vova private", "date": "2026-09-16",
                      "start_time": "10:00", "source": "manual"}],
            "sasha": [{"id": "s", "title": "Sasha private", "date": "2026-09-16",
                       "start_time": "11:00", "source": "manual"}],
        },
        "afisha": [{"id": "a", "title": "Shared show", "date": "2026-09-17",
                    "time": "19:00", "status": "active"}],
        "event_attachments": [],
    }


def _update(username, callback="upcoming:7"):
    query = SimpleNamespace(data=callback, answer=AsyncMock())
    return SimpleNamespace(callback_query=query,
                           effective_user=SimpleNamespace(username=username))


def test_main_menu_exposes_upcoming():
    assert ("📆 Ближайшее", "upcoming:7") in _buttons(main_menu_keyboard())


@pytest.mark.parametrize(("kind", "last_day"), [
    ("today", date(2026, 9, 16)),
    ("7", date(2026, 9, 22)),
    ("30", date(2026, 10, 15)),
])
def test_native_ranges_are_inclusive_local_calendar_days(kind, last_day):
    # 21:00 UTC is already the next calendar day in Europe/Moscow.
    assert upcoming.upcoming_range(kind, NOW) == (date(2026, 9, 16), last_day)


def test_upcoming_keyboard_reuses_native_calendar_and_afisha_routes():
    buttons = _buttons(upcoming.upcoming_keyboard())
    assert ("📅 Календарь", "calendar_menu") in buttons
    assert ("🗓 Афиша", "menu|afisha") in buttons
    assert ("🏠 В меню", "menu:main") in buttons


@pytest.mark.parametrize(("username", "visible", "hidden"), [
    ("wp_bvv", "Vova private", "Sasha private"),
    ("privetnormalno", "Sasha private", "Vova private"),
])
async def test_dashboard_is_read_only_actor_scoped_and_keeps_shared_afisha(
        monkeypatch, username, visible, hidden):
    snapshot = _snapshot()
    before = deepcopy(snapshot)
    edit = AsyncMock()
    monkeypatch.setattr(upcoming.storage, "load", lambda: snapshot)
    monkeypatch.setattr(upcoming, "zoned_now", lambda timezone, now=None: NOW)
    upcoming.configure_upcoming_handlers(safe_edit_message=edit)

    update = _update(username)
    await upcoming.upcoming_callback(update, SimpleNamespace())

    text = edit.await_args.args[1]
    assert "15–21 сентября" in text  # default callback requests seven days
    assert visible in text and hidden not in text and "Shared show" in text
    assert snapshot == before


async def test_empty_dashboard_renders_and_keeps_controls(monkeypatch):
    edit = AsyncMock()
    monkeypatch.setattr(upcoming.storage, "load", lambda: {"calendars": {}, "afisha": [],
                                                           "event_attachments": []})
    monkeypatch.setattr(upcoming, "zoned_now", lambda timezone, now=None: NOW)
    upcoming.configure_upcoming_handlers(safe_edit_message=edit)

    await upcoming.upcoming_callback(_update("wp_bvv", "upcoming:today"), SimpleNamespace())

    assert "На этот период ничего не запланировано." in edit.await_args.args[1]
    assert edit.await_args.kwargs["reply_markup"] == upcoming.upcoming_keyboard()
