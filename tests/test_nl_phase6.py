from datetime import datetime, timedelta, timezone

import pytest

from bot.services.nl_dates import resolve_date_range
from bot.services.nl_intent import IntentKind
from bot.services.nl_intent_decoder import decode_intent
from bot.services.nl_query_contexts import create_query_context, get_query_context
from bot.services.queries import choose_random, next_event, query_afisha, query_calendar, query_films, query_purchases
from bot.storage import parse_calendar_event_start_dt


NOW = datetime(2026, 8, 10, 12, tzinfo=timezone.utc)  # Monday


def test_query_intents_are_strict_and_separate_from_mutations():
    query = decode_intent({"intent": "query_purchases", "arguments": {"status": "planned", "priority": "any", "buyer": "any", "operation": "list"}})
    add = decode_intent({"intent": "add_movie_or_tv", "arguments": {"query": "Интерстеллар"}})
    assert query.intent is IntentKind.QUERY_PURCHASES
    assert add.intent is IntentKind.ADD_MOVIE_OR_TV
    with pytest.raises(Exception):
        decode_intent({"intent": "query_purchases", "arguments": {"filters": {}}})
    with pytest.raises(Exception):
        decode_intent({"intent": "query_calendar", "arguments": {"date_from": None, "date_to": None, "target": None, "operation": "delete"}})


def test_purchase_filters_order_count_sum_and_missing_price():
    data = {"purchases": {"planned": [
        {"title": "Low", "priority": "low", "buyer": "Sasha", "price": None},
        {"title": "High", "priority": "high", "buyer": "Vova", "price": 100},
        {"title": "Free", "priority": "medium", "buyer": "", "price": "50 ₽"},
    ], "bought": [{"title": "Done", "priority": "high", "buyer": "Vova", "price": 20}]}}
    planned = query_purchases(data, status="planned", priority="any", buyer="any", actor_name="Vova")
    assert [item["title"] for item in planned.items] == ["High", "Free", "Low"]
    assert (planned.total, planned.amount, planned.missing_prices) == (3, 150, 1)
    assert query_purchases(data, status="bought", priority="any", buyer="any", actor_name="Vova").total == 1
    assert query_purchases(data, status="planned", priority="high", buyer="any", actor_name="Vova").total == 1
    assert query_purchases(data, status="planned", priority="any", buyer="current_user", actor_name="Vova").items[0]["title"] == "High"
    assert query_purchases(data, status="planned", priority="any", buyer="other_user", actor_name="Vova").items[0]["title"] == "Low"
    assert query_purchases(data, status="planned", priority="any", buyer="unassigned", actor_name="Vova").items[0]["title"] == "Free"
    assert query_purchases(data, status="planned", priority="high", buyer="unassigned", actor_name="Vova").total == 0


def test_film_filters_reuse_genres_count_random_and_empty():
    films = [
        {"title": "Movie", "status": "want", "media_type": "movie", "genres": [" Комедия "]},
        {"title": "TV", "status": "want", "media_type": "tv", "genres": ["КОМЕДИЯ"]},
        {"title": "Seen", "status": "watched", "media_type": "movie", "genres": ["Комедия"]},
    ]
    data = {"films": films}
    result = query_films(data, status="want", media_type="any", genre="комедия")
    assert result.items == films[:2]
    assert query_films(data, status="watched", media_type="any", genre=None).items == [films[2]]
    assert query_films(data, status="want", media_type="movie", genre=None).items == [films[0]]
    assert query_films(data, status="want", media_type="tv", genre=None).items == [films[1]]
    assert choose_random(result, lambda rows: rows[-1]) == films[1]
    assert choose_random(query_films(data, status="want", media_type="any", genre="Драма"), lambda _: pytest.fail()) is None


def test_calendar_is_actor_owner_isolated_includes_projection_and_orders():
    manual = {"title": "Dentist", "date": "2026-08-11", "start_time": "12:00", "source": "manual"}
    projection = {"title": "Concert", "date": "2026-08-11", "start_time": "10:00", "source": "afisha"}
    other = {"title": "Secret", "date": "2026-08-11", "start_time": "09:00"}
    data = {"calendars": {"vova": [manual, projection], "sasha": [other]}}
    result = query_calendar(data, owner="vova", date_from="2026-08-11", date_to="2026-08-11", target=None, now=NOW)
    assert result.items == [projection, manual]
    assert other not in result.items
    assert query_calendar(data, owner="vova", date_from=None, date_to=None, target="dentist", now=NOW).items == [manual]
    assert next_event(result, parse_calendar_event_start_dt, NOW) == projection


def test_afisha_uses_sources_ranges_target_count_order_and_empty():
    late = {"title": "Concert big", "date": "2026-08-20", "time": "20:00"}
    early = {"title": "Concert small", "date": "2026-08-11", "time": "18:00"}
    data = {"afisha": [late, early], "calendars": {"vova": [{**early, "start_time": "18:00"}]}}
    result = query_afisha(data, date_from="2026-08-01", date_to="2026-08-31", target="concert", now=NOW)
    assert result.items == [early, late]
    assert result.total == 2
    assert query_afisha(data, date_from="2026-09-01", date_to="2026-09-30", target=None, now=NOW).total == 0


@pytest.mark.parametrize("phrase, expected", [
    ("завтра", ("2026-08-11", "2026-08-11")),
    ("выходные", ("2026-08-15", "2026-08-16")),
    ("следующие выходные", ("2026-08-22", "2026-08-23")),
    ("эта неделя", ("2026-08-10", "2026-08-16")),
    ("следующая неделя", ("2026-08-17", "2026-08-23")),
    ("в августе", ("2026-08-01", "2026-08-31")),
])
def test_date_range_boundaries(phrase, expected):
    assert resolve_date_range(phrase, now=NOW, timezone="UTC") == expected


def test_query_context_actor_ttl_and_filters_only():
    user_data = {}
    args = {"status": "want", "media_type": "movie", "genre": None, "operation": "list"}
    value = create_query_context(user_data, intent=IntentKind.QUERY_FILMS, arguments=args, actor_key="vova", now=NOW, ttl_seconds=60)
    assert value.arguments == args
    assert get_query_context(user_data, value.token, actor_key="vova", now=NOW) == value
    assert get_query_context(user_data, value.token, actor_key="sasha", now=NOW) is None
    assert get_query_context(user_data, value.token, actor_key="vova", now=NOW + timedelta(seconds=61)) is None
    assert get_query_context(user_data, "stale", actor_key="vova", now=NOW) is None
