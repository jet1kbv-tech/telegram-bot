from datetime import datetime, timedelta, timezone

from bot.services.event_attachment_query import query_event_attachments
from bot.services.event_attachments import delete_event_attachment, resolve_attachment_parent, update_event_attachment_metadata
from bot.services.nl_attachment_mutation_context import (
    clear_pending_mutation, create_pending_mutation, get_pending_mutation,
)


def data():
    return {
        "afisha": [{"id": "a", "title": "Поездка", "status": "active"}],
        "calendars": {"vova": [{"id": "projection", "source": "afisha", "source_id": "a"}], "sasha": []},
        "event_attachments": [
            {"id": "one", "parent_type": "afisha", "parent_event_id": "a", "semantic_type": "transport_ticket",
             "telegram_file_id": "file-one", "telegram_media_type": "document",
             "transport_type": "train", "origin": "Москва", "destination": "Воронеж", "date": "2026-08-30",
             "departure_time": "22:00", "arrival_date": "2026-08-31", "arrival_time": "09:33", "person": "vova"},
            {"id": "two", "parent_type": "afisha", "parent_event_id": "a", "semantic_type": "voucher",
             "telegram_file_id": "file-two", "telegram_media_type": "document"},
        ],
    }


def test_resolution_none_single_multiple_and_route_containment():
    stored = data()
    assert query_event_attachments(stored, owner="vova", destination="Курск").outcome == "none"
    assert query_event_attachments(stored, owner="vova", destination="воронеж").outcome == "single"
    assert query_event_attachments(stored, owner="vova").outcome == "multiple"
    assert query_event_attachments(stored, owner="vova", origin="Моск").outcome == "none"


def test_domain_delete_preserves_parent_and_other_attachment():
    stored = data()
    assert delete_event_attachment(stored, "one") is True
    assert delete_event_attachment(stored, "one") is False
    assert stored["afisha"][0]["id"] == "a"
    assert [item["id"] for item in stored["event_attachments"]] == ["two"]


def test_domain_update_preserves_unmentioned_fields_and_projection_is_canonical():
    stored = data()
    parent_type, parent_id, _ = resolve_attachment_parent(stored, "calendar", "projection")
    match = query_event_attachments(stored, owner="vova", canonical_parent=(parent_type, parent_id), destination="Воронеж")
    assert match.candidates[0].attachment_id == "one"
    update_event_attachment_metadata(stored, "one", arrival_time="09:40", date="2026-08-31")
    item = stored["event_attachments"][0]
    assert item["arrival_time"] == "09:40" and item["date"] == "2026-08-31"
    assert item["origin"] == "Москва" and item["telegram_file_id"] == "file-one"


def test_pending_actor_ttl_cancel_and_stale_callback_are_fail_closed():
    state = {}; now = datetime.now(timezone.utc)
    operation = create_pending_mutation(state, actor_key="vova", intent="delete_event_attachment", now=now,
                                        query={"destination": "Воронеж"}, changes={}, ttl_seconds=10)
    assert get_pending_mutation(state, actor_key="sasha", now=now, operation_id=operation.operation_id) is None
    operation = create_pending_mutation(state, actor_key="vova", intent="delete_event_attachment", now=now,
                                        query={}, changes={}, ttl_seconds=10)
    assert get_pending_mutation(state, actor_key="vova", now=now + timedelta(seconds=11), operation_id=operation.operation_id) is None
    operation = create_pending_mutation(state, actor_key="vova", intent="delete_event_attachment", now=now, query={}, changes={})
    clear_pending_mutation(state)
    assert get_pending_mutation(state, actor_key="vova", now=now, operation_id=operation.operation_id) is None
