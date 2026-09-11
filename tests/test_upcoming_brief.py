from copy import deepcopy
import asyncio
import json
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from bot.services.context_queries import execute_context_query, query_context
from bot.services.context_sessions import get_context_session
from bot.services.nl_intent import IntentContext, IntentParserInvalidOutput
from bot.services.nl_intent_decoder import decode_intent
from bot.services.polza_intent_parser import PolzaIntentParser, SYSTEM_PROMPT
from bot.services.upcoming_brief import render_upcoming_weather
from bot.services.weather import WeatherDay, WeatherForecast

NOW = datetime(2026, 9, 11, 9, tzinfo=timezone.utc)  # Friday


def afisha(identity, title, day, clock="10:00", **extra):
    return {"id": identity, "title": title, "date": day, "time": clock,
            "status": "active", **extra}


def doc(identity, parent, semantic="transport_ticket", **extra):
    return {"id": identity, "parent_type": "afisha", "parent_event_id": parent,
            "semantic_type": semantic, "telegram_media_type": "document", **extra}


def data(*, trip=True):
    rows = [afisha("a1", "Театр", "2026-09-13", "19:00")]
    documents = [doc("theatre-doc", "a1", "other")]
    if trip:
        rows.append(afisha("trip", "Поездка", "2026-09-14", "08:00",
                           end_date="2026-09-16", end_time="22:00"))
        documents += [
            doc("out", "trip", transport_type="train", origin="Москва", destination="Санкт-Петербург",
                date="2026-09-14", departure_time="08:00", arrival_date="2026-09-14", arrival_time="12:00"),
            doc("back", "trip", transport_type="train", origin="Санкт-Петербург", destination="Москва",
                date="2026-09-16", departure_time="18:00", arrival_date="2026-09-16", arrival_time="22:00"),
        ]
    return {"afisha": rows, "calendars": {"vova": [
        {"id": "private", "title": "Ужин", "date": "2026-09-11", "start_time": "19:00", "source": "manual"},
        {"id": "projection", "title": "Театр", "date": "2026-09-13", "start_time": "19:00", "source": "afisha", "source_id": "a1"},
    ], "sasha": [{"id": "secret", "title": "Секрет", "date": "2026-09-12", "start_time": "09:00", "source": "manual"}]},
            "event_attachments": documents, "meta": {"context_sessions": {}}}


def ask(snapshot, **arguments):
    return query_context(snapshot, actor_key="vova", now=NOW, timezone="Europe/Moscow",
                         query_type="upcoming_brief", **arguments)


@pytest.mark.parametrize(("expression", "range_text"), [
    (None, "11–17 сентября"), ("выходные", "12–13 сентября"),
    ("эта неделя", "7–13 сентября"), ("следующая неделя", "14–20 сентября"),
    ("13 сентября", "13 сентября"),
    ("12–15 сентября", "12–15 сентября"),
])
def test_supported_ranges_and_default_seven_days(expression, range_text):
    assert range_text in ask(data(), date_expression=expression).text


def test_visibility_projection_status_duplicates_and_ordering():
    snapshot = data(trip=False)
    snapshot["afisha"] += [
        afisha("early", "Бранч", "2026-09-13", "11:00"),
        afisha("a1", "Театр duplicate", "2026-09-13", "19:00"),
        afisha("done", "Готово", "2026-09-12", status="done"),
    ]
    text = ask(snapshot).text
    assert "Ужин" in text and "Театр" in text and "Секрет" not in text
    assert text.count("Театр") == 2  # event plus its positive document signal, never projection/duplicate
    assert "projection" not in text and "Готово" not in text
    assert text.index("Ужин") < text.index("Бранч") < text.index("Театр")


def test_person_can_only_narrow_to_shared_context():
    assert "Ужин" not in ask(data(trip=False), person="both").text
    assert "Театр" in ask(data(trip=False), person="both").text
    assert "Ужин" not in ask(data(trip=False), person="sasha").text
    sasha = query_context(data(trip=False), actor_key="sasha", now=NOW, timezone="Europe/Moscow",
                          query_type="upcoming_brief", person="vova")
    assert "Секрет" not in sasha.text and "Театр" in sasha.text


def test_limit_remainder_and_effective_multiday_overlap_uses_visible_date():
    snapshot = data(trip=False)
    snapshot["afisha"] = [afisha(f"e{i}", f"План {i}", "2026-09-12", f"{i:02d}:00") for i in range(9)]
    snapshot["afisha"].append(afisha("multi", "Фестиваль", "2026-09-09", "10:00",
                                      end_date="2026-09-12", end_time="20:00"))
    result = ask(snapshot, date_expression="выходные")
    assert len(result.upcoming_brief.events) == 8 and result.upcoming_brief.event_remainder == 2
    assert "Фестиваль" in result.text and "И ещё 2…" in result.text
    assert "Среда, 9 сентября" not in result.text
    assert result.text.index("Завтра") < result.text.index("Фестиваль")


def test_event_starting_inside_range_keeps_its_own_display_date():
    result = ask(data(trip=False), date_expression="выходные")
    assert "Воскресенье, 13 сентября\n• 19:00 — Театр" in result.text


