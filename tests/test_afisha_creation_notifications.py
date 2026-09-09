from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.error import NetworkError

from bot.handlers import afisha
from bot.runtime import notify_other_user_about_afisha_item
from bot.services.actions import afisha as afisha_actions
from bot.storage import JsonStorage


def _update(username: str, text: str = "-") -> SimpleNamespace:
    message = SimpleNamespace(text=text, reply_text=AsyncMock())
    return SimpleNamespace(
        message=message,
        effective_user=SimpleNamespace(username=username),
        effective_chat=SimpleNamespace(id=100),
    )


def _context(item: dict | None = None, *, bot: object | None = None) -> SimpleNamespace:
    values = {
        "event_title": "Концерт",
        "event_place": "",
        "event_date": "2026-09-14",
        "event_time": "19:00",
        "event_end_date": "",
        "event_end_time": "",
    }
    if item:
        values.update(item)
    return SimpleNamespace(user_data=values, bot=bot or SimpleNamespace(send_message=AsyncMock()))


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch, tmp_path):
    value = JsonStorage(tmp_path / "data.json")
    data = value.load()
    data["meta"]["user_chats"] = {"wp_bvv": 101, "privetnormalno": 202}
    value.save(data)
    monkeypatch.setattr(afisha_actions, "storage", value)
    monkeypatch.setattr("bot.runtime.storage", value)
    monkeypatch.setattr(afisha, "ensure_access", AsyncMock(return_value=True))
    monkeypatch.setattr(afisha, "remember_current_chat", AsyncMock())
    monkeypatch.setattr(afisha, "_build_item_text", lambda section, item: item["title"])
    monkeypatch.setattr(afisha, "_item_keyboard", lambda *args, **kwargs: None)
    monkeypatch.setattr(afisha, "_notify_other_user_about_afisha_item", notify_other_user_about_afisha_item)
    return value


@pytest.mark.parametrize(("username", "recipient", "creator"), [
    ("wp_bvv", 202, "Вова"),
    ("privetnormalno", 101, "Саша"),
])
async def test_manual_creation_notifies_only_partner_once(store, username, recipient, creator):
    bot = SimpleNamespace(send_message=AsyncMock())
    await afisha.add_event_link(_update(username), _context(bot=bot))

    assert len(store.load()["afisha"]) == 1
    bot.send_message.assert_awaited_once_with(
        chat_id=recipient,
        text=f"🎟 {creator} добавил(а) в Афишу:\nКонцерт\n14.09.2026 19:00",
    )


async def test_notification_failure_preserves_single_successful_write(store):
    bot = SimpleNamespace(send_message=AsyncMock(side_effect=NetworkError("offline")))
    update = _update("wp_bvv")

    await afisha.add_event_link(update, _context(bot=bot))

    assert len(store.load()["afisha"]) == 1
    update.message.reply_text.assert_awaited_once()
    bot.send_message.assert_awaited_once()


async def test_unknown_actor_does_not_send_cross_user_notification(store):
    bot = SimpleNamespace(send_message=AsyncMock())
    await notify_other_user_about_afisha_item(
        SimpleNamespace(bot=bot),
        _update("intruder"),
        {"title": "Концерт", "date": "2026-09-14", "time": "19:00"},
    )
    bot.send_message.assert_not_awaited()


@pytest.mark.parametrize(("item", "expected"), [
    ({"title": "День", "date": "2026-09-14", "time": ""}, "14.09.2026"),
    ({"title": "Фестиваль", "date": "2026-09-14", "time": "19:00",
      "end_date": "2026-09-16", "end_time": "21:00"},
     "14.09.2026 19:00 – 16.09.2026 21:00"),
])
async def test_notification_reuses_afisha_date_range_format(store, item, expected):
    bot = SimpleNamespace(send_message=AsyncMock())
    await notify_other_user_about_afisha_item(SimpleNamespace(bot=bot), _update("wp_bvv"), item)
    assert bot.send_message.await_args.kwargs["text"].endswith(expected)


async def test_edit_delete_and_projection_do_not_emit_creation_notification(store):
    notify = AsyncMock()
    item = afisha_actions.create_afisha_event({"title": "Концерт", "date": "2026-09-14", "time": "19:00"})
    data = store.load()
    assert all(len(events) == 1 for events in data["calendars"].values())
    afisha.apply_afisha_status_update(data, item, "done")
    data = store.load()
    afisha.apply_afisha_delete(data, item)

    notify.assert_not_awaited()
