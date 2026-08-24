from datetime import datetime

from bot.handlers.contextual_actions import trip_callback
from bot.services.contextual_actions import (
    render_trip_card,
    render_trip_overview,
    render_trip_route,
    resolve_trip_action_context,
    trip_weather_target,
)
from bot.services.context_engine import build_context_bundle


NOW = datetime(2026, 8, 23, 12)


def snapshot(*documents, shared=True):
    event = {"id": "san", "title": "Санаторий", "date": "2026-08-31", "time": "10:00",
             "status": "active", "place": ""}
    return {"afisha": [event] if shared else [],
            "calendars": {"vova": [] if shared else [{"id": "san", "owner": "vova", "source": "manual",
                "title": "Санаторий", "date": "2026-08-31", "start_time": "10:00"}], "sasha": []},
            "event_attachments": list(documents)}


def document(identity, *, parent="afisha", **values):
    return {"id": identity, "parent_type": parent, "parent_event_id": "san",
            "semantic_type": values.pop("semantic_type", "transport_ticket"),
            "telegram_media_type": "document", "telegram_file_id": f"file-{identity}", **values}


def outbound(parent="afisha"):
    return document("out", parent=parent, transport_type="train", origin="Москва Казанская",
                    destination="Придача Воронеж Южный", date="2026-08-30", departure_time="23:38",
                    arrival_date="2026-08-31", arrival_time="09:33")


def resolve(data, actor="vova"):
    trip_id = build_context_bundle(data, actor, NOW, "Europe/Moscow", include_past=True).trips[0].context_id
    return resolve_trip_action_context(data, actor_key=actor, trip_context_id=trip_id,
                                       now=NOW, timezone="Europe/Moscow")


def test_card_is_canonical_bounded_and_keeps_outbound_separate():
    back = document("back", origin="Старый Оскол", destination="Москва", date="2026-09-06",
                    departure_time="21:18", arrival_date="2026-09-07", arrival_time="09:02")
    value = resolve(snapshot(outbound(), back))
    text = render_trip_card(value)
    assert "🧳 Поездка в Воронеж" in text
    assert "Москва Казанская → Придача Воронеж Южный" in text
    assert "31 августа · 09:33" in text
    assert "Старый Оскол" not in text and "6 сентября" not in text
    payload = trip_callback("weather", value.trip.context_id)
    assert payload == f"ctx:trip:weather:{value.trip.context_id}"
    assert len(payload.encode()) <= 64


def test_route_and_overview_are_local_and_never_derive_duration():
    value = resolve(snapshot(outbound(), document("voucher", semantic_type="voucher")))
    route = render_trip_route(value.trip)
    overview = render_trip_overview(value)
    assert "Отправление:" in route and "Прибытие:" in route
    assert "длитель" not in route.casefold()
    assert "Документы: 1" in overview
    assert "Санаторий · 31 августа" in overview
    assert trip_weather_target(value.trip) == ("Воронеж", value.trip.arrival_date)


def test_weather_falls_back_to_departure_date_but_not_to_event_location():
    row = outbound()
    row.pop("arrival_date")
    row.pop("arrival_time")
    value = resolve(snapshot(row))
    assert trip_weather_target(value.trip) == ("Воронеж", value.trip.departure_date)


def test_documents_and_stale_visibility_are_rebuilt_from_fresh_storage():
    data = snapshot(outbound(), document("voucher", semantic_type="voucher"))
    value = resolve(data)
    trip_id = value.trip.context_id
    assert len(value.documents) == 1
    data["event_attachments"] = [outbound()]
    # Canonical identity changes when the ticket disappears, while deletion of
    # a related voucher is reflected without changing the trip identity.
    refreshed = resolve_trip_action_context(data, actor_key="vova", trip_context_id=trip_id,
                                            now=NOW, timezone="Europe/Moscow")
    assert len(refreshed.documents) == 1
    data["event_attachments"] = []
    assert resolve_trip_action_context(data, actor_key="vova", trip_context_id=trip_id,
                                       now=NOW, timezone="Europe/Moscow") is None


def test_private_calendar_trip_is_owner_only_and_afisha_trip_is_shared():
    private = snapshot(outbound("calendar"), shared=False)
    owner = resolve(private)
    assert resolve_trip_action_context(private, actor_key="sasha", trip_context_id=owner.trip.context_id,
                                       now=NOW, timezone="Europe/Moscow") is None
    shared = snapshot(outbound())
    vova = resolve(shared)
    assert resolve_trip_action_context(shared, actor_key="sasha", trip_context_id=vova.trip.context_id,
                                       now=NOW, timezone="Europe/Moscow") is not None


def test_multiple_linked_events_are_counted_not_selected():
    data = snapshot(outbound())
    data["afisha"].append({"id": "other", "title": "Другое", "date": "2026-08-31", "time": "12:00",
                           "status": "active"})
    data["event_attachments"].append(document("same", parent="afisha", origin="Москва",
        destination="Придача Воронеж Южный", date="2026-08-30", parent_event_id="other"))
    # Constructing the second parent explicitly avoids any UI-side selection.
    data["event_attachments"][-1]["parent_event_id"] = "other"
    value = resolve(data)
    assert len(value.linked_events) == 2
    assert "Связано событий: 2" in render_trip_card(value)
