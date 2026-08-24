# Proactive Trip Reminders v2

## Scope and architecture

One scheduler job scans fresh persistent storage and rebuilds the canonical Context Engine bundle separately for every configured actor. It sends exactly `trip_24h` and `trip_2h`; it does not infer trips, merge segments, call an LLM, or replace Afisha reminders. Every canonical `TripContext` has its own lifecycle.

## Timing and timezone

The proactive trip job scans every 15 minutes. Both reminder targets use the half-open window `target <= now < target + 30 minutes`: this normally provides two scan opportunities for scheduler jitter while keeping the fixed “in two hours” message close to its stated target and preventing stale catch-up. The separate Afisha notification job remains hourly. Targets are departure minus 24 hours and departure minus 2 hours. Both structured `departure_date` and `departure_time` are mandatory.

Transport clocks are interpreted in configured `BOT_TIMEZONE` (currently Europe/Moscow by default), consistently with existing naive stored event/transport clocks. V2 does not infer a timezone from a city; cross-timezone itineraries therefore retain this known limitation.

## Content and actions

The 24-hour message includes canonical departure, route and optional canonical arrival, trip-scoped document count, and optional arrival weather. Its location precedence is `city_hint`, then destination; its date precedence is arrival date, then departure date. There is no Moscow or event fallback. The two-hour message is deliberately compact and never requests weather.

Buttons reuse opaque Contextual Actions callbacks for trip cards, trip documents and trip weather. A direct canonical event callback is included only when exactly one actor-visible linked event exists. Zero or multiple events produce no event button. Callback data contains no route, city, user, filename, or provider data.

Only documents in `TripContext.linked_attachment_ids` are counted or exposed. Sharing an event parent is insufficient. A single document is described as a saved ticket only when canonical `semantic_type` is `transport_ticket`; otherwise neutral document wording is used.

## Identity, visibility, persistence, and rescheduling

Recipients come only from the existing configured-user-to-actor mapping and persisted chat mapping. Calendar context remains private to its owner; shared active Afisha context follows existing Context Engine visibility. Context is rebuilt on every scan, so removed, changed, or newly invisible trips are not sent from stale snapshots.

The persisted delivery identity is a SHA-256 digest of actor, opaque canonical trip ID, reminder type, and canonical zoned departure datetime. Weather and document state are excluded. Thus repeated scans/restarts do not resend the same schedule, while a changed departure creates a new version. Outbound and return `TripContext` IDs remain independent. Missing JSON metadata defaults safely, requiring no migration.

Telegram is called before the marker is saved. A rejected send is unmarked and can retry only while its eligibility window remains active. Successful sends are saved immediately. If that save itself fails, scanning continues and the in-memory marker prevents another send in that cycle, but a restart can duplicate the accepted message because durable acknowledgement was impossible; this conservative tradeoff avoids falsely claiming delivery.

## Failure isolation, provider budget, and privacy

Actor construction, each candidate, weather, Telegram delivery, and marker persistence have separate failure boundaries. Weather failure merely omits weather. Missing documents/events do not suppress a valid trip. At most one provider lookup occurs for each eligible, non-deduped 24-hour candidate and zero for a two-hour candidate.

Logs contain reminder type, structural outcomes, and aggregate cycle counts only. They never contain actor/chat IDs, canonical IDs, titles, routes, locations, filenames, attachments, weather payloads, message bodies, or credentials.

## Existing Afisha reminders

Existing event-centric Afisha reminders and markers are unchanged. An event reminder and a departure reminder can occur near one another when event start and transport departure align; V2 applies no broad suppression because they express different canonical subjects and no safe deterministic equivalence exists.

## Production smoke checklist

1. Confirm `BOT_TIMEZONE` matches the interpretation of stored transport clocks.
2. Confirm APScheduler/JobQueue is available and the named scan job is registered once.
3. Confirm each configured actor has a persisted chat mapping.
4. Create two independently ticketed segments and verify separate 24-hour/2-hour messages.
5. Verify each message exposes only its segment document and only an unambiguous event.
6. Simulate weather failure and confirm the transport notification still arrives.
7. Restart inside an undelivered window, then after an expired window, and verify bounded behavior.
8. Confirm successful marker persistence and structural, content-free logs.
