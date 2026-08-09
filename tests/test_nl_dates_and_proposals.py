from datetime import datetime, timedelta

import pytest

from bot.services.nl_dates import DateExpressionError, resolve_date_expression, resolve_time_expression
from bot.services.nl_intent import IntentKind
from bot.services.nl_proposals import create_proposal, discard_proposal, get_proposal

NOW = datetime(2026, 8, 9, 12, 0)  # Sunday


@pytest.mark.parametrize(("expression", "expected"), [
    ("сегодня", "2026-08-09"),
    ("завтра", "2026-08-10"),
    ("послезавтра", "2026-08-11"),
    ("в пятницу", "2026-08-14"),
    ("в следующую субботу", "2026-08-22"),
    ("15 августа", "2026-08-15"),
    ("15.08", "2026-08-15"),
    ("2027-01-02", "2027-01-02"),
])
def test_russian_relative_dates(expression, expected):
    assert resolve_date_expression(expression, now=NOW, timezone="Europe/Moscow") == expected


@pytest.mark.parametrize(("expression", "expected"), [
    ("18:30", "18:30"), ("в 19", "19:00"), ("7 вечера", "19:00"), ("вечером", "19:00"),
])
def test_russian_times(expression, expected):
    assert resolve_time_expression(expression) == expected


def test_unsupported_date_is_rejected():
    with pytest.raises(DateExpressionError):
        resolve_date_expression("когда-нибудь", now=NOW, timezone="Europe/Moscow")


def test_proposal_expiration_actor_binding_and_cancel():
    data = {}
    proposal = create_proposal(data, intent=IntentKind.ADD_PURCHASE, arguments={"title": "X"}, actor_key="a",
                               now=NOW, ttl_seconds=60)
    assert len(f"ai:c:{proposal.proposal_id}".encode()) <= 64
    assert get_proposal(data, proposal.proposal_id, actor_key="b", now=NOW) is None
    # Actor mismatch removes the proposal defensively, so create a second one for expiry/cancel checks.
    proposal = create_proposal(data, intent=IntentKind.ADD_PURCHASE, arguments={"title": "X"}, actor_key="a",
                               now=NOW, ttl_seconds=60)
    assert get_proposal(data, proposal.proposal_id, actor_key="a", now=NOW + timedelta(seconds=61)) is None
    proposal = create_proposal(data, intent=IntentKind.ADD_PURCHASE, arguments={"title": "X"}, actor_key="a",
                               now=NOW, ttl_seconds=60)
    discard_proposal(data, proposal)
    assert get_proposal(data, proposal.proposal_id, actor_key="a", now=NOW) is None
