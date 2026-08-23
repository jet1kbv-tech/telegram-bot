import asyncio
from datetime import date, datetime
from unittest.mock import AsyncMock

from bot import runtime
from bot.services.notification_enrichment import build_notification_context, render_notification_enrichment
from bot.services.weather import WeatherDay, WeatherForecast, WeatherMalformedResponse, WeatherTimeout
from bot.storage import JsonStorage

NOW = datetime(2026, 8, 29, 23, 38)


class Provider:
    horizon_days = 16

    def __init__(self, *, location="Москва", minimum=14, maximum=21, rain=70, error=None):
        self.location, self.minimum, self.maximum, self.rain, self.error = location, minimum, maximum, rain, error
        self.calls = []

    async def get_forecast(self, location, date_from, date_to=None):
        self.calls.append((location, date_from, date_to))
        if self.error:
            raise self.error
        return WeatherForecast(self.location, (WeatherDay(date_from, self.minimum, self.maximum,
            self.rain, "rain", "дождь", 3),))


def run(value):
    return asyncio.run(value)


def snapshot(*, place="", event_date="2026-08-30", attachments=()):
    return {
        "afisha": [{"id": "a", "title": "Пикник", "date": event_date, "time": "23:38",
                    "end_date": "", "end_time": "", "place": place, "status": "active"}],
        "calendars": {"vova": [], "sasha": []},
        "event_attachments": list(attachments),
    }


def ticket(parent_type="afisha", parent_id="a"):
    return {"id": "ticket", "parent_type": parent_type, "parent_event_id": parent_id,
            "semantic_type": "transport_ticket", "transport_type": "train",
            "origin": "Москва Казанская", "destination": "Придача Воронеж Южный",
            "date": "2026-08-30", "departure_time": "23:38",
            "arrival_date": "2026-08-31", "arrival_time": "09:33",
            "telegram_media_type": "document", "telegram_file_id": "opaque-file"}


def build(data, provider, actor="vova"):
    return run(build_notification_context(data, event_id="a", actor_key=actor, now=NOW,
                                           timezone="Europe/Moscow", weather_provider=provider))


def test_normal_event_uses_moscow_fallback_weather_and_umbrella_advice():
    provider = Provider()
    result = build(snapshot(), provider)
    assert provider.calls == [("Москва", date(2026, 8, 30), date(2026, 8, 30))]
    assert result.trip_context is None
    assert "🌦 Москва · 30 августа" in render_notification_enrichment(result)
    assert "💡 Лучше взять зонт." in render_notification_enrichment(result)


def test_explicit_non_moscow_location_and_cold_advice():
    provider = Provider(location="Санкт-Петербург", maximum=10, rain=0)
    result = build(snapshot(place="Санкт-Петербург"), provider)
    assert provider.calls[0][0] == "Санкт-Петербург"
    assert "Стоит взять что-то потеплее." in render_notification_enrichment(result)
    assert "Москва" not in render_notification_enrichment(result)


def test_voronezh_trip_uses_arrival_day_and_renders_bounded_travel_block():
    provider = Provider(location="Воронеж")
    result = build(snapshot(attachments=[ticket()]), provider)
    text = "ORIGINAL\n\n" + render_notification_enrichment(result)
    assert provider.calls == [("Воронеж", date(2026, 8, 31), date(2026, 8, 31))]
    assert "ORIGINAL" in text
    assert "🚆 Отправление: 30 августа · 23:38" in text
    assert "Прибытие: 31 августа · 09:33" in text
    assert "🌦 Воронеж · 31 августа" in text
    assert "Лучше взять зонт." in text


def test_horizon_is_rejected_before_provider_call_and_context_missing_is_empty():
    provider = Provider()
    result = build(snapshot(event_date="2026-10-30"), provider)
    assert provider.calls == []
    assert render_notification_enrichment(result) == ""
    missing = run(build_notification_context(snapshot(), event_id="missing", actor_key="vova", now=NOW,
        timezone="UTC", weather_provider=provider))
    assert render_notification_enrichment(missing) == ""


def test_provider_failures_are_silent_and_preserve_trip_metadata():
    for error in (WeatherTimeout(), WeatherMalformedResponse()):
        result = build(snapshot(attachments=[ticket()]), Provider(error=error))
        text = render_notification_enrichment(result)
        assert "Отправление" in text and "🌦" not in text and "недоступ" not in text


def test_private_calendar_document_is_not_visible_to_shared_afisha_recipient():
    data = snapshot()
    data["calendars"]["vova"] = [{"id": "private", "owner": "vova", "source": "manual",
        "title": "Private", "date": "2026-08-30", "start_time": "20:00"}]
    data["event_attachments"] = [ticket("calendar", "private")]
    result = build(data, Provider(location="Москва"), actor="sasha")
    assert result.trip_context is None
    assert "Отправление" not in render_notification_enrichment(result)


def test_service_has_no_polza_boundary_and_does_not_mutate_storage():
    data = snapshot(attachments=[ticket()])
    before = repr(data)
    build(data, Provider(location="Воронеж"))
    assert repr(data) == before
    assert "polza" not in build_notification_context.__module__


def test_existing_job_sends_one_enriched_message_and_keeps_existing_marker(monkeypatch, tmp_path):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 8, 29, 23, 38)

    store = JsonStorage(tmp_path / "data.json")
    data = snapshot(attachments=[ticket()])
    data["meta"] = {"user_chats": {"one": 101}}
    store.save(data)
    provider = Provider(location="Воронеж")
    monkeypatch.setattr(runtime, "storage", store)
    monkeypatch.setattr(runtime, "datetime", Clock)
    monkeypatch.setattr(runtime, "ALLOWED_USERS", {"one": {"name": "Вова", "wishlist_owner": "vova"}})
    runtime.configure_notification_enrichment(provider)
    bot = AsyncMock()

    run(runtime.check_afisha_notifications(type("Context", (), {"bot": bot})()))

    bot.send_message.assert_awaited_once()
    text = bot.send_message.await_args.kwargs["text"]
    assert "что завтра у вас событие: Пикник" in text
    assert "🚆 Отправление" in text and "🌦 Воронеж · 31 августа" in text
    saved = store.load()
    assert saved["afisha"][0]["notified_24h"] is True
    assert set(saved) == {"meta", "films", "wishlist", "leisure", "backlog", "spark", "afisha", "calendars", "places", "purchases", "tickets", "event_attachments"}
