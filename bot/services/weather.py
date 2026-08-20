"""Telegram/LLM-independent Open-Meteo weather provider boundary."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Protocol

import httpx


class WeatherError(Exception): pass
class WeatherUnavailable(WeatherError): pass
class WeatherTimeout(WeatherError): pass
class WeatherMalformedResponse(WeatherError): pass
class WeatherLocationUnresolved(WeatherError): pass
class WeatherHorizonUnavailable(WeatherError): pass


@dataclass(frozen=True, slots=True)
class WeatherLocation:
    label: str
    latitude: float
    longitude: float


@dataclass(frozen=True, slots=True)
class WeatherDay:
    date: date
    min_temperature: float
    max_temperature: float
    precipitation_probability: int | None
    precipitation_type: str | None
    condition: str
    wind_speed: float | None


@dataclass(frozen=True, slots=True)
class WeatherForecast:
    location_label: str
    days: tuple[WeatherDay, ...]


class WeatherProvider(Protocol):
    async def get_forecast(self, location: str, date_from: date,
                           date_to: date | None = None) -> WeatherForecast: ...


_CONDITIONS = {
    0: "ясно", 1: "преимущественно ясно", 2: "переменная облачность", 3: "облачно",
    45: "туман", 48: "туман", 51: "морось", 53: "морось", 55: "морось",
    61: "дождь", 63: "дождь", 65: "сильный дождь", 71: "снег", 73: "снег",
    75: "сильный снег", 80: "ливни", 81: "ливни", 82: "сильные ливни", 95: "гроза",
}


class OpenMeteoWeatherProvider:
    """One geocode plus one range forecast request, with bounded memory caches."""
    horizon_days = 16

    def __init__(self, *, timeout_seconds: float = 8, cache_ttl_seconds: int = 1800,
                 client: httpx.AsyncClient | None = None) -> None:
        self.timeout_seconds, self.cache_ttl_seconds = timeout_seconds, cache_ttl_seconds
        self._client = client
        self._locations: dict[str, tuple[float, WeatherLocation]] = {}
        self._forecasts: dict[tuple[str, date, date], tuple[float, WeatherForecast]] = {}

    async def _get(self, url: str, params: dict[str, object]) -> dict:
        try:
            if self._client is not None:
                response = await self._client.get(url, params=params, timeout=self.timeout_seconds)
            else:
                async with httpx.AsyncClient() as client:
                    response = await client.get(url, params=params, timeout=self.timeout_seconds)
            response.raise_for_status()
            value = response.json()
            if not isinstance(value, dict): raise WeatherMalformedResponse()
            return value
        except (httpx.TimeoutException, asyncio.TimeoutError) as exc: raise WeatherTimeout() from exc
        except WeatherMalformedResponse: raise
        except (httpx.HTTPError, ValueError, TypeError) as exc: raise WeatherUnavailable() from exc

    async def _resolve(self, query: str) -> WeatherLocation:
        key = " ".join(query.casefold().split())
        cached = self._locations.get(key)
        if cached and cached[0] > time.monotonic(): return cached[1]
        body = await self._get("https://geocoding-api.open-meteo.com/v1/search",
                               {"name": query, "count": 1, "language": "ru", "format": "json"})
        rows = body.get("results")
        if not isinstance(rows, list) or not rows: raise WeatherLocationUnresolved()
        row = rows[0]
        try:
            location = WeatherLocation(str(row["name"]), float(row["latitude"]), float(row["longitude"]))
        except (KeyError, TypeError, ValueError) as exc: raise WeatherMalformedResponse() from exc
        if len(self._locations) >= 128: self._locations.pop(next(iter(self._locations)))
        self._locations[key] = (time.monotonic() + self.cache_ttl_seconds, location)
        return location

    async def get_forecast(self, location: str, date_from: date, date_to: date | None = None) -> WeatherForecast:
        end = date_to or date_from
        today = date.today()
        if date_from < today or end > today + timedelta(days=self.horizon_days):
            raise WeatherHorizonUnavailable()
        resolved = await self._resolve(location)
        key = (f"{resolved.latitude:.4f},{resolved.longitude:.4f}", date_from, end)
        cached = self._forecasts.get(key)
        if cached and cached[0] > time.monotonic(): return cached[1]
        body = await self._get("https://api.open-meteo.com/v1/forecast", {
            "latitude": resolved.latitude, "longitude": resolved.longitude,
            "start_date": date_from.isoformat(), "end_date": end.isoformat(),
            "timezone": "auto", "daily": "temperature_2m_min,temperature_2m_max,precipitation_probability_max,weather_code,wind_speed_10m_max",
        })
        daily = body.get("daily")
        required = ("time", "temperature_2m_min", "temperature_2m_max", "weather_code")
        if not isinstance(daily, dict) or any(not isinstance(daily.get(k), list) for k in required):
            raise WeatherMalformedResponse()
        try:
            count = len(daily["time"])
            if not count or any(len(daily[k]) != count for k in required): raise ValueError
            probabilities = daily.get("precipitation_probability_max", [None] * count)
            winds = daily.get("wind_speed_10m_max", [None] * count)
            days = tuple(WeatherDay(date.fromisoformat(daily["time"][i]), float(daily["temperature_2m_min"][i]),
                float(daily["temperature_2m_max"][i]), int(probabilities[i]) if probabilities[i] is not None else None,
                "rain_or_snow" if int(daily["weather_code"][i]) >= 51 else None,
                _CONDITIONS.get(int(daily["weather_code"][i]), "без уточнения"),
                float(winds[i]) if winds[i] is not None else None) for i in range(count))
        except (IndexError, TypeError, ValueError) as exc: raise WeatherMalformedResponse() from exc
        forecast = WeatherForecast(resolved.label, days)
        if len(self._forecasts) >= 128: self._forecasts.pop(next(iter(self._forecasts)))
        self._forecasts[key] = (time.monotonic() + self.cache_ttl_seconds, forecast)
        return forecast
