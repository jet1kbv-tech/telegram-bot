import asyncio
from datetime import date

import httpx

from bot.services.weather import OpenMeteoWeatherProvider


def test_open_meteo_normalizes_and_caches_both_requests():
    calls = []
    def handler(request):
        calls.append(str(request.url))
        if "geocoding" in request.url.host:
            return httpx.Response(200, json={"results": [{"name": "Воронеж", "latitude": 51.67, "longitude": 39.18}]})
        return httpx.Response(200, json={"daily": {"time": ["2026-08-20"], "temperature_2m_min": [12],
            "temperature_2m_max": [21], "precipitation_probability_max": [65], "weather_code": [61], "wind_speed_10m_max": [7]}})
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OpenMeteoWeatherProvider(client=client)
            provider.horizon_days = 10000
            first = await provider.get_forecast("Воронеж", date(2026, 8, 20))
            second = await provider.get_forecast("Воронеж", date(2026, 8, 20))
            return first, second
    first, second = asyncio.run(scenario())
    assert first.days[0].precipitation_probability == 65
    assert first == second and len(calls) == 2
