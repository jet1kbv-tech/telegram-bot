import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from bot.handlers import nl_assistant
from bot.services.nl_intent import IntentKind, IntentParserInvalidOutput, IntentParserTimeout, IntentParserUnavailable, ParsedIntent
from bot.states import ADDING_CALENDAR_EVENT_TITLE, ADDING_EVENT_TITLE, ADDING_PURCHASE_TITLE, AI_CLARIFYING, MENU, SECTION


class FakeParser:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def parse(self, text, context):
        self.calls.append((text, context))
        return self.result


class FailingParser:
    def __init__(self, error):
        self.error = error

    async def parse(self, text, context):
        raise self.error


def run(coro):
    return asyncio.run(coro)


def update(*, text="command", callback_data=None):
    waiting = SimpleNamespace(edit_text=AsyncMock(), delete=AsyncMock())
    message = SimpleNamespace(text=text, reply_text=AsyncMock(return_value=waiting), waiting=waiting)
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
    preview = upd.effective_message.waiting.edit_text.await_args.args[0]
    assert preview.startswith("➕ Добавить покупку")
    assert "Название: Кофемашина" in preview and "Стоимость: 35 000 ₽" in preview
    assert "Приоритет: Высокий" in preview and "Куплю я" in preview
    proposal_id = ctx.user_data["ai_active_proposal_id"]
    cb = update(callback_data=f"ai:c:{proposal_id}")
    assert run(nl_assistant.nl_callback_router(cb, ctx)) == SECTION
    create.assert_called_once()


@pytest.mark.parametrize(("error", "expected"), [
    (IntentParserTimeout("timeout"), "слишком много времени"),
    (IntentParserUnavailable("down"), "Сейчас не получается"),
    (IntentParserInvalidOutput("bad"), "надёжно разобрать"),
])
def test_controlled_parser_failure_replaces_waiting_message(error, expected):
    nl_assistant._parser = FailingParser(error)
    upd = update(text="private command")
    run(nl_assistant.nl_text_handler(upd, context()))
    assert upd.effective_message.reply_text.await_args_list[0].args[0] == "⏳ Разбираю команду…"
    assert expected in upd.effective_message.waiting.edit_text.await_args.args[0]


def test_no_action_removes_waiting_message():
    nl_assistant._parser = FakeParser(ParsedIntent(IntentKind.NO_ACTION, {}))
    upd = update(text="привет")
    run(nl_assistant.nl_text_handler(upd, context()))
    upd.effective_message.waiting.delete.assert_awaited_once()


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
    assert first.effective_message.waiting.edit_text.await_args.args[0] == "На какую дату поставить событие?"
    assert first.effective_message.waiting.edit_text.await_args.kwargs["reply_markup"].inline_keyboard[0][0].text == "❌ Отменить"
    answer = update(text="завтра")
    assert run(nl_assistant.nl_clarification_handler(answer, ctx)) == MENU
    assert ctx.user_data["ai_active_proposal_id"] == proposal_id
    assert "11.08.2026" in answer.effective_message.reply_text.await_args.args[0]
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


def test_proposal_copy_hides_empty_fields_and_humanizes_changes():
    now = nl_assistant.zoned_now(nl_assistant.BOT_TIMEZONE)
    purchase = nl_assistant.create_proposal({}, intent=IntentKind.ADD_PURCHASE, arguments={
        "title": "чайник", "price": None, "priority": "low", "buyer": "", "link": None, "comment": None,
    }, actor_key="vova", now=now, ttl_seconds=60)
    text = nl_assistant._preview(purchase)
    assert text.startswith("➕ Добавить покупку")
    assert "Стоимость" not in text and "None" not in text and "null" not in text
    assert "Приоритет: Низкий" in text and "Пока ничего не добавлено" in text

    update_proposal = SimpleNamespace(intent=IntentKind.UPDATE_PURCHASE, arguments={
        "_selected": {"title": "Чайник", "priority": "medium", "price": 1000},
        "_changes": {"priority": "high", "price": 2500},
    })
    changed = nl_assistant._preview(update_proposal)
    assert "Приоритет: средний → высокий" in changed
    assert "Стоимость: 1 000 ₽ → 2 500 ₽" in changed
    assert "Пока ничего не изменено" in changed


