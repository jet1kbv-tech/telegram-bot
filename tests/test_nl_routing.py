from telegram.ext import ConversationHandler

from bot.app import build_app
from bot.states import (ADDING_EVENT_ATTACHMENT_FILE, ADDING_PURCHASE_PRICE, ADDING_TICKET_ATTACHMENTS,
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
