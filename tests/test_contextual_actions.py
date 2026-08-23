from datetime import datetime

from bot.handlers.contextual_actions import contextual_action_rows
from bot.services.contextual_actions import render_overview, render_trip, resolve_event_action_context, visible_actions

NOW = datetime(2026, 8, 23, 12)


def event(event_id="a", **extra):
    return {"id": event_id, "title": extra.pop("title", "Психолог"), "date": "2026-08-31",
            "time": "10:00", "status": "active", "place": extra.pop("place", ""), **extra}


def document(document_id, **extra):
    return {"id": document_id, "parent_type": "afisha", "parent_event_id": "a",
            "semantic_type": extra.pop("semantic_type", "other"), "telegram_media_type": "document",
            "telegram_file_id": f"file-{document_id}", **extra}


def snapshot(*documents):
    return {"afisha": [event()], "calendars": {"vova": [], "sasha": []},
            "event_attachments": list(documents)}


def resolve(data, actor="vova"):
    return resolve_event_action_context(data, actor_key=actor, parent_type="afisha", parent_id="a",
                                        now=NOW, timezone="Europe/Moscow")


def labels(rows):
    return [button.text for row in rows for button in row]


def test_normal_event_keyboard_uses_moscow_fallback_without_dead_actions():
    value = resolve(snapshot())
    assert visible_actions(value) == ("weather", "overview")
    assert labels(contextual_action_rows(snapshot(), actor_key="vova", parent_type="afisha",
                  parent_id="a", page=0, now=NOW)) == ["🌦 Погода", "🗓 Что известно"]


def test_document_only_keyboard_has_documents_but_no_trip_and_no_duplicates():
    data = snapshot(document("voucher"))
    button_labels = labels(contextual_action_rows(data, actor_key="vova", parent_type="afisha",
                           parent_id="a", page=2, now=NOW))
    assert button_labels == ["🌦 Погода", "📎 Документы", "🗓 Что известно"]
    assert len(button_labels) == len(set(button_labels))


def test_sanatorium_selects_outbound_and_exposes_all_four_actions():
    outbound = document("out", semantic_type="transport_ticket", transport_type="train",
        origin="Москва Казанская", destination="Придача Воронеж Южный", date="2026-08-30",
        departure_time="23:38", arrival_date="2026-08-31", arrival_time="09:33")
    voucher = document("voucher")
    later = document("later", semantic_type="transport_ticket", transport_type="train",
        origin="Старый Оскол", destination="Москва", date="2026-09-06",
        departure_time="21:18", arrival_date="2026-09-07", arrival_time="09:02")
    data = snapshot(outbound, voucher, later)
    data["afisha"][0]["title"] = "Санаторий"
    value = resolve(data)
    assert visible_actions(value) == ("weather", "docs", "trip", "overview")
    assert value.document_count == 3
    assert value.trip.linked_attachment_ids == ("out",)
    trip_text = render_trip(value.trip)
    assert "Москва Казанская → Придача Воронеж Южный" in trip_text
    assert "31 августа · 09:33" in trip_text
    assert "6 сентября" not in trip_text and "Старый Оскол" not in trip_text
    assert "Документы: 3" in render_overview(value)


def test_ambiguous_exact_arrivals_hide_trip_action():
    first = document("one", semantic_type="transport_ticket", destination="Воронеж", date="2026-08-30",
                     arrival_date="2026-08-31")
    second = document("two", semantic_type="transport_ticket", destination="Казань", date="2026-08-30",
                      arrival_date="2026-08-31")
    value = resolve(snapshot(first, second))
    assert value.trip is None
    assert "trip" not in visible_actions(value)


def test_private_calendar_is_actor_scoped_and_shared_afisha_is_visible():
    data = snapshot()
    data["calendars"]["vova"] = [{"id": "private", "owner": "vova", "source": "manual",
        "title": "Личное", "date": "2026-08-31", "start_time": "10:00"}]
    assert resolve_event_action_context(data, actor_key="sasha", parent_type="calendar", parent_id="private",
                                        now=NOW, timezone="Europe/Moscow") is None
    assert resolve(data, actor="sasha") is not None


def test_stale_or_inactive_event_has_no_buttons():
    data = snapshot()
    data["afisha"][0]["status"] = "done"
    assert contextual_action_rows(data, actor_key="vova", parent_type="afisha", parent_id="a",
                                  page=0, now=NOW) == []
