from bot.services.event_attachment_query import query_event_attachments, route_contains


def data():
    return {"afisha": [{"id": "a", "title": "Санаторий", "status": "active"}],
            "calendars": {"vova": [{"id": "c", "title": "Поездка", "source": "manual"}], "sasha": []},
            "event_attachments": [
        {"id": "1", "parent_type": "calendar", "parent_event_id": "c", "semantic_type": "transport_ticket",
         "transport_type": "train", "origin": "Москва Казанская", "destination": "Придача, Воронеж-Южный",
         "date": "2026-08-30", "person": "vova"},
        {"id": "2", "parent_type": "afisha", "parent_event_id": "a", "semantic_type": "voucher"},
    ]}


def test_conservative_route_matching_rules():
    assert route_contains("Москва Казанская", "  МОСКВА ")
    assert route_contains("Придача, Воронеж-Южный", "Воронеж")
    assert route_contains("Орёл", "орел")
    assert not route_contains("Москва Казанская", "Воронеж")
    assert not route_contains("Московская", "Москва")


def test_exact_and_combined_filters_and_reasons():
    result = query_event_attachments(data(), owner="vova", semantic_type="transport_ticket",
        transport_type="train", origin="Москва", destination="Воронеж", date="2026-08-30", person="vova")
    assert result.outcome == "single"
    assert result.candidates[0].attachment_id == "1"
    assert result.candidates[0].reasons == ("semantic_type", "transport_type", "date", "person", "origin", "destination")
    assert query_event_attachments(data(), owner="vova", destination="Курск").outcome == "none"
    assert query_event_attachments(data(), owner="vova", date="2026-08-31").outcome == "none"


def test_visibility_no_filters_projection_and_parent_restriction():
    assert [c.attachment_id for c in query_event_attachments(data(), owner="vova").candidates] == ["1", "2"]
    assert [c.attachment_id for c in query_event_attachments(data(), owner="sasha").candidates] == ["2"]
    # Shared Afisha source is canonical and appears once, not once per calendar projection.
    assert [c.attachment_id for c in query_event_attachments(data(), owner="sasha", canonical_parent=("afisha", "a")).candidates] == ["2"]


def test_stable_bounded_order():
    value = data(); value["event_attachments"] = [dict(value["event_attachments"][1], id=str(i)) for i in range(12)]
    result = query_event_attachments(value, owner="vova", limit=3)
    assert [c.attachment_id for c in result.candidates] == ["0", "1", "2"]
    assert result.total_count == 12 and result.bounded and result.outcome == "multiple"
