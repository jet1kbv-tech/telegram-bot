"""Derived event lifecycle based on explicit dates and linked transport."""
from __future__ import annotations

from datetime import datetime, time
from typing import Any

from bot.storage import (
    find_item,
    parse_calendar_event_end_dt,
    parse_event_end_dt,
    parse_event_dt,
)


def _dated_endpoint(day_value: Any, time_value: Any) -> datetime | None:
    """Parse a document endpoint, treating a date without a time as its full day."""
    if not day_value:
        return None
    try:
        day = datetime.strptime(str(day_value), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None
    if time_value:
        try:
            clock = datetime.strptime(str(time_value), "%H:%M").time()
        except (TypeError, ValueError):
            return None
    else:
        clock = time.max
    return datetime.combine(day, clock)


def transport_endpoint(document: dict[str, Any]) -> datetime | None:
    """Return a transport's arrival, or its departure when arrival is absent."""
    if document.get("semantic_type") != "transport_ticket":
        return None
    if document.get("arrival_date"):
        return _dated_endpoint(document.get("arrival_date"), document.get("arrival_time"))
    return _dated_endpoint(document.get("date"), document.get("departure_time"))


def _canonical_event(data: dict[str, Any], parent_type: str,
                     event: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    if parent_type == "afisha":
        return "afisha", event
    if parent_type != "calendar":
        return None
    if event.get("source") == "afisha" and event.get("source_id"):
        source = find_item(data.get("afisha", []), str(event["source_id"]))
        return ("afisha", source) if source else None
    return "calendar", event


def get_effective_event_end(data: dict[str, Any], parent_type: str,
                            event: dict[str, Any]) -> datetime | None:
    """Calculate lifecycle end without modifying the event's explicit fields."""
    resolved = _canonical_event(data, parent_type, event)
    if not resolved:
        return None
    canonical_type, canonical_event = resolved
    event_id = str(canonical_event.get("id") or "")
    explicit_end = (
        (parse_event_end_dt(canonical_event) or parse_event_dt(canonical_event))
        if canonical_type == "afisha"
        else parse_calendar_event_end_dt(canonical_event)
    )
    endpoints = [explicit_end] if explicit_end else []
    endpoints.extend(
        endpoint
        for attachment in data.get("event_attachments", [])
        if isinstance(attachment, dict)
        and attachment.get("parent_type") == canonical_type
        and str(attachment.get("parent_event_id") or "") == event_id
        and (endpoint := transport_endpoint(attachment)) is not None
    )

    # The still-supported legacy ticket model has no arrival field, but its
    # explicit date/time and afisha_id safely identify a linked transport point.
    if canonical_type == "afisha":
        for bucket in ("active", "used"):
            for ticket in data.get("tickets", {}).get(bucket, []):
                if str(ticket.get("afisha_id") or "") == event_id:
                    endpoint = _dated_endpoint(ticket.get("date"), ticket.get("time"))
                    if endpoint:
                        endpoints.append(endpoint)
    return max(endpoints) if endpoints else None


def is_event_effectively_actual(data: dict[str, Any], parent_type: str,
                                event: dict[str, Any], now: datetime | None = None) -> bool:
    """Apply canonical status and the data-aware derived lifecycle end."""
    resolved = _canonical_event(data, parent_type, event)
    if not resolved:
        return False
    canonical_type, canonical_event = resolved
    if canonical_type == "afisha" and canonical_event.get("status") != "active":
        return False
    end = get_effective_event_end(data, parent_type, event)
    return bool(end and end >= (now or datetime.now()))
