from copy import deepcopy
import asyncio
import json
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from bot.services.context_queries import execute_context_query, query_context
from bot.services.context_sessions import CONTEXT_SESSION_TTL, get_context_session
from bot.services.nl_intent import IntentContext, IntentParserInvalidOutput
from bot.services.nl_intent_decoder import decode_intent
from bot.services.polza_intent_parser import PolzaIntentParser, SYSTEM_PROMPT
from bot.services.trip_briefing import render_weather_enrichment
from bot.services.weather import WeatherDay, WeatherForecast, WeatherHorizonUnavailable

NOW = datetime(2026, 9, 11, 9, tzinfo=timezone.utc)


def event(identity, title, day, clock="10:00", **extra):
    return {"id": identity, "title": title, "date": day, "time": clock, "status": "active", **extra}


def document(identity, parent="trip", semantic="transport_ticket", **extra):
    return {"id": identity, "parent_type": extra.pop("parent_type", "afisha"),
            "parent_event_id": parent, "semantic_type": semantic,
            "telegram_media_type": "document", **extra}


def snapshot(*, returning=True, arrival=True, plans=(), extra_documents=()):
    events = [event("trip", "Поездка", "2026-09-12", "08:00",
                    end_date="2026-09-15", end_time="22:00"), *plans]
    documents = [document("out", transport_type="train", origin="Москва",
        destination="Санкт-Петербург", date="2026-09-12", departure_time="08:00",
        arrival_date="2026-09-12" if arrival else None,
        arrival_time="12:00" if arrival else None)]
    if returning:
        documents.append(document("back", transport_type="train", origin="Санкт-Петербург",
            destination="Москва", date="2026-09-15", departure_time="18:00",
            arrival_date="2026-09-15", arrival_time="22:00"))
    return {"afisha": events, "calendars": {"vova": [], "sasha": []},
            "event_attachments": [*documents, *extra_documents], "meta": {"context_sessions": {}}}


def ask(data, *, actor="vova", follow_up=False, **arguments):
    return execute_context_query(data, actor_key=actor, now=NOW, timezone="Europe/Moscow",
                                 follow_up=follow_up, **arguments)


@pytest.mark.parametrize("destination", ["Санкт-Петербург", "Питер", "СПб"])
def test_unique_destination_alias_outbound_return_and_session(destination):
    data = snapshot(extra_documents=[
        document("reservation", semantic="reservation"),
        document("generic", semantic="other"),
    ])
    result = ask(data, query_type="trip_briefing", destination=destination)
    assert result.outcome == "found" and result.briefing is not None
    assert "📍 Санкт-Петербург" in result.text and "12–15 сентября" in result.text
    assert "Москва → Санкт-Петербург" in result.text and "08:00" in result.text and "12:00" in result.text
    assert "Санкт-Петербург → Москва" in result.text and "18:00" in result.text and "22:00" in result.text
    assert "Билет туда" in result.text and "Билет обратно" in result.text
    assert "Бронь" in result.text and "• Документ" in result.text
    assert "transport_ticket" not in result.text and "generic" not in result.text
    assert get_context_session(data, "vova", NOW).context_id == result.trip.context_id


def test_no_return_and_missing_arrival_render_cleanly():
    result = ask(snapshot(returning=False, arrival=False), query_type="trip_briefing", destination="Питер")
    assert result.outcome == "found"
    assert "Обратный транспорт не прикреплён" in result.text
    assert "08:00" in result.text and "12:00" not in result.text


def test_plans_exclude_source_projection_and_are_ordered_limited():
    plans = [event(f"p{i}", f"План {i}", f"2026-09-{day:02d}", f"{clock:02d}:00")
             for i, (day, clock) in enumerate([(14, 20), (12, 15), (13, 9), (13, 8), (14, 8), (15, 8)])]
    data = snapshot(plans=plans)
    data["calendars"]["vova"].append({"id": "projection", "title": "План 2", "date": "2026-09-13",
        "start_time": "09:00", "source": "afisha", "source_id": "p2"})
    result = ask(data, query_type="trip_briefing", destination="Питер")
    assert "Поездка —" not in result.text and result.text.count("План 2") == 1
    assert result.text.index("План 1") < result.text.index("План 3") < result.text.index("План 2")
    assert "План 5" not in result.text and "И ещё 1…" in result.text


def test_parent_documents_included_and_duplicate_identity_deduplicated():
    docs = [document("voucher", semantic="voucher"), document("insurance", semantic="insurance"),
            document("voucher", semantic="voucher")]
    result = ask(snapshot(extra_documents=docs), query_type="trip_briefing", destination="Питер")
    assert result.text.count("Ваучер") == 1 and result.text.count("Страховка") == 1


def test_multiple_not_found_date_and_transport_filters_preserve_session():
    data = snapshot()
    first = ask(data, query_type="trip_briefing", destination="Питер")
    pointer = get_context_session(data, "vova", NOW).context_id
    second = snapshot()
    second["afisha"][0]["id"] = "trip2"
    second["afisha"][0]["date"] = "2026-10-12"
    second["event_attachments"][0].update(id="out2", parent_event_id="trip2", date="2026-10-12",
                                          arrival_date="2026-10-12")
    second["event_attachments"][1].update(id="back2", parent_event_id="trip2", date="2026-10-15",
                                          arrival_date="2026-10-15")
    data["afisha"] += second["afisha"]
    data["event_attachments"] += second["event_attachments"]
    ambiguous = ask(data, query_type="trip_briefing", destination="Питер")
    assert ambiguous.outcome == "ambiguous" and "12 сентября" in ambiguous.text and "12 октября" in ambiguous.text
    assert get_context_session(data, "vova", NOW).context_id == pointer
    assert ask(data, query_type="trip_briefing", destination="Казань").outcome == "not_found"
    assert ask(data, query_type="trip_briefing", destination="Питер",
               date_expression="12 сентября").trip.context_id == first.trip.context_id
    assert ask(data, query_type="trip_briefing", destination="Питер", transport_type="plane").outcome == "not_found"


