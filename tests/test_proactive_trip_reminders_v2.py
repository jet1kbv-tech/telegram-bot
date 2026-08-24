import asyncio
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock

from bot.services.proactive_trip_reminders import scan_trip_reminders
from bot.services.weather import WeatherDay, WeatherForecast
from bot.storage import JsonStorage


def ticket(identity, origin, destination, departure, clock, arrival, arrival_clock,
           *, parent="san"):
    return {"id": identity, "parent_type": "afisha", "parent_event_id": parent,
            "semantic_type": "transport_ticket", "transport_type": "train",
            "origin": origin, "destination": destination, "date": departure,
            "departure_time": clock, "arrival_date": arrival, "arrival_time": arrival_clock,
            "telegram_media_type": "document", "telegram_file_id": "file"}


def snapshot(attachments):
    return {"afisha": [{"id": "san", "title": "Sanatorium", "date": "2026-08-31",
                        "time": "12:00", "status": "active"}],
            "calendars": {"vova": [], "sasha": []}, "event_attachments": attachments,
            "meta": {"user_chats": {"one": 101}}}


OUT = ticket("out", "Москва", "Придача Воронеж Южный", "2026-08-30", "23:38", "2026-08-31", "09:33")
BACK = ticket("back", "Старый Оскол", "Москва", "2026-09-06", "21:18", "2026-09-07", "09:02")
ACTORS = {"one": {"wishlist_owner": "vova"}}


class Provider:
    def __init__(self, fail=False): self.calls, self.fail = [], fail
    async def get_forecast(self, location, date_from, date_to=None):
        self.calls.append((location, date_from, date_to))
        if self.fail: raise RuntimeError("weather")
        return WeatherForecast("Воронеж", (WeatherDay(date_from, 13, 26, 29, "rain", "облачно", 2),))


def run_scan(store, now, *, bot=None, provider=None):
    bot = bot or AsyncMock()
    result = asyncio.run(scan_trip_reminders(storage=store, bot=bot, actors=ACTORS,
        timezone="Europe/Moscow", grace=timedelta(minutes=30), now=now,
        weather_provider=provider))
    return result, bot


def store_at(tmp_path, data):
    value = JsonStorage(tmp_path / "data.json"); value.save(data); return value


def test_24h_window_weather_scope_buttons_documents_and_dedupe(tmp_path):
    store = store_at(tmp_path, snapshot([OUT, BACK])); provider = Provider()
    result, bot = run_scan(store, datetime(2026, 8, 29, 23, 50), provider=provider)
    assert result["sent"] == 1
    text = bot.send_message.await_args.kwargs["text"]
    assert "Москва → Придача Воронеж Южный" in text and "Старый Оскол" not in text
    assert "📎 Билет сохранён" in text and "🌦 Воронеж · 31 августа" in text
    assert provider.calls == [("Воронеж", date(2026, 8, 31), date(2026, 8, 31))]
    callbacks = [b.callback_data for row in bot.send_message.await_args.kwargs["reply_markup"].inline_keyboard for b in row]
    assert any(":docs:" in x for x in callbacks) and any(":card:" in x for x in callbacks)
    assert any(x.startswith("view|afisha|san") for x in callbacks)
    again, _ = run_scan(store, datetime(2026, 8, 30, 0, 0), provider=provider)
    assert again["sent"] == 0 and again["deduped"] == 1
    assert len(store.load()["meta"]["trip_reminder_deliveries"]) == 1


def test_expired_missing_time_and_failed_delivery_are_not_marked(tmp_path):
    missing = {**OUT, "departure_time": ""}
    store = store_at(tmp_path, snapshot([missing]))
    result, _ = run_scan(store, datetime(2026, 8, 30, 5, 0))
    assert result["sent"] == 0
    store = store_at(tmp_path, snapshot([OUT]))
    result, _ = run_scan(store, datetime(2026, 8, 30, 2, 0))
    assert result["sent"] == 0
    bot = AsyncMock(); bot.send_message.side_effect = RuntimeError("telegram")
    result, _ = run_scan(store, datetime(2026, 8, 29, 23, 50), bot=bot)
    assert result["failed"] == 1
    assert store.load()["meta"]["trip_reminder_deliveries"] == []


def test_2h_is_compact_never_calls_weather_and_return_is_independent(tmp_path):
    store = store_at(tmp_path, snapshot([OUT, BACK])); provider = Provider()
    first, bot = run_scan(store, datetime(2026, 8, 30, 21, 45), provider=provider)
    assert first["sent"] == 1 and "Через 2 часа" in bot.send_message.await_args.kwargs["text"]
    assert "🌦" not in bot.send_message.await_args.kwargs["text"] and provider.calls == []
    second, bot2 = run_scan(store, datetime(2026, 9, 6, 19, 30), provider=provider)
    assert second["sent"] == 1
    text = bot2.send_message.await_args.kwargs["text"]
    assert "Старый Оскол → Москва" in text and "Воронеж" not in text
    assert len(store.load()["meta"]["trip_reminder_deliveries"]) == 2


def test_2h_expires_at_end_of_30_minute_window(tmp_path):
    store = store_at(tmp_path, snapshot([OUT]))
    inside, bot = run_scan(store, datetime(2026, 8, 30, 22, 7, 59))
    assert inside["sent"] == 1
    assert "Через 2 часа" in bot.send_message.await_args.kwargs["text"]

    fresh = store_at(tmp_path / "expired", snapshot([OUT]))
    expired, expired_bot = run_scan(fresh, datetime(2026, 8, 30, 22, 8))
    assert expired["sent"] == 0
    expired_bot.send_message.assert_not_awaited()


def test_weather_failure_still_sends_and_reschedule_versions_marker(tmp_path):
    store = store_at(tmp_path, snapshot([OUT])); provider = Provider(fail=True)
    result, bot = run_scan(store, datetime(2026, 8, 29, 23, 50), provider=provider)
    assert result["sent"] == 1 and "🌦" not in bot.send_message.await_args.kwargs["text"]
    data = store.load(); data["event_attachments"][0]["date"] = "2026-08-31"; store.save(data)
    result, _ = run_scan(store, datetime(2026, 8, 30, 23, 50), provider=provider)
    assert result["sent"] == 1
    assert len(store.load()["meta"]["trip_reminder_deliveries"]) == 2


def test_private_actor_visibility_and_candidate_failure_isolation(tmp_path):
    private = {**OUT, "parent_type": "calendar", "parent_event_id": "private"}
    data = snapshot([private, BACK])
    data["calendars"]["vova"] = [{"id": "private", "owner": "vova", "source": "manual",
                                     "title": "Private", "date": "2026-08-31", "start_time": "12:00"}]
    data["meta"]["user_chats"]["two"] = 202
    actors = {**ACTORS, "two": {"wishlist_owner": "sasha"}}
    store = store_at(tmp_path, data); bot = AsyncMock()
    asyncio.run(scan_trip_reminders(storage=store, bot=bot, actors=actors,
        timezone="Europe/Moscow", grace=timedelta(minutes=30),
        now=datetime(2026, 8, 29, 23, 50), weather_provider=None))
    bot.send_message.assert_awaited_once()
    assert bot.send_message.await_args.kwargs["chat_id"] == 101
