import asyncio
from datetime import date
import json
import logging
import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from telegram.error import BadRequest

from bot.handlers import event_attachments as handlers
from bot.services.ticket_enrichment import (PolzaTicketEnricher, TicketEnrichmentInvalidOutput,
    TicketEnrichmentTimeout, TicketEnrichmentUnavailable, decode_ticket_enrichment)
from bot.storage import JsonStorage


def _response(content, status=200):
    return httpx.Response(status, json={"choices": [{"message": {"content": content}}]},
                          request=httpx.Request("POST", "https://polza.ai"))


@pytest.mark.parametrize("raw", [
    '{"origin":" Москва ","destination":"Воронеж","date":"2026-08-31","departure_time":"08:10","arrival_date":null,"arrival_time":null}',
    '{"origin":null,"destination":"Воронеж","date":null,"departure_time":null,"arrival_date":null,"arrival_time":null}',
])
def test_strict_decoder_valid_and_partial(raw):
    result = decode_ticket_enrichment(raw)
    assert result.destination == "Воронеж"
    assert result.origin in {"Москва", None}


def test_production_overnight_ticket_contract():
    result = decode_ticket_enrichment(json.dumps({
        "origin": "Москва Казанская", "destination": "Придача Воронеж Южный",
        "date": "2026-08-30", "departure_time": "23:38",
        "arrival_date": "2026-08-31", "arrival_time": "09:33",
    }))
    assert result.as_dict() == {"origin": "Москва Казанская", "destination": "Придача Воронеж Южный",
        "date": "2026-08-30", "departure_time": "23:38", "arrival_date": "2026-08-31", "arrival_time": "09:33"}


@pytest.mark.parametrize("raw", [
    '{"origin":null,"destination":null,"date":null,"departure_time":null}',
    '{"origin":null,"destination":null,"date":null,"departure_time":null,"arrival_date":"2026-02-30","arrival_time":null}',
    '{"origin":null,"destination":null,"date":null,"departure_time":null,"arrival_date":null,"arrival_time":"24:00"}',
])
def test_arrival_fields_are_required_and_strict(raw):
    with pytest.raises(TicketEnrichmentInvalidOutput): decode_ticket_enrichment(raw)


@pytest.mark.parametrize("raw", ["not json",
    '{"origin":null,"destination":null,"date":"31.08.2026","departure_time":null,"arrival_date":null,"arrival_time":null}',
    '{"origin":null,"destination":null,"date":null,"departure_time":"25:00","arrival_date":null,"arrival_time":null}',
    '{"origin":null,"destination":null,"date":null,"departure_time":null,"arrival_date":null,"arrival_time":null,"seat":"1"}'])
def test_strict_decoder_rejects_malformed_dates_times_and_extra_fields(raw):
    with pytest.raises(TicketEnrichmentInvalidOutput): decode_ticket_enrichment(raw)


