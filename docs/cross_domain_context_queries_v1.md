# FEAT-08: cross-domain context queries v1

FEAT-08 is a read-only set of deterministic joins over one actor-scoped
`ContextBundle`. The provider only selects a bounded query family and explicit
filters; it never receives storage JSON, identifiers, candidates, or answer
facts.

## Contract

`query_context` adds `semantic_type` (`transport_ticket`, `voucher`,
`reservation`, `insurance`, `other`) and these query types:

* `events_during_trip`
* `events_on_trip_arrival`
* `trips_missing_documents`
* `trips_missing_return`
* `events_with_documents`
* `events_without_documents`
* `events_with_document_type`

Unknown document types fail strict decoding. There are no arbitrary predicates,
provider-supplied IDs, semantic search, or generic query language.

## Deterministic semantics

Trip resolution uses visible `TripContext` objects and requires one match for
trip-anchored questions. Multiple matches produce clarification. The trip
interval starts at outbound departure. It ends at return arrival, then return
departure when arrival is absent. Without a return, outbound arrival is used;
if absent, a later linked event effective end is the last defensible fallback.
Otherwise the answer explicitly reports that the end is unknown.

Event overlap is inclusive and compares normalized local `datetime` bounds.
The event end is the BUG-01 effective lifetime, falling back to explicit end or
the full event day. Trip parent events are excluded from their own plan list.
Calendar Afisha projections never enter the bundle, so shared Afisha appears
once.

Arrival day requires the structured outbound `arrival_date`; v1 deliberately
does not infer it from departure or a title. A trip is “missing return” only
when it has one unambiguous structured outbound segment and no structured
return segment. The schema has no one-way-complete flag, so that phrase means
“no structured return transport segment attached”.

Trip document coverage comprises visible structured segments plus all visible
canonical documents attached to the segments' linked event parents. “Without
documents” means zero such documents. A requested type checks only that exact
canonical semantic type; there is no preparation checklist. Since v1
`TripContext` is seeded by a structured transport ticket, an inferred trip
normally cannot be missing all documents or its outbound transport ticket.
Event admission tickets have no separate taxonomy: `transport_ticket` remains
transport-only and generic event files remain `other` unless explicitly typed.

All event candidates come from canonical visible events. Manual Calendar data
is owner-scoped and shared Afisha is visible under existing policy. `person`
only narrows that actor-scoped set: another person's name (or `both`) permits
shared events, never that person's private Calendar. It cannot widen access.

Date filters reuse `resolve_date_range`. Existing single dates, today,
tomorrow, weekends and ISO Monday–Sunday week ranges remain supported.
“Ближайший месяц” / “ближайшие 30 дней” means today through today + 30 days,
inclusive. “Следующий месяц” is the next calendar month. Results sort by local
date/time and stable canonical identity, show at most ten rows, and report the
remainder.

Only a successful exactly-one-trip anchored query exposes the existing
`ContextQueryResult.subject`, allowing FEAT-09 to retain that trip. List queries
have no subject and establish no session. Query execution does not mutate any
domain collection; the only permitted update is the existing short-lived
context pointer.

## Examples and limitations

Supported classifications include “Что запланировано на время поездки в
Питер?”, “Что в день приезда в Питер?”, “Какие поездки без обратного билета?”,
“Какие поездки в ближайший месяц без страховки?”, “Какие события с
документами?” and “Какие события на этих выходных с бронью?”.

“Какие поездки не полностью подготовлены?” remains unsupported because there
is no deterministic required-document checklist. Event-ticket versus transport
ticket inference, one-way-complete inference, guessed arrival dates, semantic
search, contextual mutations, and proactive recommendations are deferred.
