import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from bot.handlers import nl_assistant
from bot.services.nl_intent import IntentKind, ParsedIntent
from bot.states import ADDING_CALENDAR_EVENT_TITLE, ADDING_EVENT_TITLE, ADDING_PURCHASE_TITLE, AI_CLARIFYING, MENU, SECTION


class FakeParser:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def parse(self, text, context):
        self.calls.append((text, context))
        return self.result


def run(coro):
    return asyncio.run(coro)


def update(*, text="command", callback_data=None):
    message = SimpleNamespace(text=text, reply_text=AsyncMock())
    query = None
    if callback_data:
        query = SimpleNamespace(data=callback_data, answer=AsyncMock(), edit_message_text=AsyncMock(), message=message)
    return SimpleNamespace(
        effective_message=message, message=message, callback_query=query,
        effective_chat=SimpleNamespace(id=1), effective_user=SimpleNamespace(username="wp_bvv"),
    )


def context():
    return SimpleNamespace(user_data={}, bot=SimpleNamespace(send_chat_action=AsyncMock()))


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setattr(nl_assistant, "ensure_access", AsyncMock(return_value=True))
    monkeypatch.setattr(nl_assistant, "get_username", lambda update: "wp_bvv")
    monkeypatch.setattr(nl_assistant, "get_user_name", lambda update: "Вова")
    monkeypatch.setattr(nl_assistant, "get_allowed_profile", lambda update: {"wishlist_owner": "vova"})
    monkeypatch.setattr(nl_assistant, "_notify_calendar", AsyncMock())


def purchase_intent():
    return ParsedIntent(IntentKind.ADD_PURCHASE, {
        "title": "кофемашина", "price": 35000, "priority": "high", "link": None,
        "comment": None, "buyer": "current_user",
    })


def test_purchase_has_preview_and_no_mutation_before_confirmation(monkeypatch):
    parser = FakeParser(purchase_intent())
    nl_assistant._parser = parser
    create = Mock(return_value={})
    monkeypatch.setattr(nl_assistant, "create_purchase", create)
    upd, ctx = update(), context()
    assert run(nl_assistant.nl_text_handler(upd, ctx)) == MENU
    create.assert_not_called()
    preview = upd.effective_message.reply_text.await_args.args[0]
    assert preview.startswith("🛍 Новая покупка")
    assert "Название: Кофемашина" in preview and "Стоимость: 35 000 ₽" in preview
    assert "Приоритет: Высокий" in preview and "Куплю я" in preview
    proposal_id = ctx.user_data["ai_active_proposal_id"]
    cb = update(callback_data=f"ai:c:{proposal_id}")
    assert run(nl_assistant.nl_callback_router(cb, ctx)) == SECTION
    create.assert_called_once()


def test_cancel_and_double_confirm_are_idempotent(monkeypatch):
    nl_assistant._parser = FakeParser(purchase_intent())
    create = Mock(return_value={})
    monkeypatch.setattr(nl_assistant, "create_purchase", create)
    ctx = context()
    run(nl_assistant.nl_text_handler(update(), ctx))
    proposal_id = ctx.user_data["ai_active_proposal_id"]
    assert run(nl_assistant.nl_callback_router(update(callback_data=f"ai:x:{proposal_id}"), ctx)) == MENU
    create.assert_not_called()
    run(nl_assistant.nl_text_handler(update(), ctx))
    proposal_id = ctx.user_data["ai_active_proposal_id"]
    run(nl_assistant.nl_callback_router(update(callback_data=f"ai:c:{proposal_id}"), ctx))
    run(nl_assistant.nl_callback_router(update(callback_data=f"ai:c:{proposal_id}"), ctx))
    create.assert_called_once()


