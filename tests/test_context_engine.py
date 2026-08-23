from datetime import datetime, timezone

from bot.services.context_engine import (
    build_context_bundle,
    documents_for_context,
    extract_city_hint,
    find_event_context,
    find_trip_by_destination,
    normalize_location_text,
)

NOW = datetime(2026, 8, 19, 12, tzinfo=timezone.utc)


def event(event_id, title, day, clock="10:00", **extra):
    return {"id": event_id, "title": title, "date": day, "time": clock, "status": "active", **extra}


def manual(event_id, owner, day, clock="10:00", **extra):
    return {"id": event_id, "owner": owner, "title": extra.pop("title", event_id), "date": day,
            "start_time": clock, "end_time": extra.pop("end_time", ""), "source": "manual", "source_id": "", **extra}


def projection(event_id, source_id, owner):
    return {"id": event_id, "owner": owner, "title": "projection", "date": "2026-08-30",
            "start_time": "23:38", "end_time": "", "source": "afisha", "source_id": source_id}


def attachment(attachment_id, parent_type, parent_id, **extra):
    return {"id": attachment_id, "parent_type": parent_type, "parent_event_id": parent_id,
            "telegram_media_type": "document", "semantic_type": "transport_ticket", **extra}


def data(*, afisha=(), vova=(), sasha=(), attachments=(), tickets=None):
    return {"afisha": list(afisha), "calendars": {"vova": list(vova), "sasha": list(sasha)},
            "event_attachments": list(attachments), "tickets": tickets or {"active": [], "used": []}}


def test_collects_owned_manual_and_shared_afisha_without_projections():
    source = event("a1", "Shared", "2026-08-30")
    snapshot = data(afisha=[source], vova=[manual("v1", "vova", "2026-08-20"), projection("pv", "a1", "vova")],
                    sasha=[manual("s1", "sasha", "2026-08-21"), projection("ps", "a1", "sasha")])
    vova = build_context_bundle(snapshot, "vova", NOW, "Europe/Moscow")
    sasha = build_context_bundle(snapshot, "sasha", NOW, "Europe/Moscow")
    assert [(row.source_type, row.canonical_parent_id) for row in vova.events] == [("calendar", "v1"), ("afisha", "a1")]
    assert {row.canonical_parent_id for row in sasha.events} == {"s1", "a1"}
    assert "v1" not in {row.canonical_parent_id for row in sasha.events}


def test_active_past_injected_now_and_date_bounds():
    snapshot = data(afisha=[event("past", "Past", "2026-08-18"), event("inactive", "No", "2026-08-20", status="done"),
                                    event("near", "Near", "2026-08-21"), event("far", "Far", "2026-09-01")])
    result = build_context_bundle(snapshot, "vova", NOW, "UTC", date_from="2026-08-20", date_to="2026-08-25")
    assert [row.canonical_parent_id for row in result.events] == ["near"]
    assert result.diagnostics.date_range_applied
    historical = build_context_bundle(snapshot, "vova", NOW, "UTC", include_past=True)
    assert "past" in {row.canonical_parent_id for row in historical.events}


def test_documents_inherit_parent_visibility_and_ignore_legacy_tickets():
    shared = event("a", "Shared", "2026-08-30")
    private = manual("v", "vova", "2026-08-30")
    attachments = [attachment("shared-doc", "afisha", "a", destination="Воронеж", date="2026-08-30"),
                   attachment("private-doc", "calendar", "v", destination="Казань", date="2026-08-30")]
    snapshot = data(afisha=[shared], vova=[private], attachments=attachments,
                    tickets={"active": [{"id": "legacy"}], "used": []})
    assert {row.attachment_id for row in build_context_bundle(snapshot, "vova", NOW, "UTC").documents} == {"shared-doc", "private-doc"}
    assert {row.attachment_id for row in build_context_bundle(snapshot, "sasha", NOW, "UTC").documents} == {"shared-doc"}


def test_production_voronezh_ticket_creates_parent_linked_trip():
    shared = event("a", "Поезд в Воронеж", "2026-08-30", "23:38")
    ticket = attachment("t", "afisha", "a", transport_type="train", origin="Москва Казанская",
        destination="Придача Воронеж Южный", date="2026-08-30", departure_time="23:38",
        arrival_date="2026-08-31", arrival_time="09:33", person="both")
    bundle = build_context_bundle(data(afisha=[shared], attachments=[ticket]), "vova", NOW, "Europe/Moscow")
    trip = bundle.trips[0]
    assert trip.city_hint == "Воронеж"
    assert trip.trip_start == datetime(2026, 8, 30, 23, 38)
    assert trip.trip_end == datetime(2026, 8, 31, 9, 33)
    assert trip.linked_attachment_ids == ("t",)
    assert trip.linked_event_ids == (bundle.events[0].context_id,)
    assert trip.confidence == "strong"
    assert {"same_parent", "attachment_parent_event"} <= set(trip.match_reasons)
    assert documents_for_context(bundle, trip)[0].attachment_id == "t"


