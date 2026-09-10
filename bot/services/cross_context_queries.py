"""Small, deterministic cross-domain joins over an actor-scoped bundle."""
from __future__ import annotations

from datetime import date, datetime, time

from bot.services.context_engine import ContextBundle, DocumentContext, EventContext, TripContext
from bot.services.nl_dates import resolve_date_range

RESULT_LIMIT = 10


def event_bounds(event: EventContext) -> tuple[datetime, datetime]:
    start = datetime.combine(event.date, event.start_time or time.min)
    if event.effective_end is not None:
        end = event.effective_end
    elif event.end_date is not None:
        end = datetime.combine(event.end_date, event.end_time or time.max)
    else:
        end = datetime.combine(event.date, event.end_time or event.start_time or time.max)
    return start, max(start, end)


def trip_documents(bundle: ContextBundle, trip: TripContext) -> tuple[DocumentContext, ...]:
    """Return visible documents on either the trip segments or their canonical parents."""
    attachment_ids = set(trip.linked_attachment_ids)
    event_ids = set(trip.linked_event_ids)
    parents = {(event.canonical_parent_type, event.canonical_parent_id)
               for event in bundle.events if event.context_id in event_ids}
    return tuple(document for document in bundle.documents
                 if document.attachment_id in attachment_ids
                 or (document.parent_type, document.parent_id) in parents)


def direction_document(bundle: ContextBundle, trip: TripContext, direction: str) -> DocumentContext | None:
    identities = {identity for identity, value in trip.directions if value == direction}
    matches = [document for document in bundle.documents if document.attachment_id in identities]
    return matches[0] if len(matches) == 1 else None


def trip_interval(bundle: ContextBundle, trip: TripContext) -> tuple[datetime, datetime] | None:
    outbound = direction_document(bundle, trip, "outbound")
    if outbound is None or outbound.departure_date is None:
        return None
    start = datetime.combine(outbound.departure_date, outbound.departure_time or time.min)
    returning = direction_document(bundle, trip, "return")
    if returning is not None:
        if returning.arrival_date is not None:
            return start, datetime.combine(returning.arrival_date, returning.arrival_time or time.max)
        if returning.departure_date is not None:
            return start, datetime.combine(returning.departure_date, returning.departure_time or time.min)
        return None
    # Without a structured return, outbound arrival is only one defensible end
    # candidate.  A canonical trip event can explicitly span beyond the travel
    # day (for example Sep 12-15 while only the outbound ticket is attached),
    # so retain the latest bound belonging to this trip rather than collapsing
    # the stay to the arrival instant.  Unlinked events are never considered.
    candidates = []
    if outbound.arrival_date is not None:
        candidates.append(datetime.combine(
            outbound.arrival_date, outbound.arrival_time or time.max))
    linked_ids = set(trip.linked_event_ids)
    candidates.extend(
        event.effective_end
        for event in bundle.events
        if event.context_id in linked_ids
        and event.effective_end is not None
        and event.effective_end >= start
    )
    return (start, max(candidates)) if candidates else None


def events_overlapping_trip(bundle: ContextBundle, trip: TripContext) -> tuple[EventContext, ...] | None:
    interval = trip_interval(bundle, trip)
    if interval is None:
        return None
    linked = set(trip.linked_event_ids)
    start, end = interval
    rows = [event for event in bundle.events
            if event.context_id not in linked
            and event_bounds(event)[0] <= end and event_bounds(event)[1] >= start]
    return tuple(sorted(rows, key=lambda event: (event_bounds(event)[0], event.canonical_parent_type,
                                                  event.canonical_parent_id)))


def events_on_arrival(bundle: ContextBundle, trip: TripContext) -> tuple[EventContext, ...] | None:
    outbound = direction_document(bundle, trip, "outbound")
    if outbound is None or outbound.arrival_date is None:
        return None
    linked = set(trip.linked_event_ids)
    arrival_start = datetime.combine(outbound.arrival_date, time.min)
    arrival_end = datetime.combine(outbound.arrival_date, time.max)
    rows = [event for event in bundle.events if event.context_id not in linked
            and event_bounds(event)[0] <= arrival_end and event_bounds(event)[1] >= arrival_start]
    return tuple(sorted(rows, key=lambda event: (event_bounds(event)[0], event.canonical_parent_type,
                                                  event.canonical_parent_id)))


def date_bounds(expression: str | None, now: datetime, timezone: str) -> tuple[date, date] | None:
    if not expression:
        return None
    lower, upper = resolve_date_range(expression, now=now, timezone=timezone)
    return date.fromisoformat(lower), date.fromisoformat(upper)


def in_range(day: date, bounds: tuple[date, date] | None) -> bool:
    return bounds is None or bounds[0] <= day <= bounds[1]


def limited(rows: tuple[EventContext, ...] | tuple[TripContext, ...]):
    return rows[:RESULT_LIMIT], max(0, len(rows) - RESULT_LIMIT)