def test_delete_preview_names_target_and_is_explicitly_pending():
    proposal = SimpleNamespace(intent=IntentKind.DELETE_CALENDAR_EVENT, arguments={
        "_selected": {"title": "Стоматолог", "date": "2026-08-12", "start_time": "18:00"}, "_changes": {},
    })
    text = nl_assistant._preview(proposal)
    assert text.startswith("🗑 Удалить")
    assert "Стоматолог" in text and "12.08.2026 в 18:00" in text
    assert "Пока ничего не удалено" in text


def test_waiting_edit_failure_falls_back_to_single_reply():
    nl_assistant._parser = FakeParser(purchase_intent())
    upd = update()
    upd.effective_message.waiting.edit_text.side_effect = RuntimeError("telegram edit failed")
    run(nl_assistant.nl_text_handler(upd, context()))
    assert upd.effective_message.reply_text.await_count == 2
    assert upd.effective_message.reply_text.await_args_list[-1].args[0].startswith("➕ Добавить покупку")


def test_clarification_prompts_never_expose_internal_names():
    prompts = [nl_assistant._clarification_prompt(field) for field in ("date", "time", "title", "price", "target", "place")]
    assert all(field not in prompt for field, prompt in zip(("date", "time", "title", "price", "target", "place"), prompts))
    assert prompts == [
        "На какую дату поставить событие?", "Во сколько начнётся событие?", "Как назвать?",
        "Сколько стоит покупка?", "Какую запись нужно изменить?", "Где пройдёт событие?",
    ]


def test_multiple_entity_candidates_are_readable_and_keep_ids_hidden(monkeypatch):
    nl_assistant._parser = FakeParser(ParsedIntent(IntentKind.DELETE_CALENDAR_EVENT, {"target": "Стоматолог"}))
    candidates = [
        nl_assistant.EntityCandidate("secret-1", "vova", {"title": "Стоматолог", "date": "2026-08-12", "start_time": "18:00"}),
        nl_assistant.EntityCandidate("secret-2", "vova", {"title": "Стоматолог", "date": "2026-08-19", "start_time": "09:30"}),
    ]
    monkeypatch.setattr(nl_assistant, "resolve_entities", lambda *args, **kwargs: candidates)
    monkeypatch.setattr(nl_assistant.storage, "load", lambda: {})
    upd = update(); run(nl_assistant.nl_text_handler(upd, context()))
    text = upd.effective_message.waiting.edit_text.await_args.args[0]
    markup = upd.effective_message.waiting.edit_text.await_args.kwargs["reply_markup"]
    assert "несколько похожих записей" in text
    assert "12.08.2026, 18:00" in text and "19.08.2026, 09:30" in text
    assert "secret-" not in text
    assert markup.inline_keyboard[0][0].text.startswith("1. Стоматолог")
    assert markup.inline_keyboard[-1][0].text == "❌ Отменить"


def test_no_candidate_has_clear_manual_recovery(monkeypatch):
    nl_assistant._parser = FakeParser(ParsedIntent(IntentKind.DELETE_PURCHASE, {"target": "Телескоп"}))
    monkeypatch.setattr(nl_assistant, "resolve_entities", lambda *args, **kwargs: [])
    monkeypatch.setattr(nl_assistant.storage, "load", lambda: {})
    upd = update(); run(nl_assistant.nl_text_handler(upd, context()))
    assert upd.effective_message.waiting.edit_text.await_args.args[0] == "Не нашёл такую запись. Проверь название или открой нужный раздел вручную."


def test_phase6_list_and_empty_state_replace_waiting(monkeypatch):
    args = {"status": "planned", "priority": "any", "buyer": "any", "operation": "list"}
    nl_assistant._parser = FakeParser(ParsedIntent(IntentKind.QUERY_PURCHASES, args))
    monkeypatch.setattr(nl_assistant.storage, "load", lambda: {})
    monkeypatch.setattr(nl_assistant, "query_purchases", lambda *a, **kw: SimpleNamespace(
        items=[{"title": "Кофе", "price": 1200}], total=1, amount=1200, missing_prices=0))
    upd = update(); run(nl_assistant.nl_text_handler(upd, context()))
    text = upd.effective_message.waiting.edit_text.await_args.args[0]
    assert text.startswith("🛍 Покупки") and "Кофе — 1 200 ₽" in text and "Показано 1 из 1" in text

    monkeypatch.setattr(nl_assistant, "query_purchases", lambda *a, **kw: SimpleNamespace(items=[], total=0, amount=0, missing_prices=0))
    upd = update(); run(nl_assistant.nl_text_handler(upd, context()))
    assert upd.effective_message.waiting.edit_text.await_args.args[0] == "Подходящих покупок пока нет."