def test_follow_up_reauthorizes_expires_and_reflects_fresh_data():
    data = snapshot()
    ask(data, query_type="departure", destination="Питер")
    data["event_attachments"][0]["departure_time"] = "09:15"
    result = ask(data, follow_up=True, query_type="trip_briefing")
    assert result.outcome == "found" and "09:15" in result.text
    expired = execute_context_query(
        data, actor_key="vova", now=NOW + CONTEXT_SESSION_TTL + timedelta(seconds=1),
        timezone="Europe/Moscow", follow_up=True, query_type="trip_briefing")
    assert expired.outcome == "missing_context"


def test_deleted_and_forged_private_pointer_fail_actor_scope():
    private = snapshot()
    private["calendars"]["sasha"] = [{"id": "private", "title": "Питер", "date": "2026-09-12",
        "start_time": "08:00", "end_date": "2026-09-15", "end_time": "22:00", "source": "manual"}]
    private["afisha"] = []
    for item in private["event_attachments"]:
        item.update(parent_type="calendar", parent_event_id="private")
    ask(private, actor="sasha", query_type="trip_briefing", destination="Питер")
    private["meta"]["context_sessions"]["vova"] = deepcopy(private["meta"]["context_sessions"]["sasha"])
    assert ask(private, actor="vova", follow_up=True, query_type="trip_briefing").outcome == "unavailable"
    assert "vova" not in private["meta"]["context_sessions"]


class Weather:
    def __init__(self, mode="ok"):
        self.mode = mode
        self.calls = []

    async def get_forecast(self, location, date_from, date_to=None):
        self.calls.append((location, date_from, date_to))
        if self.mode == "fail":
            raise RuntimeError("provider")
        if self.mode == "horizon":
            raise WeatherHorizonUnavailable()
        end = date_to or date_from
        days = tuple(WeatherDay(date_from + timedelta(days=i), 5, 12, 20, None, "облачно", None)
                     for i in range((end - date_from).days + 1))
        return WeatherForecast(location, days)


@pytest.mark.parametrize("mode,available", [("ok", True), ("fail", False), ("horizon", False)])
async def test_weather_is_optional_isolated_and_uses_reliable_interval(mode, available):
    result = ask(snapshot(), query_type="trip_briefing", destination="Питер")
    provider = Weather(mode)
    weather = await render_weather_enrichment(result.briefing, provider)
    assert bool(weather) is available
    assert provider.calls == [("Санкт-Петербург", date(2026, 9, 12), date(2026, 9, 15))]
    assert "📍 Санкт-Петербург" in result.text  # core is independent of enrichment


def test_read_only_domains_and_shared_afisha_visibility():
    data = snapshot(plans=[event("shared", "Общий план", "2026-09-13")])
    before = {key: deepcopy(data.get(key)) for key in ("afisha", "calendars", "event_attachments")}
    result = ask(data, actor="sasha", query_type="trip_briefing", destination="Питер", person="vova")
    assert result.outcome == "found" and "Общий план" in result.text
    assert all(data.get(key) == value for key, value in before.items())


def _payload(query_type):
    return {"intent": "query_context", "arguments": {"query_type": query_type, "destination": None,
        "transport_type": None, "target": None, "date_expression": None, "person": None,
        "semantic_type": None, "follow_up": False}}


def test_decoder_accepts_trip_briefing_and_rejects_unknown_type():
    assert decode_intent(_payload("trip_briefing")).arguments["query_type"] == "trip_briefing"
    with pytest.raises(IntentParserInvalidOutput):
        decode_intent(_payload("travel_guide"))


@pytest.mark.parametrize(("phrase", "destination", "follow_up"), [
    ("Собери всё по поездке в Питер", "Питер", False),
    ("Покажи сводку по поездке в Питер", "Питер", False),
    ("Что нужно знать про поездку в Питер?", "Питер", False),
    ("Что у нас по поездке в Питер?", "Питер", False),
    ("Покажи информацию по поездке", None, False),
    ("Покажи сводку", None, True),
    ("Собери всё по ней", None, True),
    ("Что нужно знать?", None, True),
])
def test_parser_briefing_examples_at_strict_provider_boundary(phrase, destination, follow_up):
    envelope = {"intent": "query_context", "arguments": [
        {"name": "query_type", "value": "trip_briefing"},
        *([{"name": "destination", "value": destination}] if destination else []),
        *([{"name": "follow_up", "value": "true"}] if follow_up else []),
    ]}

    def transport(_request):
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(envelope)}}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    parsed = asyncio.run(PolzaIntentParser(api_key="secret", model="model", client=client).parse(
        phrase, IntentContext("actor", NOW, "Europe/Moscow")))
    asyncio.run(client.aclose())
    assert parsed.arguments["query_type"] == "trip_briefing"
    assert parsed.arguments["destination"] == destination and parsed.arguments["follow_up"] is follow_up


def test_prompt_keeps_city_only_phrase_conservative():
    assert "Не классифицируй расплывчатое «что у нас по Питеру?»" in SYSTEM_PROMPT


async def test_weather_outside_provider_horizon_is_not_requested():
    result = ask(snapshot(), query_type="trip_briefing", destination="Питер")
    provider = Weather()
    provider.horizon_days = 3
    assert await render_weather_enrichment(result.briefing, provider, today=date(2026, 9, 11)) is None
    assert provider.calls == []
