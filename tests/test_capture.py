import asyncio
from datetime import timedelta
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.handlers import capture, common, nl_assistant
from bot.services.capture import (CaptureContext, CaptureValidationError, NotesCaptureClassifier,
                                  normalize_text_capture, validate_capture_classification)
from bot.services.capture_proposals import ACTIVE_KEY, PROPOSALS_KEY
from bot.services.nl_dates import DateExpressionError, zoned_now
from bot.services.nl_intent import IntentKind, ParsedIntent
from bot.services.notes_service import NotesService
from bot.states import MENU, SECTION
from bot.storage import JsonStorage


class FakeIntentParser:
    def __init__(self, result):
        self.result = result

    async def parse(self, text, context):
        return self.result


class InvalidCaptureClassifier:
    async def classify(self, source, context):
        return {"contract_version": 1, "destination": "notes", "action": "create",
                "confidence": float("nan"), "reason_code": "personal_thought",
                "candidate": {"text": source.text}}


def run(value):
    return asyncio.run(value)


def make_update(*, username="wp_bvv", text="Надо посмотреть варианты поездки в Выборг весной",
                callback_data=None):
    waiting = SimpleNamespace(edit_text=AsyncMock(), delete=AsyncMock())
    message = SimpleNamespace(text=text, caption=None, reply_text=AsyncMock(return_value=waiting),
                              waiting=waiting, photo=[], document=None)
    query = None
    if callback_data is not None:
        query = SimpleNamespace(data=callback_data, answer=AsyncMock(), edit_message_text=AsyncMock(),
                                message=message)
    return SimpleNamespace(effective_message=message, message=message, callback_query=query,
                           effective_chat=SimpleNamespace(id=1),
                           effective_user=SimpleNamespace(username=username))


def make_context(*, section=False):
    user_data = {"active_section": "notes"} if section else {}
    return SimpleNamespace(user_data=user_data,
                           bot=SimpleNamespace(send_chat_action=AsyncMock()))


@pytest.fixture
def configured_capture(monkeypatch, tmp_path):
    store = JsonStorage(tmp_path / "data.json")
    service = NotesService(store)
    capture.configure_capture(classifier=NotesCaptureClassifier(), notes_service=service)
    profiles = {"wp_bvv": {"wishlist_owner": "vova"},
                "privetnormalno": {"wishlist_owner": "sasha"}}
    monkeypatch.setattr(capture, "ensure_access", AsyncMock(return_value=True))
    monkeypatch.setattr(capture, "get_username", lambda update: update.effective_user.username or "")
    monkeypatch.setattr(capture, "get_allowed_profile",
                        lambda update: profiles.get(update.effective_user.username))
    monkeypatch.setattr(nl_assistant, "ensure_access", AsyncMock(return_value=True))
    monkeypatch.setattr(nl_assistant, "get_username", lambda update: update.effective_user.username or "")
    monkeypatch.setattr(nl_assistant, "get_user_name",
                        lambda update: "Саша" if update.effective_user.username == "privetnormalno" else "Вова")
    monkeypatch.setattr(nl_assistant, "get_allowed_profile",
                        lambda update: profiles.get(update.effective_user.username))
    monkeypatch.setattr(nl_assistant.storage, "load", store.load)
    return store, service


@pytest.mark.parametrize(("section", "expected"), [(False, MENU), (True, SECTION)])
def test_no_action_creates_notes_proposal_without_mutation(configured_capture, section, expected):
    store, service = configured_capture
    nl_assistant._parser = FakeIntentParser(ParsedIntent(IntentKind.NO_ACTION, {}))
    update, context = make_update(), make_context(section=section)

    assert run(nl_assistant.nl_text_handler(update, context)) == expected

    assert service.list_notes(owner="vova") == []
    proposal_id = context.user_data[ACTIVE_KEY]
    proposal = context.user_data[PROPOSALS_KEY][proposal_id]
    preview = update.message.waiting.edit_text.await_args.args[0]
    assert proposal.actor_key == "wp_bvv" and proposal.owner_key == "vova"
    assert proposal.classification.candidate_text in preview
    assert "Пока ничего не сохранено" in preview
    assert {button.callback_data.split(":")[1]
            for row in update.message.waiting.edit_text.await_args.kwargs["reply_markup"].inline_keyboard
            for button in row} == {"confirm", "choose", "cancel"}
    assert store.load()["notes"] == []
    assert not store.path.exists()


