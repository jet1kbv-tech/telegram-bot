from types import SimpleNamespace
from unittest.mock import AsyncMock

from telegram.ext import ConversationHandler

from bot.app import build_app
from bot.handlers import text_commands
from bot.states import (ADDING_EVENT_ATTACHMENT_FILE, ADDING_PURCHASE_PRICE, ADDING_TICKET_ATTACHMENTS,
                        ADDING_EVENT_DATE, ADDING_EVENT_END_DATE, ADDING_EVENT_END_TIME, ADDING_EVENT_LINK,
                        ADDING_EVENT_PLACE, ADDING_EVENT_TIME, ADDING_EVENT_TITLE,
                        CONFIRMING_NL_ATTACHMENT, MENU, SECTION, SELECTING_NL_ATTACHMENT_EVENT,
                        WAITING_FOR_NL_ATTACHMENTS)


def conversation(app):
    return next(handler for handlers in app.handlers.values() for handler in handlers if isinstance(handler, ConversationHandler))


def callback_names(handlers):
    return [handler.callback.__name__ for handler in handlers]


def test_missing_polza_configuration_registers_no_idle_nl_handler(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "123:abc")
    monkeypatch.delenv("POLZA_AI_API_KEY", raising=False)
    monkeypatch.delenv("POLZA_AI_MODEL", raising=False)
    conv = conversation(build_app())
    assert "nl_text_handler" not in callback_names(conv.states[MENU])
    assert "nl_text_handler" not in callback_names(conv.states[SECTION])


def test_nl_is_last_idle_text_handler_and_never_in_active_form(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "123:abc")
    monkeypatch.setenv("POLZA_AI_API_KEY", "secret")
    monkeypatch.setenv("POLZA_AI_MODEL", "deepseek/deepseek-v4-flash-0731")
    conv = conversation(build_app())
    menu = callback_names(conv.states[MENU])
    section = callback_names(conv.states[SECTION])
    assert menu[0] == section[0] == "quick_text_command_router"
    assert menu[-1] == section[-1] == "nl_text_handler"
    assert "nl_callback_router" in menu and "nl_callback_router" in section
    assert "nl_text_handler" not in callback_names(conv.states[ADDING_PURCHASE_PRICE])
    assert callback_names(conv.states[ADDING_PURCHASE_PRICE])[-1] == "add_purchase_price"


def test_afisha_free_text_states_do_not_register_quick_commands(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "123:abc")
    conv = conversation(build_app())
    expected = {
        ADDING_EVENT_TITLE: "add_event_title",
        ADDING_EVENT_PLACE: "add_event_place",
        ADDING_EVENT_DATE: "add_event_date",
        ADDING_EVENT_TIME: "add_event_time",
        ADDING_EVENT_END_DATE: "add_event_end_date",
        ADDING_EVENT_END_TIME: "add_event_end_time",
        ADDING_EVENT_LINK: "add_event_link",
    }
    for state, state_callback in expected.items():
        assert callback_names(conv.states[state]) == ["quick_return_to_main_menu", state_callback]

    assert callback_names(conv.states[MENU])[0] == "quick_text_command_router"
    assert callback_names(conv.states[SECTION])[0] == "quick_text_command_router"


async def test_moscow_quick_command_still_routes_to_places_outside_afisha():
    menu = AsyncMock()
    section = AsyncMock()
    places = AsyncMock(return_value=SECTION)
    text_commands.configure_text_commands(
        menu_router=menu, section_router=section, places_callback_router=places,
    )
    message = SimpleNamespace(text="Москва", reply_text=AsyncMock())
    update = SimpleNamespace(effective_message=message)
    context = SimpleNamespace(user_data={"active_section": "afisha"})

    assert await text_commands.quick_text_command_router(update, context) == SECTION
    places.assert_awaited_once()
    assert places.await_args.args[0].callback_query.data == "places:moscow"
    assert context.user_data == {}
    menu.assert_not_awaited()
    section.assert_not_awaited()


def test_document_photo_routes_are_isolated_by_conversation_state(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "123:abc")
    monkeypatch.setenv("POLZA_AI_API_KEY", "secret")
    monkeypatch.setenv("POLZA_AI_MODEL", "configured/model")
    conv = conversation(build_app())
    assert callback_names(conv.states[MENU])[-3:] == [
        "nl_text_handler", "orphan_attachment_handler", "nl_text_handler",
    ]
    assert callback_names(conv.states[SECTION])[-3:] == [
        "nl_text_handler", "orphan_attachment_handler", "nl_text_handler",
    ]
    assert callback_names(conv.states[ADDING_EVENT_ATTACHMENT_FILE]) == [
        "event_attachment_router", "receive_file", "receive_file",
    ]
    assert callback_names(conv.states[ADDING_TICKET_ATTACHMENTS])[-2:] == [
        "add_ticket_attachment", "add_ticket_attachment",
    ]
    assert callback_names(conv.states[WAITING_FOR_NL_ATTACHMENTS])[-2:] == [
        "collect_attachment_handler", "collect_attachment_handler",
    ]
    assert callback_names(conv.states[SELECTING_NL_ATTACHMENT_EVENT]) == [
        "back_to_main", "nl_attachment_callback_router",
    ]
    assert callback_names(conv.states[CONFIRMING_NL_ATTACHMENT]) == [
        "back_to_main", "nl_attachment_callback_router",
    ]
    assert "orphan_attachment_handler" not in callback_names(conv.states[ADDING_PURCHASE_PRICE])
