from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.handlers import notes as handlers
from bot.keyboards.notes import note_card_keyboard, notes_list_keyboard, notes_menu_keyboard
from bot.services.notes_service import MAX_NOTE_TEXT_LENGTH, NotesService
from bot.states import ADDING_NOTE_TEXT, EDITING_NOTE_TEXT, SECTION
from bot.storage import JsonStorage


def callbacks(markup):
    return [button.callback_data for row in markup.inline_keyboard for button in row]


@pytest.fixture
def notes_setup(tmp_path, monkeypatch):
    service = NotesService(JsonStorage(tmp_path / "data.json"))
    edit = AsyncMock()
    monkeypatch.setattr(handlers, "_service", service)
    monkeypatch.setattr(handlers, "_safe_edit", edit)
    monkeypatch.setattr(handlers, "ensure_access", AsyncMock(return_value=True))
    monkeypatch.setattr(handlers, "remember_current_chat", AsyncMock())
    return service, edit


def callback_update(data, username="wp_bvv"):
    return SimpleNamespace(callback_query=SimpleNamespace(data=data, answer=AsyncMock()),
                           effective_user=SimpleNamespace(username=username))


def message_update(text, username="wp_bvv"):
    return SimpleNamespace(message=SimpleNamespace(text=text, reply_text=AsyncMock()),
                           effective_user=SimpleNamespace(username=username))


def test_notes_keyboards_use_only_structural_callback_data():
    assert callbacks(notes_menu_keyboard()) == ["notes:add", "notes:list:0", "more:menu", "menu:main"]
    note = {"id": "abc123", "text": "private text", "owner": "vova"}
    values = callbacks(notes_list_keyboard([note], 0)) + callbacks(note_card_keyboard("abc123", 0))
    assert all("private text" not in value and "vova" not in value and len(value.encode()) <= 64 for value in values)


async def test_menu_add_and_one_message_create_flow(notes_setup):
    service, edit = notes_setup
    context = SimpleNamespace(user_data={})
    assert await handlers.notes_callback_router(callback_update("notes:menu"), context) == SECTION
    assert callbacks(edit.await_args.kwargs["reply_markup"]) == ["notes:add", "notes:list:0", "more:menu", "menu:main"]
    assert await handlers.notes_callback_router(callback_update("notes:add"), context) == ADDING_NOTE_TEXT
    message = message_update("  одна\nзаметка  ")
    assert await handlers.add_note_text(message, context) == SECTION
    assert [note["text"] for note in service.list_notes(owner="vova")] == ["одна\nзаметка"]


async def test_invalid_create_messages_do_not_save(notes_setup):
    service, _ = notes_setup
    context = SimpleNamespace(user_data={})
    assert await handlers.add_note_text(message_update("   "), context) == ADDING_NOTE_TEXT
    assert await handlers.add_note_text(message_update("x" * (MAX_NOTE_TEXT_LENGTH + 1)), context) == ADDING_NOTE_TEXT
    assert service.list_notes(owner="vova") == []


async def test_foreign_view_edit_and_delete_do_not_reveal_or_mutate(notes_setup):
    service, edit = notes_setup
    foreign = service.create_note(owner="sasha", text="Секрет Саши")
    context = SimpleNamespace(user_data={})
    for action in ("view", "edit", "delete_confirm", "delete"):
        result = await handlers.notes_callback_router(
            callback_update(f"notes:{action}:{foreign['id']}:0"), context)
        assert result == SECTION
        assert "Секрет Саши" not in edit.await_args.args[1]
    assert service.get_note(owner="sasha", note_id=foreign["id"]) is not None
    assert handlers.EDIT_SESSION not in context.user_data


async def test_owned_edit_and_confirmed_delete_flow(notes_setup):
    service, edit = notes_setup
    note = service.create_note(owner="vova", text="До")
    context = SimpleNamespace(user_data={})
    assert await handlers.notes_callback_router(
        callback_update(f"notes:edit:{note['id']}:0"), context) == EDITING_NOTE_TEXT
    assert await handlers.edit_note_text(message_update("После"), context) == SECTION
    assert service.get_note(owner="vova", note_id=note["id"])["text"] == "После"
    assert await handlers.notes_callback_router(
        callback_update(f"notes:delete_confirm:{note['id']}:0"), context) == SECTION
    assert service.get_note(owner="vova", note_id=note["id"]) is not None
    assert await handlers.notes_callback_router(
        callback_update(f"notes:delete:{note['id']}:99"), context) == SECTION
    assert service.get_note(owner="vova", note_id=note["id"]) is None
    assert "Пока пусто" in edit.await_args.args[1]


async def test_list_is_actor_scoped_and_paginated(notes_setup):
    service, edit = notes_setup
    for index in range(11):
        service.create_note(owner="vova", text=f"V {index}")
    service.create_note(owner="sasha", text="S private")
    await handlers.notes_callback_router(callback_update("notes:list:0"), SimpleNamespace(user_data={}))
    markup = edit.await_args.kwargs["reply_markup"]
    assert len([value for value in callbacks(markup) if value.startswith("notes:view:")]) == 10
    assert "notes:list:1" in callbacks(markup)
    assert all("S private" not in button.text for row in markup.inline_keyboard for button in row)