@pytest.mark.parametrize(("section", "expected"), [(False, MENU), (True, SECTION)])
def test_confirm_uses_notes_service_owner_source_and_is_single_use(
        configured_capture, section, expected):
    store, service = configured_capture
    spy = Mock(wraps=service.create_note)
    capture._notes_service = SimpleNamespace(create_note=spy)
    nl_assistant._parser = FakeIntentParser(ParsedIntent(IntentKind.NO_ACTION, {}))
    context = make_context(section=section)
    run(nl_assistant.nl_text_handler(make_update(), context))
    proposal_id = context.user_data[ACTIVE_KEY]

    callback = make_update(callback_data=f"cap:confirm:{proposal_id}")
    assert run(capture.capture_callback_router(callback, context)) == expected
    assert run(capture.capture_callback_router(
        make_update(callback_data=f"cap:confirm:{proposal_id}"), context)) == expected

    spy.assert_called_once_with(owner="vova",
                                text="Надо посмотреть варианты поездки в Выборг весной",
                                source="universal_capture")
    assert store.load()["notes"] == [{
        **store.load()["notes"][0], "owner": "vova", "source": "universal_capture",
        "text": "Надо посмотреть варианты поездки в Выборг весной"}]


def test_other_actor_cannot_confirm_proposal(configured_capture):
    store, service = configured_capture
    nl_assistant._parser = FakeIntentParser(ParsedIntent(IntentKind.NO_ACTION, {}))
    context = make_context()
    run(nl_assistant.nl_text_handler(make_update(), context))
    proposal_id = context.user_data[ACTIVE_KEY]

    run(capture.capture_callback_router(make_update(
        username="privetnormalno", callback_data=f"cap:confirm:{proposal_id}"), context))

    assert service.list_notes(owner="vova") == []
    assert service.list_notes(owner="sasha") == []


def test_expired_superseded_cancelled_and_malformed_proposals_do_not_mutate(configured_capture):
    store, service = configured_capture
    nl_assistant._parser = FakeIntentParser(ParsedIntent(IntentKind.NO_ACTION, {}))
    context = make_context()
    run(nl_assistant.nl_text_handler(make_update(text="Первая мысль"), context))
    first = context.user_data[ACTIVE_KEY]
    run(nl_assistant.nl_text_handler(make_update(text="Вторая мысль"), context))
    second = context.user_data[ACTIVE_KEY]
    run(capture.capture_callback_router(make_update(callback_data=f"cap:confirm:{first}"), context))
    assert service.list_notes(owner="vova") == []

    proposal = context.user_data[PROPOSALS_KEY][second]
    proposal.expires_at = zoned_now("Europe/Moscow") - timedelta(seconds=1)
    run(capture.capture_callback_router(make_update(callback_data=f"cap:confirm:{second}"), context))
    assert service.list_notes(owner="vova") == []

    run(nl_assistant.nl_text_handler(make_update(text="Отменённая мысль"), context))
    third = context.user_data[ACTIVE_KEY]
    run(capture.capture_callback_router(make_update(callback_data=f"cap:cancel:{third}"), context))
    run(capture.capture_callback_router(make_update(callback_data="cap:broken"), context))
    assert store.load()["notes"] == []


