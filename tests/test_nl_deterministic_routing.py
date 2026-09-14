from bot.services.nl_deterministic_routing import named_event_question, short_context_follow_up


def test_production_event_and_trip_follow_up_forms_are_domain_bounded():
    assert short_context_follow_up("А где?", "event").arguments["query_type"] == "event_place"
    assert short_context_follow_up("А документы?", "event").arguments["query_type"] == "documents"
    assert short_context_follow_up("А обратно?", "trip").arguments["query_type"] == "return"
    assert short_context_follow_up("А билеты?", "trip").arguments["query_type"] == "documents"
    assert short_context_follow_up("А обратно?", "event") is None


def test_named_event_questions_never_supply_an_invented_date():
    cases = {
        "Когда ТЕСТ — Купить сувениры?": ("event_date", "ТЕСТ — Купить сувениры"),
        "Во сколько ТЕСТ — Купить сувениры?": ("event_time", "ТЕСТ — Купить сувениры"),
        "Где ТЕСТ — Эрмитаж?": ("event_place", "ТЕСТ — Эрмитаж"),
    }
    for text, expected in cases.items():
        parsed = named_event_question(text)
        assert (parsed.arguments["query_type"], parsed.arguments["target"]) == expected
        assert parsed.arguments["date_expression"] is None
