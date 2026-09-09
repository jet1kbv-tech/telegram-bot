from datetime import datetime

import pytest

from bot.handlers.afisha import get_actual_afisha_items
from bot.services.context_engine import collect_visible_events
from bot.services.event_lifetime import get_effective_event_end, is_event_effectively_actual


NOW = datetime(2026, 9, 5, 12, 0)


def event(**changes):
    value = {
        "id": "san", "title": "Санаторий", "date": "2026-08-31", "time": "10:00",
        "end_date": "2026-09-04", "end_time": "18:00", "status": "active",
    }
    value.update(changes)
    return value


def transport(identity="return", **changes):
    value = {
        "id": identity, "parent_type": "afisha", "parent_event_id": "san",
        "semantic_type": "transport_ticket", "date": "2026-09-06",
        "departure_time": "08:00", "arrival_date": "2026-09-06", "arrival_time": "12:30",
    }
    value.update(changes)
    return value


def snapshot(attachments=(), afisha_event=None):
    source = afisha_event or event()
    projection = {
        "id": "projection", "owner": "vova", "title": source["title"],
        "date": source["date"], "start_time": source["time"], "end_time": "",
        "source": "afisha", "source_id": source["id"],
    }
    return {
        "afisha": [source], "calendars": {"vova": [projection], "sasha": []},
        "event_attachments": list(attachments), "tickets": {"active": [], "used": []},
    }


def test_event_without_attachments_keeps_existing_explicit_end_behavior():
    data = snapshot()
    assert get_effective_event_end(data, "afisha", data["afisha"][0]) == datetime(2026, 9, 4, 18)
    assert not is_event_effectively_actual(data, "afisha", data["afisha"][0], NOW)


@pytest.mark.parametrize("semantic_type", ["other", "voucher", "insurance", "reservation", "accommodation"])
def test_non_transport_attachment_does_not_extend_lifetime(semantic_type):
    document = transport(semantic_type=semantic_type, arrival_date="2027-12-31")
    data = snapshot([document])
    assert get_effective_event_end(data, "afisha", data["afisha"][0]) == datetime(2026, 9, 4, 18)


def test_outbound_transport_before_explicit_end_does_not_shorten_event():
    data = snapshot([transport(date="2026-08-31", arrival_date="2026-08-31")])
    assert get_effective_event_end(data, "afisha", data["afisha"][0]) == datetime(2026, 9, 4, 18)


def test_production_return_transport_regression_keeps_afisha_actual():
    data = snapshot([transport()])
    assert get_effective_event_end(data, "afisha", data["afisha"][0]) == datetime(2026, 9, 6, 12, 30)
    assert get_actual_afisha_items(data, NOW) == data["afisha"]


def test_latest_of_multiple_transport_endpoints_wins():
    documents = [transport("one", arrival_date="2026-09-05", arrival_time="20:00"),
                 transport("two", arrival_date="2026-09-07", arrival_time="09:15")]
    data = snapshot(documents)
    assert get_effective_event_end(data, "afisha", data["afisha"][0]) == datetime(2026, 9, 7, 9, 15)


def test_arrival_date_without_time_covers_the_whole_day():
    data = snapshot([transport(arrival_time=None)])
    end = get_effective_event_end(data, "afisha", data["afisha"][0])
    assert end == datetime.max.replace(year=2026, month=9, day=6)
    assert is_event_effectively_actual(data, "afisha", data["afisha"][0], datetime(2026, 9, 6, 23, 59))


def test_departure_is_fallback_when_arrival_date_is_absent():
    data = snapshot([transport(arrival_date=None, arrival_time=None, date="2026-09-06", departure_time="14:20")])
    assert get_effective_event_end(data, "afisha", data["afisha"][0]) == datetime(2026, 9, 6, 14, 20)


def test_departure_date_without_time_covers_the_whole_day():
    data = snapshot([transport(arrival_date=None, arrival_time=None, departure_time=None)])
    assert is_event_effectively_actual(data, "afisha", data["afisha"][0], datetime(2026, 9, 6, 23, 59))


def test_afisha_projection_resolves_source_lifecycle_without_owning_documents():
    data = snapshot([transport()])
    projection = data["calendars"]["vova"][0]
    assert is_event_effectively_actual(data, "calendar", projection, NOW)
    assert not any(document["parent_type"] == "calendar" for document in data["event_attachments"])


def test_manual_calendar_event_uses_its_own_transport():
    manual = {"id": "manual", "owner": "vova", "title": "Trip", "date": "2026-09-04",
              "start_time": "10:00", "end_time": "11:00", "source": "manual", "source_id": ""}
    document = transport(parent_type="calendar", parent_event_id="manual")
    data = {"afisha": [], "calendars": {"vova": [manual]}, "event_attachments": [document], "tickets": {}}
    assert is_event_effectively_actual(data, "calendar", manual, NOW)


def test_event_disappears_after_effective_transport_end():
    data = snapshot([transport()])
    assert not is_event_effectively_actual(data, "afisha", data["afisha"][0], datetime(2026, 9, 7))


def test_explicit_event_end_later_than_documents_wins():
    source = event(end_date="2026-09-10", end_time="17:00")
    data = snapshot([transport()], source)
    assert get_effective_event_end(data, "afisha", source) == datetime(2026, 9, 10, 17)


def test_context_engine_keeps_event_and_trip_actions_visible_through_return():
    data = snapshot([transport()])
    events = collect_visible_events(data, "vova", NOW, "UTC")
    assert [item.canonical_parent_id for item in events] == ["san"]


def test_linked_legacy_ticket_extends_afisha_without_migration():
    data = snapshot()
    data["tickets"]["used"] = [{"id": "legacy", "afisha_id": "san", "date": "2026-09-06", "time": "19:00"}]
    assert get_effective_event_end(data, "afisha", data["afisha"][0]) == datetime(2026, 9, 6, 19)
