# Proactive Context Notifications v1

## Existing notification architecture audit

The single `Application` job queue registers `check_afisha_notifications` once,
hourly, with a 30-second initial delay. The job scans canonical `afisha` rows and
manual calendar rows. Afisha sends a shared event separately to each known
recipient chat: a 23–25 hour reminder and a same-day reminder during 07:00–12:00.
Manual calendar reminders use the same 23–25 hour window and go only to their
owner. Afisha calendar projections are explicitly skipped.

Deduplication remains storage-based: the canonical Afisha row owns
`notified_24h` and `notified_morning`; a manual calendar row owns
`notified_24h`. The job resets the 24-hour marker only when an event is again
beyond the look-ahead window (for example after editing). It saves through the
existing storage boundary only when these markers change. No notification,
weather, or inferred-context root is added. `bot/runtime.py` still builds the
core Telegram text and performs the send.

Interactive “new calendar item” messages also exist, but they are immediate
collaboration notifications rather than scheduled reminders and are not changed
by this phase.

## Integration and domain model

At execution time, immediately after the existing Afisha core text is built and
before its existing `send_message` call, the job calls the Telegram-independent
`build_notification_context`. It resolves the canonical Afisha source directly
through Context Engine; projections are never traversed as notification sources.
The immutable `NotificationEnrichment` contains the resolved event, an optional
directly linked trip, transport/departure/arrival fields, an optional one-day
forecast, up to two shared deterministic advice strings, safe reason codes, and
a weather-attempt flag. Rendering returns a bounded suffix; an empty suffix
leaves the old text byte-for-byte unchanged.

## Deterministic precedence

* A trip is eligible only when Context Engine linked structured transport-ticket
  metadata directly to the canonical event. At most one unambiguous trip is used.
* A linked outbound trip uses its known arrival date and destination (`city_hint`
  when Context Engine safely supplies one, otherwise the stored destination).
  It never fabricates arrival data. Otherwise weather uses the event date.
* Without a linked trip, an explicit `EventContext.location_text` wins. If it is
  absent, the existing Weather Context event rule falls back to Moscow. Narrative
  titles are never parsed for geography.
* Exactly one weather day is requested and rendered. Travel output contains at
  most one departure/arrival block. Advice calls the shared Weather Context
  thresholds and is limited to two lines.

## Privacy and provider budget

Enrichment is built per recipient using that actor's Context Engine visibility.
Private manual-calendar documents belonging to another actor therefore cannot
enter the bundle. Direct attachments to a shared Afisha source retain the
existing shared visibility semantics. Logs contain booleans/reason classes only:
no title, route, place, coordinates, attachment ID, Telegram ID, or identity.

The scheduled path has **zero Polza calls**. It performs local Context Engine
resolution, then at most one geocoding request on a location-cache miss and one
forecast request on a forecast-cache miss. The provider receives only location
and the single requested date. The same `OpenMeteoWeatherProvider` instance is
shared with NL Weather Context, so its bounded in-memory TTL caches and configured
`WEATHER_TIMEOUT_SECONDS` apply. Notification code adds no cache, retry, or
persistent forecast.

## Failure isolation and deduplication

Known provider failures produce no weather block. Horizon rejection happens
locally when the provider advertises its horizon. Empty/malformed output and any
unexpected context/rendering error are isolated by the optional-enrichment
boundary; the original reminder is still sent. There is no “forecast
unavailable” noise and no retry loop.

Enrichment does not create a job, send, identity, marker, or write of its own.
The canonical Afisha loop still sends exactly once per configured recipient and
sets the same marker after the same send attempt behavior as before. Existing
calendar projections remain excluded, so source plus Vova/Sasha projections do
not become three logical reminders.

## Limitations and deferred work

V1 enriches scheduled Afisha reminders only. Manual calendar reminders and
immediate collaboration messages retain their current text. Ambiguous trips are
silently omitted. There is no multi-city reasoning, live transport status,
traffic, web/Places lookup, persisted weather, preferences UI, packing list,
generated advice, digest, independent trip scheduler, weather-change watcher,
morning weather stream, or other standalone proactive alert.