def test_trip_interval_source_dedup_documents_and_generic_semantics():
    result = ask(data())
    assert len(result.upcoming_brief.trips) == 1
    assert "🚆 Санкт-Петербург · 14–16 сентября" in result.text
    assert "Поездка —" not in result.text
    assert "Санкт-Петербург — билеты туда и обратно" in result.text
    assert "Театр — есть вложение" in result.text
    assert "Театр — билет" not in result.text and "не хватает" not in result.text


def test_multiple_trips_are_deterministically_ordered_and_disable_weather():
    snapshot = data()
    snapshot["afisha"].append(afisha("trip2", "Ещё поездка", "2026-09-12", "06:00",
                                      end_date="2026-09-12", end_time="12:00"))
    snapshot["event_attachments"].append(doc("plane", "trip2", transport_type="plane",
        origin="Москва", destination="Казань", date="2026-09-12", departure_time="06:00",
        arrival_date="2026-09-12", arrival_time="08:00"))
    result = ask(snapshot)
    assert result.text.index("Казань") < result.text.index("Санкт-Петербург")
    assert result.upcoming_brief.weather_target is None


class Weather:
    horizon_days = 16

    def __init__(self, fail=False):
        self.fail, self.calls = fail, []

    async def get_forecast(self, location, lower, upper=None):
        self.calls.append((location, lower, upper))
        if self.fail:
            raise RuntimeError
        days = tuple(WeatherDay(lower + timedelta(days=i), 5, 12, None, None, "ясно", None)
                     for i in range(((upper or lower) - lower).days + 1))
        return WeatherForecast(location, days)


@pytest.mark.parametrize("fail", [False, True])
async def test_weather_one_bounded_request_and_failure_preserves_core(fail):
    result, provider = ask(data()), Weather(fail)
    enrichment = await render_upcoming_weather(result.upcoming_brief, provider, today=date(2026, 9, 11))
    assert bool(enrichment) is not fail
    assert provider.calls == [("Санкт-Петербург", date(2026, 9, 14), date(2026, 9, 16))]
    assert "Театр" in result.text


async def test_weather_omitted_without_location_or_outside_horizon():
    no_trip, provider = ask(data(trip=False)), Weather()
    assert await render_upcoming_weather(no_trip.upcoming_brief, provider) is None and provider.calls == []
    result, provider = ask(data()), Weather()
    provider.horizon_days = 1
    assert await render_upcoming_weather(result.upcoming_brief, provider, today=date(2026, 9, 11)) is None
    assert provider.calls == []


def test_read_only_and_existing_session_pointer_is_preserved():
    snapshot = data()
    execute_context_query(snapshot, actor_key="vova", now=NOW, timezone="Europe/Moscow",
                          query_type="event_time", target="Театр")
    pointer = deepcopy(get_context_session(snapshot, "vova", NOW))
    before = deepcopy(snapshot)
    result = execute_context_query(snapshot, actor_key="vova", now=NOW, timezone="Europe/Moscow",
                                   query_type="upcoming_brief")
    assert result.subject is None and snapshot == before
    assert get_context_session(snapshot, "vova", NOW) == pointer


def _payload(kind):
    return {"intent": "query_context", "arguments": {"query_type": kind, "destination": None,
        "transport_type": None, "target": None, "date_expression": None, "person": None,
        "semantic_type": None, "follow_up": False}}


def test_decoder_accepts_upcoming_and_rejects_unknown():
    assert decode_intent(_payload("upcoming_brief")).arguments["query_type"] == "upcoming_brief"
    with pytest.raises(IntentParserInvalidOutput):
        decode_intent(_payload("lifestyle_summary"))


@pytest.mark.parametrize(("phrase", "expression", "person"), [
    ("Что у меня впереди?", None, "self"),
    ("Покажи ближайшие планы", None, None),
    ("Что у нас на выходных?", "выходные", "both"),
    ("Собери сводку на эту неделю", "эта неделя", None),
])
def test_provider_examples_at_strict_boundary(phrase, expression, person):
    arguments = [{"name": "query_type", "value": "upcoming_brief"}]
    if expression:
        arguments.append({"name": "date_expression", "value": expression})
    if person:
        arguments.append({"name": "person", "value": person})
    envelope = {"intent": "query_context", "arguments": arguments}
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(
        200, json={"choices": [{"message": {"content": json.dumps(envelope)}}]})))
    parsed = asyncio.run(PolzaIntentParser(api_key="secret", model="model", client=client).parse(
        phrase, IntentContext("actor", NOW, "Europe/Moscow")))
    asyncio.run(client.aclose())
    assert parsed.arguments["query_type"] == "upcoming_brief"
    assert parsed.arguments["date_expression"] == expression and parsed.arguments["person"] == person


def test_prompt_preserves_simple_event_route_and_conservative_boundary():
    assert '«что у меня завтра?» -> query_context query_type="events"' in SYSTEM_PROMPT
    assert "расплывчатая беседа без просьбы о планах не является upcoming_brief" in SYSTEM_PROMPT
