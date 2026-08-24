# Contextual Assistant Actions v2 — trip cards

## Architecture and entry points

V2 is a read-only presentation layer over the existing Context Engine. It does not add storage, trip inference, search, or authorization rules. An event's existing **🚆 Поездка** action opens a card only after the existing conservative `select_event_trip` produces one result. A successful, unambiguous NL `query_context` result offers **🧳 Открыть поездку**; ambiguous results retain the established controlled response.

## Card UX and identity

A card is identified only by the canonical opaque `TripContext.context_id`. Trip callbacks use `ctx:trip:<action>:<context-id>` (`card`, `weather`, `docs`, `route`, or `overview`); they contain no route, place, file, attachment, Telegram, or provider data and remain below Telegram's 64-byte limit.

The Russian card displays only stored destination/city hint, departure, route endpoints, arrival, visible linked event information, and canonical visible document count. Missing destinations produce the neutral `🧳 Поездка` title. One linked event is displayed and receives **📅 К событию**. Multiple visible events are counted and never silently reduced to one.

Buttons are:

- **🌦 Погода**, **📎 Документы**;
- **🚆 Маршрут**, **🗓 Что известно**;
- optionally **📅 К событию**;
- **⬅️ Назад**, **🏠 В меню**.

An event-origin card returns directly to that event. Internal action pages return to the freshly rendered card. NL cards use the stable main menu as their fallback surface.

## Visibility, stale state, and privacy

Every callback loads storage again, rebuilds the Context Engine for the authenticated actor, and resolves the opaque canonical ID in that actor's bundle. Callback data is never authorization. Manual Calendar context therefore stays owner-private, while active Afisha context retains its existing shared visibility. Missing, deleted, inactive, or newly invisible trips all return the same non-disclosing message: `Эта поездка больше недоступна.`

## Actions

### Weather

Weather reuses the configured `WeatherProvider` and existing forecast formatter. Location precedence is `city_hint`, then stored `destination`; there is no Moscow or linked-event fallback. Date precedence is stored arrival date, then departure date. Missing coordinates produce the controlled missing-data response. Provider errors and forecast-horizon errors reuse existing controlled Russian messages.

### Documents

Documents come only from canonical Context Engine visibility: the trip's linked attachments plus visible canonical attachments belonging to its visible linked events. Legacy Tickets storage is not read. The established attachment list/detail/send UI is called through a reusable read-only boundary; add, edit, recognition, and delete controls are suppressed. A fresh callback reflects deletions and an empty result returns `К этой поездке больше нет сохранённых документов.`

### Route and overview

Route and overview are deterministic local renderers. Route emits only stored departure, endpoint(s), and arrival. It never calculates duration, transfers, maps, accommodation, or missing endpoints. Overview combines the same facts with safely visible linked-event information and document count; it invokes no provider.

## Provider call budget and logging

`weather` permits exactly one WeatherProvider forecast request. `card`, `docs`, `route`, `overview`, and navigation permit zero external calls. No direct trip button calls Polza. Structural logs contain action, outcome, document count, and linked-event count only; they never contain user IDs, titles, places, routes, filenames, attachment IDs, or payloads.

## Production smoke checklist

1. Open an Afisha event with one outbound ticket and tap **🚆 Поездка**.
2. Confirm arrival, outbound route, linked event, and document count match storage.
3. Confirm a later unrelated/return segment is absent from that route card.
4. Check weather targets the explicit destination and arrival date; check horizon and provider failures.
5. Delete a related document, reopen **📎 Документы**, and confirm it disappeared.
6. Delete/deactivate the trip source and confirm the uniform stale message.
7. Confirm the other actor cannot open an owner-private Calendar trip, but both can open a shared Afisha trip.
8. Confirm multiple linked events show a count and no event shortcut.
9. Confirm document detail offers send/back only and no mutation controls.
10. Confirm card, route, overview, docs, and navigation make no Polza requests.
