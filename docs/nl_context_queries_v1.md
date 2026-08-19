# NL Context Queries v1

## Architecture audit and reused boundaries

The canonical snapshot boundary is `JsonStorage`. Context Engine Foundation exposes the pure
`build_context_bundle`, visible-event/document collectors, trip inference and trip/event/document lookup
functions. Its frozen `ContextBundle` contains `EventContext`, privacy-redacted `DocumentContext`, inferred
`TripContext`, and structural diagnostics. Manual Calendar records are owner-private; active Afisha source
records are shared. Calendar Afisha projections are ignored as logical events, while attachment parent
resolution canonicalizes them back to the Afisha source. This prevents duplicate contextual results.

The integration reuses Context Engine destination normalization and its deliberately conservative Voronezh
`city_hint`, exact opposite-route return inference, three-/fourteen-day link windows, canonical ownership,
and attachment-to-event visibility. It also reuses the event-attachment date/time and transport presentation
helpers. Dates remain ISO strings in storage and become `date`/`time` values in context models; Telegram uses
Russian month labels. `BOT_TIMEZONE` and an injected zoned `now` are used throughout. The actor is resolved
from the allow-listed Telegram profile's `wishlist_owner`, never provider arguments.

Existing NL uses one Polza structured-output request, a strict decoder, read-only direct query branches, and
confirmation/pending operations for mutations. Existing attachment retrieval has its own actor-bound,
expiring opaque pending operation and revalidates visibility before file delivery; it remains unchanged.

## Contract and routing

`query_context` is one read-only intent with required enum `query_type` (`departure`, `arrival`, `return`,
`documents`, or `overview`) and nullable `destination` and `transport_type`. The latter has exactly the
bounded vocabulary `train`, `plane`, `bus`, and `other`; omission in the compact provider envelope represents
null. The Polza wire schema is strict and has `additionalProperties=false`, but its generic name/value carrier
cannot condition `value` on `name`. The generated semantic branch schema and canonical decoder therefore
enforce the enum after normalization and reject unknown fields, enum values, types, and an explicit empty
destination. The prompt teaches the identical English vocabulary and requires omission when transport is not
explicitly stated. Polza extracts only these semantics. It receives neither storage nor a context
bundle and never supplies actor identity or factual values.

Questions about stored schedule facts, document existence/count, and trip overview route to `query_context`.
An imperative request to send/open a file (for example, “пришли билет”) remains
`query_event_attachments`, preserving its canonical query and delivery UX. Calendar/Afisha list/count/next,
mutations, films, and recommendations retain their existing intents.

## Resolution and UX

Every factual lookup is local and read-only. Departure and arrival render only their separately stored date
and time; neither is calculated from the other, duration, or event dates. Return answers require the
Foundation's exact opposite-route inference and never use creation order. Overview includes only available
outbound, arrival, return, and document-count fields. Missing values and missing trips receive explicit
messages. Multiple trip matches are reported as unresolved rather than silently selected; v1 intentionally
does not add a second chooser/pending-state implementation.

The provider-call budget is exactly one parse for an incoming text and zero calls for resolution. Existing
attachment chooser callbacks and all local rendering make zero provider calls. Privacy filtering happens
before trip inference, so one actor cannot see another's Calendar event or document; active shared Afisha
remains visible once. Destination matching inherits Foundation v1's bounded normalization (currently only a
Voronezh city hint) and adds no fuzzy matching, geocoding, or external API.

Weather, search, maps, traffic, recommendations, and external travel data are deferred: this phase proves
that semantic parsing and factual authority stay separate and that answers are grounded only in canonical
local storage.
