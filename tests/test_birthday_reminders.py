import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from bot.services.birthday_reminders import (DELIVERY_RETENTION_DAYS, delivery_marker,
                                              scan_birthday_reminders)
from bot.services.important_dates import effective_occurrence
from bot.storage import JsonStorage


ACTORS = {
    "wp_bvv": {"wishlist_owner": "vova"},
    "privetnormalno": {"wishlist_owner": "sasha"},
}


def birthday(*, month=11, day=12, year=1968, visibility="shared", created_by="vova"):
    return {"id": "birthday-1", "type": "birthday", "title": "Мама", "month": month,
            "day": day, "year": year, "visibility": visibility, "note": "private",
            "created_by": created_by}


def make_store(tmp_path, item=None, markers=None):
    store = JsonStorage(tmp_path / "data.json")
    data = store.default_data()
    data["important_dates"] = [item or birthday()]
    data["meta"]["user_chats"] = {"wp_bvv": 101, "privetnormalno": 202}
    data["meta"]["birthday_reminders"] = markers or {}
    store.save(data)
    return store


def scan(store, now, bot=None):
    bot = bot or AsyncMock()
    result = asyncio.run(scan_birthday_reminders(
        storage=store, bot=bot, actors=ACTORS, timezone="Europe/Moscow", now=now))
    return result, bot


@pytest.mark.parametrize(("now", "phrase"), [
    (datetime(2026, 10, 29, 18), "Через 14 дней"),
    (datetime(2026, 11, 5, 18), "Через неделю"),
    (datetime(2026, 11, 12, 23, 45), "Сегодня —"),
])
def test_exact_windows_are_due_and_day_of_runs_late(tmp_path, now, phrase):
    result, bot = scan(make_store(tmp_path), now)
    assert result["sent"] == 2
    assert all(phrase in call.kwargs["text"] for call in bot.send_message.await_args_list)
    assert all("Исполнится 58 лет" in call.kwargs["text"] for call in bot.send_message.await_args_list)


@pytest.mark.parametrize("days", [13, 6, 1])
def test_intermediate_windows_do_not_send(tmp_path, days):
    result, bot = scan(make_store(tmp_path), datetime(2026, 11, 12, 12) - timedelta(days=days))
    assert result["sent"] == 0
    bot.send_message.assert_not_awaited()


@pytest.mark.parametrize(("visibility", "expected"), [
    ("shared", {101, 202}), ("vova", {101}), ("sasha", {202}),
])
@pytest.mark.parametrize("created_by", ["vova", "sasha"])
def test_visibility_not_creator_controls_trusted_recipients(tmp_path, visibility, expected, created_by):
    _, bot = scan(make_store(tmp_path, birthday(visibility=visibility, created_by=created_by)),
                  datetime(2026, 11, 5))
    assert {call.kwargs["chat_id"] for call in bot.send_message.await_args_list} == expected


def test_persistent_dedupe_survives_repeat_and_new_storage_instance(tmp_path):
    store = make_store(tmp_path)
    first, _ = scan(store, datetime(2026, 11, 5))
    second, bot = scan(store, datetime(2026, 11, 5, 22))
    restarted = JsonStorage(store.path)
    third, _ = scan(restarted, datetime(2026, 11, 5, 23))
    assert first["sent"] == 2
    assert second["duplicates_skipped"] == 2 and third["duplicates_skipped"] == 2
    bot.send_message.assert_not_awaited()


def test_markers_distinguish_window_recipient_and_occurrence_year():
    item_id = "birthday-1"
    occurrence = effective_occurrence({"month": 11, "day": 12}, 2026)
    values = {
        delivery_marker(item_id, "vova", occurrence, 14),
        delivery_marker(item_id, "vova", occurrence, 7),
        delivery_marker(item_id, "sasha", occurrence, 14),
        delivery_marker(item_id, "vova", occurrence.replace(year=2027), 14),
    }
    assert len(values) == 4
    assert all(len(value) == 64 and "birthday" not in value for value in values)


def test_feb_29_uses_feb_28_in_non_leap_year(tmp_path):
    result, bot = scan(make_store(tmp_path, birthday(month=2, day=29, year=2000)),
                       datetime(2027, 2, 28, 20))
    assert result["sent"] == 2
    assert "Сегодня" in bot.send_message.await_args.kwargs["text"]
    assert set(JsonStorage(tmp_path / "data.json").load()["meta"]["birthday_reminders"].values()) == {"2027-02-28"}


def test_failed_recipient_is_retryable_and_does_not_block_other(tmp_path):
    store = make_store(tmp_path)
    bot = AsyncMock()
    bot.send_message.side_effect = [RuntimeError("telegram"), None]
    result, _ = scan(store, datetime(2026, 11, 5), bot)
    assert result["failed"] == 1 and result["sent"] == 1
    assert len(store.load()["meta"]["birthday_reminders"]) == 1

    retry, retry_bot = scan(store, datetime(2026, 11, 5, 20))
    assert retry["sent"] == 1 and retry["duplicates_skipped"] == 1
    retry_bot.send_message.assert_awaited_once_with(
        chat_id=101, text=retry_bot.send_message.await_args.kwargs["text"])


def test_old_and_invalid_markers_are_pruned(tmp_path):
    markers = {"a" * 64: "2025-01-01", "b" * 64: "not-a-date", "c" * 64: "2026-11-01"}
    store = make_store(tmp_path, markers=markers)
    scan(store, datetime(2026, 12, 1))
    assert store.load()["meta"]["birthday_reminders"] == {"c" * 64: "2026-11-01"}
    assert DELIVERY_RETENTION_DAYS == 32


def test_no_stale_catch_up_and_birthday_records_are_not_mutated(tmp_path):
    store = make_store(tmp_path)
    before = deepcopy(store.load()["important_dates"])
    result, bot = scan(store, datetime(2026, 11, 6))  # Six days away; missed seven-day window.
    assert result["sent"] == 0
    bot.send_message.assert_not_awaited()
    assert store.load()["important_dates"] == before
