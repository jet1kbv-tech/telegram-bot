from telegram.ext import ConversationHandler

from bot.app import build_app
from bot.states import ADDING_PURCHASE_PRICE, MENU, SECTION


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