def test_unsupported_and_existing_intents_do_not_create_capture_proposals(configured_capture, monkeypatch):
    for result in (
        ParsedIntent(IntentKind.UNSUPPORTED, {"reason": "other_user_calendar"}),
        ParsedIntent(IntentKind.ADD_PURCHASE, {"title": "чай", "price": None, "priority": None,
                                               "link": None, "comment": None, "buyer": None}),
        ParsedIntent(IntentKind.QUERY_FILMS, {"operation": "count", "status": "want",
                                             "media_type": None, "genre": None}),
    ):
        nl_assistant._parser = FakeIntentParser(result)
        context = make_context()
        if result.intent is IntentKind.QUERY_FILMS:
            monkeypatch.setattr(nl_assistant, "query_films", lambda *args, **kwargs:
                                SimpleNamespace(total=0, items=[], amount=0, missing_prices=0))
        run(nl_assistant.nl_text_handler(make_update(text="Команда"), context))
        assert ACTIVE_KEY not in context.user_data


def test_invalid_capture_classification_fails_closed_with_existing_no_action_help(configured_capture):
    store, service = configured_capture
    capture.configure_capture(classifier=InvalidCaptureClassifier(), notes_service=service)
    nl_assistant._parser = FakeIntentParser(ParsedIntent(IntentKind.NO_ACTION, {}))
    update, context = make_update(), make_context()

    run(nl_assistant.nl_text_handler(update, context))

    assert ACTIVE_KEY not in context.user_data
    assert "лучше всего умею" in update.message.waiting.edit_text.await_args.args[0]
    assert store.load()["notes"] == []


def context_query_intent():
    return ParsedIntent(IntentKind.QUERY_CONTEXT, {
        "query_type": "events", "target": None, "date_expression": "весной",
        "person": None, "follow_up": False, "semantic_type": None,
    })


def test_unsupported_context_date_creates_capture_proposal_then_one_personal_note(
        configured_capture, monkeypatch):
    store, service = configured_capture
    nl_assistant._parser = FakeIntentParser(context_query_intent())
    monkeypatch.setattr(nl_assistant.storage, "update", lambda callback: callback(store.load()))
    monkeypatch.setattr(nl_assistant, "execute_context_query",
                        Mock(side_effect=DateExpressionError("unsupported_date")))
    update, context = make_update(), make_context()

    assert run(nl_assistant.nl_text_handler(update, context)) == MENU
    assert service.list_notes(owner="vova") == []
    proposal_id = context.user_data[ACTIVE_KEY]
    proposal = context.user_data[PROPOSALS_KEY][proposal_id]
    assert proposal.classification.candidate_text == update.message.text

    callback = make_update(callback_data=f"cap:confirm:{proposal_id}")
    assert run(capture.capture_callback_router(callback, context)) == MENU
    notes = service.list_notes(owner="vova")
    assert len(notes) == 1
    assert notes[0]["text"] == update.message.text
    assert notes[0]["source"] == "universal_capture"


def test_mutating_semantic_failure_does_not_fallback_to_capture(configured_capture, monkeypatch):
    nl_assistant._parser = FakeIntentParser(ParsedIntent(IntentKind.ADD_PURCHASE, {
        "title": "чай", "price": None, "priority": None, "link": None,
        "comment": None, "buyer": None,
    }))
    monkeypatch.setattr(nl_assistant, "_prepare",
                        Mock(side_effect=DateExpressionError("unsupported_date")))
    update, context = make_update(text="Добавь чай"), make_context()

    assert run(nl_assistant.nl_text_handler(update, context)) == MENU
    assert ACTIVE_KEY not in context.user_data
    assert "Не получилось надёжно" in update.message.waiting.edit_text.await_args.args[0]


@pytest.mark.parametrize("failure", [RuntimeError("bug"), OSError("storage unavailable")])
def test_context_query_unexpected_or_storage_failure_does_not_fallback(
        configured_capture, monkeypatch, failure):
    nl_assistant._parser = FakeIntentParser(context_query_intent())
    monkeypatch.setattr(nl_assistant.storage, "update", Mock(side_effect=failure))
    context = make_context()

    with pytest.raises(type(failure), match=str(failure)):
        run(nl_assistant.nl_text_handler(make_update(), context))
    assert ACTIVE_KEY not in context.user_data


