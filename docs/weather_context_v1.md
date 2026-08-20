# Weather Context v1

## Architecture and audit

The request path is one Polza semantic parse, read-only Context Engine projection,
local location/date selection, `WeatherProvider`, and deterministic formatting.
Neither context bundles nor provider JSON cross those boundaries. Existing
`EventContext` exposes a canonical date/range and `location_text`; manual Calendar
events currently have no structured location, while Afisha's `place` supplies it.
`TripContext` exposes structured destination/origin, arrival fields and non-null
`trip_start`/`trip_end` datetimes inferred from visible transport documents.
Its deliberately narrow `city_hint` recognizes Voronezh only; weather does not
expand that allow-list. Context Engine continues to enforce actor-visible Calendar,
shared Afisha, and attachment-parent visibility. Notification scheduling is unchanged.

## Provider and normalized model

`OpenMeteoWeatherProvider` was selected because Open-Meteo has a stable structured
city geocoder and daily forecast API, requires no API credential for this small use,
and supports a 16-day horizon. The replaceable protocol returns `WeatherForecast`
and normalized `WeatherDay` values (label, dates, temperature range, precipitation
probability/type, condition and wind), never raw JSON. It performs at most one
geocode and one range forecast call. A bounded in-memory TTL cache stores normalized
locations and forecasts; nothing is written to `data.json`.

## Resolution rules

Trip destination wins for trip/arrival questions. Event weather uses structured
`EventContext.location_text`; an explicitly supplied location is next. Moscow is
the fallback **only** for a resolved local Calendar/Afisha event with no location,
matching the product's Moscow-local Afisha semantics. It never overrides a trip
destination or a non-Moscow event place. Narrative event titles are used only for
local entity matching and are never sent as geographic truth.

Arrival scope uses `arrival_date` and fails rather than inventing it. Trip scope
uses `trip_start.date()` through `trip_end.date()`. Event scope uses its canonical
date through end date. An explicit date is used only for explicit-place/date scope.
Multiple matches return an ambiguity response before any provider call; v1 asks the
user to clarify rather than issuing speculative lookups.

## Horizon, advice, UX and failures

Dates outside today's 16-day daily forecast horizon receive a controlled “forecast
not available yet” response; climatology is not substituted. Output is capped at
six daily rows. Advice is deterministic and conservative: precipitation probability
at least 60% suggests an umbrella, and a maximum temperature at or below 12 °C
suggests warmer clothing. Missing probability produces no precipitation claim.

Location failure, missing context/date, horizon failure, and provider failure have
separate compact messages. HTTP details and raw payloads are never shown. Structural
logs contain scope/outcome/count only, not event titles, routes, location text,
Telegram identifiers, provider payloads, tokens, or raw Polza output.

## Privacy and call budget

The provider receives only a location query (then coordinates) and a date/range.
It never receives event titles, users, Telegram IDs, documents, ticket metadata,
route history, or a `ContextBundle`. Each natural-language request has one Polza
parse maximum, local resolution, at most one geocode and one forecast request.
Cache hits reduce this further. No Calendar, Afisha, ticket, relationship, or trip
storage is mutated, and there is no persistent weather root.

## Configuration, limitations, and deferred work

Optional environment settings are `WEATHER_TIMEOUT_SECONDS` (default `8`) and
`WEATHER_CACHE_TTL_SECONDS` (default `1800`). No weather API key is required.
Current weather is represented by today's daily forecast rather than hourly
conditions. V1 does not persist geocoding, provide climatology, general search,
places/recommendations, or proactive weather/Afisha notifications. Callback-based
weather choosers and proactive notifications are explicitly deferred.
