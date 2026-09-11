# Smart Trip Briefing v1

## Architecture audit and reuse

FEAT-10 is a bounded `query_context` capability. The provider may classify
`trip_briefing`, copy an explicit destination, transport type or date expression,
and mark a genuine short follow-up. It never receives storage and never selects a
canonical ID or computes facts.

The implementation reuses the actor-scoped, read-only Context Engine projection.
`TripContext` continues to be inferred only from visible structured transport
tickets. Destination matching, including the existing `Питер`/`СПб` alias, is
canonical Context Engine behavior. FEAT-08 supplies directional document lookup,
trip document coverage, effective trip intervals and event overlap. Calendar
Afisha projections are already removed by the Context Engine, and linked source
events are removed by the overlap helper.

Contextual trip cards remain a separate drill-down UI. Proactive reminders remain
unchanged: the 24-hour reminder may contain route, arrival, document count and
one arrival-weather day; the compact two-hour reminder does not request weather.

## Resolution and output

Every request rebuilds a fresh bundle for the application-derived actor. A
self-contained request filters trips by exact normalized destination, departure
date range and explicit transport type. Zero results are not found; multiple
results produce a dated clarification; exactly one produces a briefing. A
follow-up resolves the existing FEAT-09 trip pointer only inside the freshly
rebuilt actor bundle.

The pure briefing model contains:

* canonical destination and the FEAT-08 reliable interval;
* structured outbound and optional structured return transport;
* at most five overlapping canonical plans, in deterministic order;
* segment and linked-parent documents, deduplicated by canonical attachment ID
  and rendered with human labels.

No internal identifiers or semantic type names are rendered. A missing return is
reported neutrally. Missing preparation recommendations are never invented.

## Weather, privacy and state

Weather is optional enrichment through the existing injected provider. Its input
is only canonical destination plus the reliable trip date interval. Provider
horizon, unavailable, malformed and runtime failures all omit the section while
preserving the core briefing.

Private Calendar context remains owner-only; active Afisha context is shared.
Provider `person` output cannot widen visibility. FEAT-10 does not mutate domain
collections or add a cache/persistence layer. Only an existing FEAT-09 pointer is
written after a successful unique result. Failed or ambiguous self-contained
requests preserve the current pointer; expired or newly invisible pointed trips
fail closed according to the existing session contract.

## Deferred behavior

The ambiguous city-only phrase `Что у нас по Питеру?` is deliberately not promoted
in the provider prompt because it can collide with general contextual event
questions. Explicit trip wording and established-trip follow-ups are supported.
Weather is omitted rather than displaying an unavailable placeholder.