def test_return_route_groups_structurally_and_sets_end_and_direction():
    shared = event("a", "Trip", "2026-08-30")
    outbound = attachment("out", "afisha", "a", origin="Москва", destination="Воронеж", date="2026-08-30", arrival_date="2026-08-31")
    returning = attachment("back", "afisha", "a", origin="Воронеж", destination="Москва", date="2026-09-05", departure_time="18:00")
    trip = build_context_bundle(data(afisha=[shared], attachments=[outbound, returning]), "sasha", NOW, "UTC").trips[0]
    assert trip.linked_attachment_ids == ("back", "out")
    assert trip.trip_end == datetime(2026, 9, 5, 18)
    assert dict(trip.directions) == {"out": "outbound", "back": "return"}
    assert "opposite_transport_route" in trip.match_reasons


def test_production_sanatorium_routes_remain_two_independent_trips():
    shared = event("a", "Санаторий", "2026-08-31")
    outbound = attachment("out", "afisha", "a", origin="Москва Казанская",
        destination="Придача Воронеж Южный", date="2026-08-30", departure_time="23:38",
        arrival_date="2026-08-31", arrival_time="09:33")
    returning = attachment("back", "afisha", "a", origin="Старый Оскол",
        destination="Москва ВК Восточный", date="2026-09-06", departure_time="21:18",
        arrival_date="2026-09-07", arrival_time="09:02")

    bundle = build_context_bundle(data(afisha=[shared], attachments=[outbound, returning]),
                                  "vova", NOW, "Europe/Moscow")

    assert [trip.linked_attachment_ids for trip in bundle.trips] == [("out",), ("back",)]
    assert all(trip.linked_event_ids == (bundle.events[0].context_id,) for trip in bundle.trips)


def test_same_city_months_apart_and_unrelated_destinations_are_separate_and_ordered():
    shared = event("a", "Trips", "2026-08-30")
    docs = [attachment("dec", "afisha", "a", origin="Москва", destination="Воронеж", date="2026-12-01"),
            attachment("sep", "afisha", "a", origin="Москва", destination="Воронеж-Южный", date="2026-09-01"),
            attachment("kaz", "afisha", "a", origin="Москва", destination="Казань", date="2026-09-02")]
    trips = build_context_bundle(data(afisha=[shared], attachments=docs), "vova", NOW, "UTC").trips
    assert [trip.linked_attachment_ids for trip in trips] == [("sep",), ("kaz",), ("dec",)]


def test_missing_destination_or_dates_do_not_create_trip():
    shared = event("a", "Trip", "2026-08-30")
    docs = [attachment("no-destination", "afisha", "a", date="2026-08-30"),
            attachment("no-date", "afisha", "a", destination="Воронеж"),
            attachment("voucher", "afisha", "a", semantic_type="voucher", destination="Воронеж", date="2026-08-30")]
    assert build_context_bundle(data(afisha=[shared], attachments=docs), "vova", NOW, "UTC").trips == ()


def test_location_normalization_is_conservative():
    assert normalize_location_text("Воронеж-Южный") == "воронеж южный"
    assert {extract_city_hint(value) for value in ("Воронеж", "Воронеж-Южный", "Придача Воронеж Южный")} == {"Воронеж"}
    assert extract_city_hint("Москва") is None
    assert normalize_location_text("Москва") != normalize_location_text("Московская область")


def test_query_helpers_find_event_trip_and_documents():
    shared = event("a", "Trip", "2026-08-30")
    ticket = attachment("t", "afisha", "a", destination="Придача Воронеж Южный", date="2026-08-30")
    bundle = build_context_bundle(data(afisha=[shared], attachments=[ticket]), "vova", NOW, "UTC")
    assert find_event_context(bundle, "afisha", "a") == bundle.events[0]
    assert find_trip_by_destination(bundle, "Воронеж") == bundle.trips
    assert documents_for_context(bundle, bundle.events[0]) == bundle.documents