def test_multimodal_image_and_pdf_payloads_use_private_base64_parts():
    enricher = PolzaTicketEnricher(api_key="key", model="vision")
    common = dict(local_date=date(2026, 8, 12), timezone="Europe/Moscow", event_date="2026-08-31")
    image = enricher.build_payload(b"image", "image", mime_type="image/jpeg", **common)
    pdf = enricher.build_payload(b"pdf", "pdf", mime_type="application/pdf", **common)
    image_part = image["messages"][0]["content"][1]
    pdf_part = pdf["messages"][0]["content"][1]
    assert image_part["type"] == "image_url" and image_part["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert pdf_part == {"type": "file", "file": {"filename": "ticket.pdf", "file_data": "data:application/pdf;base64,cGRm"}}
    assert image["response_format"]["json_schema"]["strict"] is True
    assert image["response_format"]["json_schema"]["schema"]["additionalProperties"] is False


def test_provider_success_timeout_http_failure_and_private_logs(caplog):
    valid = json.dumps({"origin": "SECRET_ROUTE", "destination": None, "date": "2026-08-31", "departure_time": None, "arrival_date": None, "arrival_time": None})
    client = SimpleNamespace(post=AsyncMock(return_value=_response(valid)))
    enricher = PolzaTicketEnricher(api_key="key", model="vision", client=client)
    with caplog.at_level(logging.INFO):
        result = asyncio.run(enricher.enrich(b"PRIVATE_BYTES", "image", mime_type="image/jpeg",
            local_date=date(2026, 8, 12), timezone="UTC"))
    assert result.origin == "SECRET_ROUTE"
    assert all(secret not in caplog.text for secret in ("PRIVATE_BYTES", "SECRET_ROUTE", "2026-08-31"))

    timeout_client = SimpleNamespace(post=AsyncMock(side_effect=httpx.ReadTimeout("late")))
    with pytest.raises(TicketEnrichmentTimeout):
        asyncio.run(PolzaTicketEnricher(api_key="key", model="vision", client=timeout_client).enrich(
            b"x", "image", mime_type="image/jpeg", local_date=date.today(), timezone="UTC"))
    failed_client = SimpleNamespace(post=AsyncMock(return_value=_response("{}", 503)))
    with pytest.raises(TicketEnrichmentUnavailable):
        asyncio.run(PolzaTicketEnricher(api_key="key", model="vision", client=failed_client).enrich(
            b"x", "pdf", mime_type="application/pdf", local_date=date.today(), timezone="UTC"))


@pytest.mark.parametrize(("media_type", "mime_type", "status"), [
    ("image", "image/jpeg", 400),  # image modality or response_format rejected
    ("pdf", "application/pdf", 404),  # unknown configured model or file modality rejected
    ("pdf", "application/pdf", 422),  # structured output rejected
])
def test_provider_capability_rejections_are_controlled_without_fallback(media_type, mime_type, status):
    client = SimpleNamespace(post=AsyncMock(return_value=_response("provider-private-error", status)))
    enricher = PolzaTicketEnricher(api_key="key", model="externally-configured", client=client)
    with pytest.raises(TicketEnrichmentUnavailable):
        asyncio.run(enricher.enrich(b"x", media_type, mime_type=mime_type,
                                   local_date=date.today(), timezone="UTC"))
    client.post.assert_awaited_once()
    assert client.post.await_args.kwargs["json"]["model"] == "externally-configured"


@pytest.mark.parametrize(("name", "value"), [
    ("AI_ATTACHMENT_MAX_BYTES", "0"), ("AI_ATTACHMENT_MAX_BYTES", "-1"),
    ("AI_ATTACHMENT_TIMEOUT_SECONDS", "0"), ("AI_ATTACHMENT_TIMEOUT_SECONDS", "nan"),
])
def test_invalid_attachment_limits_fail_closed_at_config_import(name, value):
    env = {**os.environ, name: value, "PYTHONPATH": "."}
    result = subprocess.run([sys.executable, "-c", "import bot.config"], env=env,
                            cwd=os.getcwd(), capture_output=True, text=True, check=False)
    assert result.returncode != 0
    assert name in result.stderr


def _data(mime="application/pdf"):
    return {"afisha": [], "calendars": {"vova": [{"id": "cal", "owner": "vova", "title": "Trip", "date": "2026-08-31",
        "start_time": "10:00", "end_time": "", "comment": "", "notified_24h": False,
        "source": "manual", "source_id": ""}]}, "event_attachments": [{"id": "att", "parent_type": "calendar",
        "parent_event_id": "cal", "telegram_file_id": "PRIVATE_FILE_ID", "telegram_file_unique_id": "PRIVATE_UNIQUE",
        "telegram_media_type": "document", "file_name": "PRIVATE_NAME.pdf", "mime_type": mime,
        "semantic_type": "transport_ticket", "transport_type": "train", "origin": "User origin",
        "destination": None, "date": None, "departure_time": None, "person": None}]}


def _update(callback):
    message = SimpleNamespace(chat_id=1, reply_text=AsyncMock())
    return SimpleNamespace(callback_query=SimpleNamespace(data=callback, answer=AsyncMock(), message=message),
        effective_user=SimpleNamespace(username="wp_bvv"), effective_chat=SimpleNamespace(id=1))


def test_explicit_consent_download_size_bound_and_existing_precedence(monkeypatch, tmp_path):
    initial = _data(); initial["event_attachments"][0]["arrival_date"] = "2026-09-01"
    store = JsonStorage(tmp_path / "data.json"); store.save(initial)
    monkeypatch.setattr(handlers, "storage", store)
    monkeypatch.setattr(handlers, "ensure_access", AsyncMock(return_value=True)); monkeypatch.setattr(handlers, "remember_current_chat", AsyncMock())
    edit = AsyncMock(); provider = SimpleNamespace(enrich=AsyncMock(return_value=decode_ticket_enrichment(
        '{"origin":"AI origin","destination":"Воронеж","date":"2026-08-31","departure_time":"08:10","arrival_date":"2026-08-31","arrival_time":"09:33"}')))
    tg_file = SimpleNamespace(file_size=3, download_as_bytearray=AsyncMock(return_value=bytearray(b"pdf")))
    ctx = SimpleNamespace(user_data={}, bot=SimpleNamespace(get_file=AsyncMock(return_value=tg_file)))
    handlers.configure_event_attachment_handlers(safe_edit_message=edit, ticket_enricher=provider, attachment_max_bytes=8)
    # Merely displaying the consent action performs no retrieval/provider call.
    asyncio.run(handlers.show_detail(_update("att|detail|att"), ctx, "att"))
    ctx.bot.get_file.assert_not_awaited(); provider.enrich.assert_not_awaited()
    state = asyncio.run(handlers.event_attachment_router(_update("att|recognize|att"), ctx))
    assert state == handlers.CONFIRMING_TICKET_ENRICHMENT
    assert ctx.user_data["ticket_enrichment_proposal"]["changes"]["destination"] == "Воронеж"
    assert "origin" not in ctx.user_data["ticket_enrichment_proposal"]["changes"]
    assert "arrival_date" not in ctx.user_data["ticket_enrichment_proposal"]["changes"]
    assert ctx.user_data["ticket_enrichment_proposal"]["changes"]["arrival_time"] == "09:33"
    assert store.load()["event_attachments"][0]["destination"] is None
    asyncio.run(handlers.event_attachment_router(_update("att|saveai"), ctx))
    saved = store.load()["event_attachments"][0]
    assert saved["id"] == "att" and saved["parent_event_id"] == "cal" and saved["telegram_file_id"] == "PRIVATE_FILE_ID"
    assert saved["origin"] == "User origin" and saved["destination"] == "Воронеж"
    assert saved["arrival_date"] == "2026-09-01" and saved["arrival_time"] == "09:33"

    large_file = SimpleNamespace(file_size=9, download_as_bytearray=AsyncMock())
    ctx.bot.get_file = AsyncMock(return_value=large_file); provider.enrich.reset_mock()
    asyncio.run(handlers.event_attachment_router(_update("att|recognize|att"), ctx))
    large_file.download_as_bytearray.assert_not_awaited(); provider.enrich.assert_not_awaited()


def test_unsupported_mime_and_telegram_failure_are_controlled(monkeypatch, tmp_path):
    store = JsonStorage(tmp_path / "data.json"); store.save(_data("text/plain")); monkeypatch.setattr(handlers, "storage", store)
    monkeypatch.setattr(handlers, "ensure_access", AsyncMock(return_value=True)); monkeypatch.setattr(handlers, "remember_current_chat", AsyncMock())
    provider = SimpleNamespace(enrich=AsyncMock()); edit = AsyncMock()
    handlers.configure_event_attachment_handlers(safe_edit_message=edit, ticket_enricher=provider)
    ctx = SimpleNamespace(user_data={}, bot=SimpleNamespace(get_file=AsyncMock(side_effect=BadRequest("gone"))))
    update = _update("att|recognize|att")
    asyncio.run(handlers.event_attachment_router(update, ctx)); ctx.bot.get_file.assert_not_awaited()
    store.save(_data()); asyncio.run(handlers.event_attachment_router(update, ctx))
    # The final controlled state replaces the progress message rather than adding another one.
    assert update.callback_query.message.reply_text.await_count == 2
    assert "Telegram" in update.callback_query.message.reply_text.return_value.edit_text.await_args.args[0]
    provider.enrich.assert_not_awaited()


@pytest.mark.parametrize(("telegram_media_type", "mime", "expected_kind", "expected_mime"), [
    ("photo", "image/jpeg", "image", "image/jpeg"),
    ("document", "image/jpeg", "image", "image/jpeg"),
    ("document", "image/png", "image", "image/png"),
    ("document", "application/pdf", "pdf", "application/pdf"),
])
def test_supported_media_routes_once_after_explicit_action(monkeypatch, tmp_path, telegram_media_type,
                                                           mime, expected_kind, expected_mime):
    data = _data(mime); data["event_attachments"][0]["telegram_media_type"] = telegram_media_type
    store = JsonStorage(tmp_path / "data.json"); store.save(data); monkeypatch.setattr(handlers, "storage", store)
    monkeypatch.setattr(handlers, "ensure_access", AsyncMock(return_value=True)); monkeypatch.setattr(handlers, "remember_current_chat", AsyncMock())
    provider = SimpleNamespace(enrich=AsyncMock(return_value=decode_ticket_enrichment(
        '{"origin":null,"destination":"B","date":null,"departure_time":null,"arrival_date":null,"arrival_time":null}')))
    tg_file = SimpleNamespace(file_size=None, download_as_bytearray=AsyncMock(return_value=bytearray(b"content")))
    ctx = SimpleNamespace(user_data={}, bot=SimpleNamespace(get_file=AsyncMock(return_value=tg_file)))
    handlers.configure_event_attachment_handlers(safe_edit_message=AsyncMock(), ticket_enricher=provider,
                                                attachment_max_bytes=100)
    asyncio.run(handlers.event_attachment_router(_update("att|detail|att"), ctx))
    ctx.bot.get_file.assert_not_awaited(); provider.enrich.assert_not_awaited()
    asyncio.run(handlers.event_attachment_router(_update("att|recognize|att"), ctx))
    ctx.bot.get_file.assert_awaited_once_with("PRIVATE_FILE_ID")
    tg_file.download_as_bytearray.assert_awaited_once()
    provider.enrich.assert_awaited_once()
    assert provider.enrich.await_args.args[1] == expected_kind
    assert provider.enrich.await_args.kwargs["mime_type"] == expected_mime


def test_actual_size_limit_with_missing_declared_size_blocks_provider(monkeypatch, tmp_path):
    store = JsonStorage(tmp_path / "data.json"); store.save(_data()); monkeypatch.setattr(handlers, "storage", store)
    monkeypatch.setattr(handlers, "ensure_access", AsyncMock(return_value=True)); monkeypatch.setattr(handlers, "remember_current_chat", AsyncMock())
    provider = SimpleNamespace(enrich=AsyncMock())
    tg_file = SimpleNamespace(file_size=None, download_as_bytearray=AsyncMock(return_value=bytearray(b"123456789")))
    ctx = SimpleNamespace(user_data={}, bot=SimpleNamespace(get_file=AsyncMock(return_value=tg_file)))
    handlers.configure_event_attachment_handlers(safe_edit_message=AsyncMock(), ticket_enricher=provider,
                                                attachment_max_bytes=8)
    asyncio.run(handlers.event_attachment_router(_update("att|recognize|att"), ctx))
    tg_file.download_as_bytearray.assert_awaited_once(); provider.enrich.assert_not_awaited()
    assert "ticket_enrichment_proposal" not in ctx.user_data


def test_concurrent_duplicate_recognition_is_coalesced_and_confirmation_does_not_recall(monkeypatch, tmp_path):
    store = JsonStorage(tmp_path / "data.json"); store.save(_data()); monkeypatch.setattr(handlers, "storage", store)
    monkeypatch.setattr(handlers, "ensure_access", AsyncMock(return_value=True)); monkeypatch.setattr(handlers, "remember_current_chat", AsyncMock())
    entered, release = asyncio.Event(), asyncio.Event()

    async def enrich(*args, **kwargs):
        entered.set(); await release.wait()
        return decode_ticket_enrichment('{"origin":null,"destination":"B","date":null,"departure_time":null,"arrival_date":null,"arrival_time":null}')

    provider = SimpleNamespace(enrich=AsyncMock(side_effect=enrich))
    tg_file = SimpleNamespace(file_size=1, download_as_bytearray=AsyncMock(return_value=bytearray(b"x")))
    ctx = SimpleNamespace(user_data={}, bot=SimpleNamespace(get_file=AsyncMock(return_value=tg_file)))
    handlers.configure_event_attachment_handlers(safe_edit_message=AsyncMock(), ticket_enricher=provider)

    async def scenario():
        first = asyncio.create_task(handlers.event_attachment_router(_update("att|recognize|att"), ctx))
        await entered.wait()
        await handlers.event_attachment_router(_update("att|recognize|att"), ctx)
        release.set(); await first
        await handlers.event_attachment_router(_update("att|saveai"), ctx)
        await handlers.event_attachment_router(_update("att|saveai"), ctx)

    asyncio.run(scenario())
    ctx.bot.get_file.assert_awaited_once(); tg_file.download_as_bytearray.assert_awaited_once()
    provider.enrich.assert_awaited_once()
    saved = store.load()["event_attachments"]
    assert len(saved) == 1 and saved[0]["destination"] == "B"
    assert "ticket_enrichment_proposal" not in ctx.user_data


def test_menu_transition_during_provider_call_discards_late_proposal(monkeypatch, tmp_path):
    store = JsonStorage(tmp_path / "data.json"); store.save(_data()); monkeypatch.setattr(handlers, "storage", store)
    monkeypatch.setattr(handlers, "ensure_access", AsyncMock(return_value=True)); monkeypatch.setattr(handlers, "remember_current_chat", AsyncMock())
    entered, release = asyncio.Event(), asyncio.Event()

    async def enrich(*args, **kwargs):
        entered.set(); await release.wait()
        return decode_ticket_enrichment('{"origin":null,"destination":"B","date":null,"departure_time":null,"arrival_date":null,"arrival_time":null}')

    provider = SimpleNamespace(enrich=AsyncMock(side_effect=enrich))
    tg_file = SimpleNamespace(file_size=1, download_as_bytearray=AsyncMock(return_value=bytearray(b"x")))
    ctx = SimpleNamespace(user_data={}, bot=SimpleNamespace(get_file=AsyncMock(return_value=tg_file)))
    handlers.configure_event_attachment_handlers(safe_edit_message=AsyncMock(), ticket_enricher=provider)

    async def scenario():
        task = asyncio.create_task(handlers.event_attachment_router(_update("att|recognize|att"), ctx))
        await entered.wait()
        handlers.discard_ticket_enrichment(ctx)  # same hook used by menu escape handlers
        release.set(); await task

    asyncio.run(scenario())
    provider.enrich.assert_awaited_once()
    assert "ticket_enrichment_proposal" not in ctx.user_data
    assert store.load()["event_attachments"][0]["destination"] is None


def test_all_null_reject_menu_and_projection_never_mutate_or_copy(monkeypatch, tmp_path):
    data = _data(); data["afisha"] = [{"id": "afi", "title": "Trip", "date": "2026-08-31", "time": "10:00",
        "end_date": "", "end_time": "", "place": "", "link": "", "status": "active",
        "notified_24h": False, "notified_morning": False}]
    data["calendars"]["vova"].append({"id": "projection", "owner": "vova", "title": "Trip", "date": "2026-08-31",
        "start_time": "10:00", "end_time": "", "comment": "", "notified_24h": False,
        "source": "afisha", "source_id": "afi"})
    item = data["event_attachments"][0]; item["parent_type"] = "afisha"; item["parent_event_id"] = "afi"
    store = JsonStorage(tmp_path / "data.json"); store.save(data); monkeypatch.setattr(handlers, "storage", store)
    monkeypatch.setattr(handlers, "ensure_access", AsyncMock(return_value=True)); monkeypatch.setattr(handlers, "remember_current_chat", AsyncMock())
    provider = SimpleNamespace(enrich=AsyncMock(return_value=decode_ticket_enrichment(
        '{"origin":null,"destination":null,"date":null,"departure_time":null,"arrival_date":null,"arrival_time":null}')))
    tg_file = SimpleNamespace(file_size=1, download_as_bytearray=AsyncMock(return_value=bytearray(b"x")))
    ctx = SimpleNamespace(user_data={"event_attachment_parent": ("afisha", "afi")},
                          bot=SimpleNamespace(get_file=AsyncMock(return_value=tg_file)))
    handlers.configure_event_attachment_handlers(safe_edit_message=AsyncMock(), ticket_enricher=provider)
    before = store.load()["event_attachments"]
    projection_update = _update("att|cal|vova|projection|0")
    asyncio.run(handlers.show_documents(projection_update, ctx, "calendar", "projection", "back"))
    assert ctx.user_data["event_attachment_parent"] == ("afisha", "afi")
    asyncio.run(handlers.event_attachment_router(_update("att|recognize|att"), ctx))
    assert store.load()["event_attachments"] == before and "ticket_enrichment_proposal" not in ctx.user_data
    assert len(store.load()["event_attachments"]) == 1


def test_native_flow_and_stale_confirmation_do_not_cross_consent_boundary(monkeypatch, tmp_path):
    store = JsonStorage(tmp_path / "data.json"); store.save(_data()); monkeypatch.setattr(handlers, "storage", store)
    monkeypatch.setattr(handlers, "ensure_access", AsyncMock(return_value=True)); monkeypatch.setattr(handlers, "remember_current_chat", AsyncMock())
    provider = SimpleNamespace(enrich=AsyncMock())
    ctx = SimpleNamespace(user_data={"event_attachment_parent": ("calendar", "cal"),
        "event_attachment_transport_type": "train", "event_attachment_draft": {
            "telegram_media_type": "document", "telegram_file_id": "draft", "telegram_file_unique_id": "draft-u",
            "file_name": "ticket.pdf", "mime_type": "application/pdf"}},
        bot=SimpleNamespace(get_file=AsyncMock()))
    handlers.configure_event_attachment_handlers(safe_edit_message=AsyncMock(), ticket_enricher=provider)
    # Choosing type/transport, manual editing, skip UI, and stale confirmation never retrieve/analyze.
    awaitables = ["att|type|transport_ticket", "att|transport|train", "att|saveai", "att|rejectai"]
    for callback in awaitables:
        asyncio.run(handlers.event_attachment_router(_update(callback), ctx))
    ctx.bot.get_file.assert_not_awaited(); provider.enrich.assert_not_awaited()
    assert "ticket_enrichment_proposal" not in ctx.user_data


def test_missing_model_hides_actions_and_leaves_documents_operational(monkeypatch, tmp_path):
    store = JsonStorage(tmp_path / "data.json"); store.save(_data()); monkeypatch.setattr(handlers, "storage", store)
    edit = AsyncMock(); ctx = SimpleNamespace(user_data={}, bot=SimpleNamespace())
    handlers.configure_event_attachment_handlers(safe_edit_message=edit, ticket_enricher=None)
    asyncio.run(handlers.show_detail(_update("att|detail|att"), ctx, "att"))
    labels = [button.text for row in edit.await_args.kwargs["reply_markup"].inline_keyboard for button in row]
    assert "✨ Распознать данные" not in labels
    assert {"📤 Отправить файл", "✏️ Изменить данные", "🗑 Удалить"} <= set(labels)


def test_native_draft_recognition_creates_only_after_confirmation_and_once(monkeypatch, tmp_path):
    base = _data(); base["event_attachments"] = []
    store = JsonStorage(tmp_path / "data.json"); store.save(base); monkeypatch.setattr(handlers, "storage", store)
    monkeypatch.setattr(handlers, "ensure_access", AsyncMock(return_value=True)); monkeypatch.setattr(handlers, "remember_current_chat", AsyncMock())
    provider = SimpleNamespace(enrich=AsyncMock(return_value=decode_ticket_enrichment(
        '{"origin":"Москва","destination":"Воронеж","date":"2026-08-30","departure_time":"23:38","arrival_date":"2026-08-31","arrival_time":"09:33"}')))
    tg_file = SimpleNamespace(file_size=3, download_as_bytearray=AsyncMock(return_value=bytearray(b"pdf")))
    draft = {"telegram_media_type": "document", "telegram_file_id": "draft", "telegram_file_unique_id": "draft-u",
             "file_name": "ticket.pdf", "mime_type": "application/pdf"}
    ctx = SimpleNamespace(user_data={"event_attachment_parent": ("calendar", "cal"),
        "event_attachment_transport_type": "train", "event_attachment_draft": draft},
        bot=SimpleNamespace(get_file=AsyncMock(return_value=tg_file)))
    handlers.configure_event_attachment_handlers(safe_edit_message=AsyncMock(), ticket_enricher=provider)
    assert asyncio.run(handlers.event_attachment_router(_update("att|recognizenew"), ctx)) == handlers.CONFIRMING_TICKET_ENRICHMENT
    assert store.load()["event_attachments"] == []
    asyncio.run(handlers.event_attachment_router(_update("att|saveai"), ctx))
    asyncio.run(handlers.event_attachment_router(_update("att|saveai"), ctx))
    saved = store.load()["event_attachments"]
    assert len(saved) == 1 and saved[0]["destination"] == "Воронеж" and saved[0]["arrival_time"] == "09:33"
    provider.enrich.assert_awaited_once()


def test_native_type_keyboard_has_one_canonical_voucher(monkeypatch):
    monkeypatch.setattr(handlers, "ensure_access", AsyncMock(return_value=True)); monkeypatch.setattr(handlers, "remember_current_chat", AsyncMock())
    message = SimpleNamespace(document=SimpleNamespace(file_id="id", file_unique_id="u", file_name="v.pdf", mime_type="application/pdf"),
                              photo=[], reply_text=AsyncMock())
    update = SimpleNamespace(message=message, effective_user=SimpleNamespace(username="wp_bvv"), effective_chat=SimpleNamespace(id=1))
    ctx = SimpleNamespace(user_data={})
    asyncio.run(handlers.receive_file(update, ctx))
    buttons = [b for row in message.reply_text.await_args.kwargs["reply_markup"].inline_keyboard for b in row]
    assert [b.text for b in buttons].count("🏨 Ваучер / проживание") == 1
    voucher = next(b for b in buttons if b.text == "🏨 Ваучер / проживание")
    assert voucher.callback_data == "att|type|voucher"