def test_invalid_capture_classification_after_safe_fallback_keeps_nl_error(
        configured_capture, monkeypatch):
    store, service = configured_capture
    capture.configure_capture(classifier=InvalidCaptureClassifier(), notes_service=service)
    nl_assistant._parser = FakeIntentParser(context_query_intent())
    monkeypatch.setattr(nl_assistant.storage, "update", lambda callback: callback(store.load()))
    monkeypatch.setattr(nl_assistant, "execute_context_query",
                        Mock(side_effect=DateExpressionError("unsupported_date")))
    update, context = make_update(), make_context()

    assert run(nl_assistant.nl_text_handler(update, context)) == MENU
    assert ACTIVE_KEY not in context.user_data
    assert "Не получилось надёжно" in update.message.waiting.edit_text.await_args.args[0]
    assert store.load()["notes"] == []


@pytest.mark.parametrize("reason", ["invalid_date", "invalid_date_range", "unknown_timezone",
                                     "unsupported_time"])
def test_other_date_error_reasons_are_not_capture_eligible(reason):
    assert not nl_assistant._is_capture_fallback_eligible(
        IntentKind.QUERY_CONTEXT, DateExpressionError(reason))


def test_unauthorized_user_cannot_create_or_confirm(configured_capture, monkeypatch):
    store, service = configured_capture
    nl_assistant._parser = FakeIntentParser(ParsedIntent(IntentKind.NO_ACTION, {}))
    monkeypatch.setattr(nl_assistant, "ensure_access", AsyncMock(return_value=False))
    context = make_context()
    assert run(nl_assistant.nl_text_handler(make_update(username="intruder"), context)) == MENU
    assert ACTIVE_KEY not in context.user_data

    monkeypatch.setattr(capture, "ensure_access", AsyncMock(return_value=False))
    run(capture.capture_callback_router(make_update(
        username="intruder", callback_data="cap:confirm:not-a-token"), context))
    assert service.list_notes(owner="vova") == []
    assert store.load()["notes"] == []


@pytest.mark.parametrize("command", ["start", "cancel"])
def test_start_and_cancel_discard_pending_capture_without_note(
        configured_capture, monkeypatch, command):
    store, service = configured_capture
    nl_assistant._parser = FakeIntentParser(ParsedIntent(IntentKind.NO_ACTION, {}))
    context = make_context()
    run(nl_assistant.nl_text_handler(make_update(), context))
    assert ACTIVE_KEY in context.user_data

    monkeypatch.setattr(common, "ensure_access", AsyncMock(return_value=True))
    monkeypatch.setattr(common, "remember_current_chat", AsyncMock())
    monkeypatch.setattr(common, "get_user_name", lambda update: "Вова")
    common.configure_common_handlers(main_menu_keyboard=lambda: "main-menu",
                                     safe_edit_message=AsyncMock())
    update = make_update(text=f"/{command}")

    assert run(getattr(common, command)(update, context)) == MENU

    assert ACTIVE_KEY not in context.user_data
    assert PROPOSALS_KEY not in context.user_data
    assert service.list_notes(owner="vova") == []
    assert store.load()["notes"] == []
    update.message.reply_text.assert_awaited_once()


@pytest.mark.parametrize("change", [
    {"destination": "films"}, {"action": "delete"}, {"contract_version": 2},
    {"confidence": float("inf")}, {"confidence": "1"}, {"reason_code": "free form"},
    {"candidate": {"text": ""}}, {"candidate": {"text": "thought", "owner": "sasha"}},
])
def test_strict_capture_contract_rejects_invalid_output(change):
    source = normalize_text_capture("thought")
    raw = {"contract_version": 1, "destination": "notes", "action": "create",
           "confidence": 0.8, "reason_code": "personal_thought",
           "candidate": {"text": "thought"}}
    raw.update(change)
    with pytest.raises(CaptureValidationError):
        validate_capture_classification(raw, source=source)


def test_capture_foundation_modules_have_no_storage_dependency():
    import inspect
    import bot.services.capture as capture_models
    import bot.services.capture_proposals as capture_proposals

    assert "bot.storage" not in inspect.getsource(capture_models)
    assert "bot.storage" not in inspect.getsource(capture_proposals)
