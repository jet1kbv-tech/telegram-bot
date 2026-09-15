from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.app import build_app
from bot.config import (BIRTHDAY_REMINDER_CHECK_INTERVAL, NOTIFICATION_CHECK_INTERVAL,
                        TRIP_REMINDER_CHECK_INTERVAL, AIESA_TRANSCRIPTION_POLL_SECONDS)
from bot.states import (ADDING_NOTE_TEXT, BIRTHDAY_DATE, BIRTHDAY_IMPORT_FILE, BIRTHDAY_TITLE,
                        BIRTHDAY_YEAR, EDITING_NOTE_TEXT, MENU, SECTION, WAITING_FOR_AI_TRANSCRIPTION)
from telegram.ext import CallbackQueryHandler, ConversationHandler


def test_build_app_smoke(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "123456:TEST_TOKEN")

    app = build_app()

    assert app is not None


def test_notification_scheduler_cadences(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "123456:TEST_TOKEN")
    app = build_app()
    jobs = {job.name: job for job in app.job_queue.jobs()}

    assert jobs["proactive_trip_reminders_v2"].job.trigger.interval.total_seconds() == 15 * 60
    assert TRIP_REMINDER_CHECK_INTERVAL == 15 * 60
    assert jobs["afisha_notifications"].job.trigger.interval.total_seconds() == 60 * 60
    assert NOTIFICATION_CHECK_INTERVAL == 60 * 60
    assert jobs["birthday_reminders"].job.trigger.interval.total_seconds() == 60 * 60
    assert BIRTHDAY_REMINDER_CHECK_INTERVAL == 60 * 60
    assert jobs["ai_transcription_jobs"].job.trigger.interval.total_seconds() == AIESA_TRANSCRIPTION_POLL_SECONDS


def test_navigation_callbacks_and_conversation_states_remain_registered(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "123456:TEST_TOKEN")
    app = build_app()
    conversation = next(handler for handler in app.handlers[0] if isinstance(handler, ConversationHandler))

    assert {MENU, SECTION, WAITING_FOR_AI_TRANSCRIPTION, BIRTHDAY_TITLE, BIRTHDAY_DATE,
            BIRTHDAY_YEAR, BIRTHDAY_IMPORT_FILE, ADDING_NOTE_TEXT, EDITING_NOTE_TEXT} <= set(conversation.states)
    assert (WAITING_FOR_AI_TRANSCRIPTION, BIRTHDAY_TITLE, BIRTHDAY_DATE, BIRTHDAY_YEAR,
            BIRTHDAY_IMPORT_FILE, ADDING_NOTE_TEXT, EDITING_NOTE_TEXT) == (77, 78, 79, 80, 81, 82, 83)
    for state in (MENU, SECTION):
        callbacks = [handler for handler in conversation.states[state]
                     if isinstance(handler, CallbackQueryHandler)]
        patterns = {handler.pattern.pattern for handler in callbacks if handler.pattern is not None}
        names = {handler.callback.__name__ for handler in callbacks}
        assert {r"^(main|menu:main)$", r"^upcoming:(?:today|7|30)$", r"^birthday:",
                r"^aif:", r"^menu\|(films|wishlist|leisure|afisha|backlog)$",
                r"^places:", r"^spark:", r"^tickets:", r"^purchases:"} <= patterns
        # calendar_menu, activity:menu, and more:menu deliberately use this
        # existing fallback so legacy and presentation-only routes share ordering.
        assert "section_router" in names
