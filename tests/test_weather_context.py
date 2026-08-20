import asyncio
from datetime import date, datetime

from bot.services.weather import WeatherDay, WeatherForecast
from bot.services.weather_context import advice, query_weather_context


class Provider:
    def __init__(self): self.calls = []
    async def get_forecast(self, location, date_from, date_to=None):
        self.calls.append((location, date_from, date_to))
        return WeatherForecast(location, (WeatherDay(date_from, 5, 11, 70, "rain", "дождь", 4),))


def data(place=""):
    return {"calendars": {"vova": []}, "afisha": [{"id": "e1", "title": "Пикник", "date": "2026-08-25", "time": "12:00", "end_date": "2026-08-26", "end_time": "12:00", "place": place, "status": "active"}], "event_attachments": []}


def run(coro): return asyncio.run(coro)


def test_event_uses_structured_location_and_canonical_range():
    provider = Provider()
    result = run(query_weather_context(data("Воронеж, парк"), actor_key="vova", now=datetime(2026, 8, 19),
        timezone="Europe/Moscow", provider=provider, weather_scope="event", target="пикник", location=None,
        explicit_date=None, include_advice=True))
    assert result.outcome == "found"
    assert provider.calls == [("Воронеж, парк", date(2026, 8, 25), date(2026, 8, 26))]
    assert "Лучше взять зонт" in result.text


def test_local_event_has_moscow_fallback():
    provider = Provider()
    run(query_weather_context(data(), actor_key="vova", now=datetime(2026, 8, 19), timezone="Europe/Moscow",
        provider=provider, weather_scope="event", target="Пикник", location=None, explicit_date=None, include_advice=False))
    assert provider.calls[0][0] == "Москва"


def test_ambiguity_never_calls_provider():
    provider = Provider(); value = data(); value["afisha"].append({**value["afisha"][0], "id": "e2"})
    result = run(query_weather_context(value, actor_key="vova", now=datetime(2026, 8, 19), timezone="Europe/Moscow",
        provider=provider, weather_scope="event", target="Пикник", location=None, explicit_date=None, include_advice=False))
    assert result.outcome == "ambiguous" and not provider.calls


def test_advice_is_conservative_for_missing_probability():
    forecast = WeatherForecast("X", (WeatherDay(date(2026, 8, 20), 15, 20, None, None, "облачно", None),))
    assert advice(forecast) == ()
