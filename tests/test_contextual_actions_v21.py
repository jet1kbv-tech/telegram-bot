from datetime import datetime

from bot.handlers.calendar import calendar_event_keyboard_for_item
from bot.handlers.contextual_actions import _trip_selector, contextual_action_rows
from bot.services.afisha_calendar_sync import build_afisha_projection_id
from bot.services.contextual_actions import resolve_event_action_context, resolve_trip_action_context


NOW = datetime(2026, 8, 23, 12)


def ticket(identity, origin, destination, day, departure, arrival_day, arrival):
    return {"id": identity, "parent_type": "afisha", "parent_event_id": "san",
            "semantic_type": "transport_ticket", "telegram_media_type": "document",
            "telegram_file_id": f"file-{identity}", "transport_type": "train",
            "origin": origin, "destination": destination, "date": day,
            "departure_time": departure, "arrival_date": arrival_day, "arrival_time": arrival}


def data():
    event = {"id": "san", "title": "Санаторий", "date": "2026-08-31", "time": "10:00",
             "status": "active"}
    projection_id = build_afisha_projection_id("san", "vova")
    projection = {"id": projection_id, "owner": "vova", "source": "afisha", "source_id": "san",
                  "title": "Санаторий", "date": "2026-08-31", "start_time": "10:00"}
    return {"afisha": [event], "calendars": {"vova": [projection], "sasha": []},
            "event_attachments": [
                ticket("return-ticket", "Старый Оскол", "Москва", "2026-09-06", "21:18", "2026-09-07", "09:02"),
                {"id": "voucher", "parent_type": "afisha", "parent_event_id": "san",
                 "semantic_type": "voucher", "telegram_media_type": "document", "telegram_file_id": "voucher-file"},
                ticket("out-ticket", "Москва", "Воронеж", "2026-08-30", "23:38", "2026-08-31", "09:33"),
            ]}


def labels(markup):
    return [button.text for row in markup.inline_keyboard for button in row]


def test_afisha_and_calendar_projection_have_equivalent_actions():
    snapshot = data()
    afisha = contextual_action_rows(snapshot, actor_key="vova", parent_type="afisha",
                                    parent_id="san", page=2, now=NOW)
    projection = snapshot["calendars"]["vova"][0]
    calendar = calendar_event_keyboard_for_item("vova", projection, 2, "vova", snapshot)
    assistant_labels = [text for text in labels(calendar)
                        if text in {"🌦 Погода", "📎 Документы", "🚆 Поездки", "🗓 Что известно"}]
    assert [button.text for row in afisha for button in row] == assistant_labels
    assert "🚆 Поездки" in assistant_labels


def test_selector_is_ordered_bounded_and_navigates_to_exact_source():
    snapshot = data()
    value = resolve_event_action_context(snapshot, actor_key="vova", parent_type="afisha",
        parent_id="san", now=NOW, timezone="Europe/Moscow")
    text, keyboard = _trip_selector(value, "p", 3, "vova")
    assert text.index("Москва → Воронеж") < text.index("Старый Оскол → Москва")
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert callbacks[-2] == f"cal_view|vova|{build_afisha_projection_id('san', 'vova')}|3"
    assert all(len(payload.encode()) <= 64 for payload in callbacks)
    assert not any(value in payload for payload in callbacks for value in ("Москва", "Воронеж", "ticket"))
    return_id = value.trips[1].context_id
    assert return_id in callbacks[1]
    return_trip = resolve_trip_action_context(snapshot, actor_key="vova", trip_context_id=return_id,
        now=NOW, timezone="Europe/Moscow")
    assert return_trip.trip.origin == "Старый Оскол"
    assert {document.attachment_id for document in return_trip.documents} == {"return-ticket"}
    assert value.document_count == 3


def test_single_trip_keeps_singular_action():
    snapshot = data()
    snapshot["event_attachments"] = [snapshot["event_attachments"][2]]
    rows = contextual_action_rows(snapshot, actor_key="vova", parent_type="afisha",
                                  parent_id="san", page=0, now=NOW)
    assert "🚆 Поездка" in [button.text for row in rows for button in row]
    assert "🚆 Поездки" not in [button.text for row in rows for button in row]