def test_calendar_clarification_continues_same_proposal(monkeypatch):
    nl_assistant._parser = FakeParser(ParsedIntent(IntentKind.ADD_PERSONAL_CALENDAR_EVENT, {
        "title": "Стоматолог", "date_expression": None, "time_expression": "18:30",
        "end_time_expression": None, "comment": None, "owner": "current_user",
    }))
    create = Mock(return_value={"title": "Стоматолог", "owner": "vova", "date": "2026-08-10", "start_time": "18:30", "end_time": "", "comment": ""})
    monkeypatch.setattr(nl_assistant, "create_personal_calendar_event", create)
    ctx, first = context(), update()
    assert run(nl_assistant.nl_text_handler(first, ctx)) == AI_CLARIFYING
    proposal_id = ctx.user_data["ai_active_proposal_id"]
    assert first.effective_message.reply_text.await_args.args[0] == "На какую дату?"
    answer = update(text="завтра")
    assert run(nl_assistant.nl_clarification_handler(answer, ctx)) == MENU
    assert ctx.user_data["ai_active_proposal_id"] == proposal_id
    assert "2026-" in answer.effective_message.reply_text.await_args.args[0]
    run(nl_assistant.nl_callback_router(update(callback_data=f"ai:c:{proposal_id}"), ctx))
    create.assert_called_once()


def test_afisha_confirmation_uses_domain_action(monkeypatch):
    nl_assistant._parser = FakeParser(ParsedIntent(IntentKind.ADD_AFISHA_EVENT, {
        "title": "Щелкунчик", "place": "Большой театр", "date_expression": "20 декабря",
        "time_expression": "19:00", "end_date_expression": None, "end_time_expression": None, "link": None,
    }))
    item = {"title": "Щелкунчик", "place": "Большой театр", "date": "2026-12-20", "time": "19:00", "status": "active", "end_date": "", "end_time": "", "link": ""}
    create = Mock(return_value=item)
    monkeypatch.setattr(nl_assistant, "create_afisha_event", create)
    ctx = context()
    run(nl_assistant.nl_text_handler(update(), ctx))
    create.assert_not_called()
    proposal_id = ctx.user_data["ai_active_proposal_id"]
    run(nl_assistant.nl_callback_router(update(callback_data=f"ai:c:{proposal_id}"), ctx))
    create.assert_called_once()


def test_movie_confirmation_hands_query_to_existing_flow(monkeypatch):
    nl_assistant._parser = FakeParser(ParsedIntent(IntentKind.ADD_MOVIE_OR_TV, {"query": "Игры разума"}))
    begin = AsyncMock(return_value=SECTION)
    monkeypatch.setattr(nl_assistant, "begin_film_search", begin)
    ctx = context()
    run(nl_assistant.nl_text_handler(update(), ctx))
    proposal_id = ctx.user_data["ai_active_proposal_id"]
    assert run(nl_assistant.nl_callback_router(update(callback_data=f"ai:c:{proposal_id}"), ctx)) == SECTION
    assert begin.await_args.args[2] == "Игры разума"


@pytest.mark.parametrize("kind", [IntentKind.NO_ACTION, IntentKind.UNSUPPORTED])
def test_non_action_intents_never_mutate(monkeypatch, kind):
    arguments = {} if kind is IntentKind.NO_ACTION else {"category": "conversation"}
    nl_assistant._parser = FakeParser(ParsedIntent(kind, arguments))
    create = Mock()
    monkeypatch.setattr(nl_assistant, "create_purchase", create)
    assert run(nl_assistant.nl_text_handler(update(), context())) == MENU
    create.assert_not_called()


@pytest.mark.parametrize(("intent", "expected_state"), [
    (purchase_intent(), ADDING_PURCHASE_TITLE),
    (ParsedIntent(IntentKind.ADD_PERSONAL_CALENDAR_EVENT, {"title": "X", "date_expression": "завтра", "time_expression": "18:00", "end_time_expression": None, "comment": None, "owner": "current_user"}), ADDING_CALENDAR_EVENT_TITLE),
    (ParsedIntent(IntentKind.ADD_AFISHA_EVENT, {"title": "X", "place": None, "date_expression": "завтра", "time_expression": "18:00", "end_date_expression": None, "end_time_expression": None, "link": None}), ADDING_EVENT_TITLE),
])
def test_edit_hands_off_to_native_form(intent, expected_state):
    nl_assistant._parser = FakeParser(intent)
    ctx = context()
    run(nl_assistant.nl_text_handler(update(), ctx))
    proposal_id = ctx.user_data["ai_active_proposal_id"]
    state = run(nl_assistant.nl_callback_router(update(callback_data=f"ai:e:{proposal_id}"), ctx))
    assert state == expected_state
