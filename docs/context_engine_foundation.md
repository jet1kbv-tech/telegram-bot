# Context Engine Foundation v1

## Architecture audit and ownership

`JsonStorage` is the canonical normalized snapshot boundary. Manual Calendar events live under
`calendars.<owner>` and belong only to that owner. Afisha source records live under `afisha` and
active records are shared. Afisha-to-calendar synchronization creates read-only physical projections
for both owners (`source=afisha`, `source_id=<Afisha id>`); projections are not logical events.

Files have one canonical parent in `event_attachments`: either a manual Calendar event or an Afisha
source. The existing attachment parent resolver converts an Afisha calendar projection to its Afisha
source. The shared attachment visibility boundary admits an active Afisha parent or a manual parent
found in the requesting owner's calendar. Legacy `tickets` is not a Context Engine source.

Normalized attachment metadata includes semantic/media and transport types, origin, destination,
departure date/time (`date`, `departure_time`), arrival date/time, person, and delivery-only Telegram
metadata. Context documents intentionally omit Telegram file IDs, unique IDs, names, comments, creator
identity, and timestamps. Actual delivery remains in the attachment handler/service boundary.

Existing storage date parsers remain canonical for Calendar and Afisha start/end interpretation.
Existing NL entity/attachment query services already separate owner-manual events from active Afisha;
the engine follows those boundaries without calling NL providers. The current scheduler separately
scans active Afisha and non-projection manual events and mutates notification flags; v1 does not alter it.

## Read-only domain model and collection

`EventContext`, `DocumentContext`, `TripContext`, and `ContextBundle` are frozen values generated on
demand. Nothing is persisted and no storage root, cache, database, index, provider, Telegram API, Polza,
TMDb, weather call, or mutation is involved. Opaque IDs are SHA-256-derived from canonical identities.

Event collection includes only the actor's manual records plus active Afisha sources. It never scans
Afisha projections as events. By default an event whose effective end precedes injected `now` in the
injected timezone is excluded. Inclusive `date_from`/`date_to` bounds apply to event start dates.
Ordering is start time then canonical identity. Documents are restricted to the visible, range-selected
canonical parents, so a bounded bundle cannot accidentally include documents for an out-of-range event.

## Deterministic trip inference

A v1 trip requires a normalized `transport_ticket`, a non-empty structured destination, and a structured
departure or arrival date. Titles and file contents are never inspected. The ticket's canonical parent
event is linked directly (`same_parent`, `attachment_parent_event`). Tickets with the same normalized
destination link when their structured bounds are no more than three days apart. A structurally opposite
route links within 14 days: the later origin must equal the first destination and its destination must
equal the first origin. Creation order is irrelevant; records are first sorted by structured time.

The first route is `outbound`; only an exact opposite structured route is `return`; other linked routes
are `unknown`. Match reasons are explicit. A parent link or multi-ticket structured link is `strong`; a
standalone structured ticket is `medium`. Weak links are not produced or merged.

`trip_start` is the earliest structured departure (falling back to arrival); `trip_end` is the latest
structured arrival (falling back to departure), including a return ticket. Missing timestamps use midnight
only as a boundary for a known date. Durations, missing dates, and narrative dates are never inferred.

## Location normalization

NFKC, case folding, `ё`→`е`, punctuation-to-space, and whitespace collapse provide a stable raw key.
City extraction is deliberately allow-listed: an explicit standalone `воронеж` token maps Voronezh and
its named station variants to `Воронеж`. No city is invented. In particular `Москва` and `Московская
область` remain distinct. Unknown locations retain their full normalized text.

## Privacy and query API

Every bundle has one actor scope. Manual events and their documents cannot cross owner scope; active
Afisha and its documents are shared. Destination equality never broadens parent visibility. Trips are
inferred only after visibility filtering and therefore contain only actor-visible identities.

Public pure APIs are `build_context_bundle`, `collect_visible_events`, `collect_visible_documents`,
`infer_trip_contexts`, `find_trip_contexts`, `find_trip_by_destination`, `find_event_context`, and
`documents_for_context`. Diagnostic values contain counts and range presence only, safe for structural
logging; callers must not log payloads, routes, titles, destinations, people, or file IDs.

## Future contracts (not implemented)

A weather adapter may later consume an event's reliable `location_text`, date/range and time, or a trip's
`city_hint`, destination and structured trip boundaries. It must remain a separate optional adapter; v1
does not fetch or store weather.

A future notification flow can resolve an Afisha canonical identity with `find_event_context`, select a
linked trip, and optionally pass its safe location/time contract to weather before rendering. The existing
scheduler and notification UX are unchanged. A future NL flow should parse intent, issue a narrow structured
local query, and render deterministic results rather than sending storage to Polza.

## Known limitations

Only structured transport tickets seed trips. Voucher/accommodation association is deferred because the
current schema has no separately validated accommodation location contract. The city allow-list contains
only Voronezh; this is not a geocoder. Three-day forward linkage and 14-day exact opposite-route linkage
are intentionally conservative. Events without parseable start times are absent. Date bounds select parents,
not orphan documents. Context IDs are stable for unchanged identities but are ephemeral and not persisted.
