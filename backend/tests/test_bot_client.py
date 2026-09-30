"""The real `Bot` against a local server that speaks the Bot API's wire format.

Checks what the bot relies on from aiogram: the HTML default, how a picture is
uploaded, how failures are told apart, and that the token never reaches an error.
"""

from __future__ import annotations

import json
import traceback
from collections.abc import AsyncIterator
from typing import Any

import pytest
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.exceptions import (
    ClientDecodeError,
    TelegramBadRequest,
    TelegramEntityTooLarge,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.methods import DeleteMessage
from aiogram.types import BufferedInputFile, InputMediaPhoto
from aiohttp import web
from aiohttp.test_utils import TestServer

from app.bot import errors
from app.bot.runner import make_bot

TOKEN = "4242:SECRET"


class _Recorder:
    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.status = 200
        self.reply: dict[str, Any] = {"ok": True, "result": {"message_id": 42, "date": 0, "chat": {"id": -100, "type": "supergroup"}}}


@pytest.fixture
async def server() -> AsyncIterator[tuple[TestServer, _Recorder]]:
    recorder = _Recorder()

    async def handle(request: web.Request) -> web.Response:
        form = await request.post()
        fields: dict[str, Any] = {
            key: value.file.read() if isinstance(value, web.FileField) else value
            for key, value in form.items()
        }
        recorder.requests.append((request.match_info["method"], fields))
        return web.json_response(recorder.reply, status=recorder.status)

    app = web.Application()
    app.router.add_post("/bot{token}/{method}", handle)
    test_server = TestServer(app)
    await test_server.start_server()
    yield test_server, recorder
    await test_server.close()


def _bot(base: str) -> Bot:
    session = AiohttpSession(api=TelegramAPIServer.from_base(base.rstrip("/")))
    return make_bot(TOKEN, session)


@pytest.fixture
async def bot(server: tuple[TestServer, _Recorder]) -> AsyncIterator[Bot]:
    made = _bot(str(server[0].make_url("")))
    yield made
    await made.session.close()


async def test_texts_go_out_as_html_without_link_previews(
    bot: Bot, server: tuple[TestServer, _Recorder]
) -> None:
    await bot.send_message(chat_id="-100", text="<b>привет</b>", message_thread_id=2)

    method, fields = server[1].requests[0]
    assert method == "sendMessage" and fields["message_thread_id"] == "2"
    assert fields["parse_mode"] == "HTML"
    assert json.loads(fields["link_preview_options"]) == {"is_disabled": True}


async def test_a_picture_is_uploaded_as_a_file_with_its_caption(
    bot: Bot, server: tuple[TestServer, _Recorder]
) -> None:
    await bot.send_photo(
        chat_id="-100", photo=BufferedInputFile(b"\x89PNGdata", "s.png"), caption="📅 подпись"
    )

    method, fields = server[1].requests[0]
    assert method == "sendPhoto" and fields["chat_id"] == "-100"
    assert fields["caption"] == "📅 подпись" and fields["parse_mode"] == "HTML"
    assert fields["photo"].startswith("attach://")
    assert fields[fields["photo"].removeprefix("attach://")] == b"\x89PNGdata"


async def test_editing_a_picture_points_the_media_at_the_attachment(
    bot: Bot, server: tuple[TestServer, _Recorder]
) -> None:
    await bot.edit_message_media(
        InputMediaPhoto(media=BufferedInputFile(b"png", "s.png"), caption="новая подпись"),
        chat_id="-100",
        message_id=7,
    )

    method, fields = server[1].requests[0]
    media = json.loads(fields["media"])
    assert method == "editMessageMedia" and fields["message_id"] == "7"
    assert media["type"] == "photo" and media["media"].startswith("attach://")
    assert media["caption"] == "новая подпись"
    assert fields[media["media"].removeprefix("attach://")] == b"png"


def _fail(recorder: _Recorder, status: int, description: str, **extra: Any) -> None:
    recorder.status = status
    recorder.reply = {"ok": False, "error_code": status, "description": description, **extra}


async def test_not_modified_is_not_an_error_but_other_bad_requests_are(
    bot: Bot, server: tuple[TestServer, _Recorder]
) -> None:
    _fail(server[1], 400, "Bad Request: message is not modified: specified new message content")
    assert await errors.tolerate(bot.delete_message(chat_id="-100", message_id=1), errors.NOT_MODIFIED) is False

    _fail(server[1], 400, "Bad Request: chat not found")
    with pytest.raises(TelegramBadRequest):
        await errors.tolerate(bot.delete_message(chat_id="-100", message_id=1), errors.NOT_MODIFIED)


async def test_a_deleted_message_is_tolerated_but_lost_rights_are_not(
    bot: Bot, server: tuple[TestServer, _Recorder]
) -> None:
    _fail(server[1], 400, "Bad Request: message to delete not found")
    assert await errors.tolerate(bot.delete_message(chat_id="-100", message_id=1), errors.ALREADY_GONE) is False

    _fail(server[1], 403, "Forbidden: bot was kicked from the supergroup chat")
    with pytest.raises(TelegramForbiddenError) as raised:
        await errors.tolerate(bot.delete_message(chat_id="-100", message_id=1), errors.ALREADY_GONE)
    assert errors.is_transient(raised.value) is False


async def test_flood_control_and_server_errors_are_worth_a_retry(
    bot: Bot, server: tuple[TestServer, _Recorder]
) -> None:
    _fail(server[1], 429, "Too Many Requests: retry after 3", parameters={"retry_after": 3})
    with pytest.raises(TelegramRetryAfter) as flood:
        await bot.send_message(chat_id="-100", text="x")
    assert errors.is_transient(flood.value) is True

    _fail(server[1], 500, "Internal Server Error")
    with pytest.raises(TelegramServerError) as broken:
        await bot.send_message(chat_id="-100", text="x")
    assert errors.is_transient(broken.value) is True


async def test_a_gateway_error_page_is_not_a_crash_and_is_worth_a_retry() -> None:
    async def gateway_error(request: web.Request) -> web.Response:
        return web.Response(status=502, text="<html>Bad gateway</html>", content_type="text/html")

    app = web.Application()
    app.router.add_post("/bot{token}/{method}", gateway_error)
    bad = TestServer(app)
    await bad.start_server()
    made = _bot(str(bad.make_url("")))
    try:
        with pytest.raises(ClientDecodeError) as raised:
            await made.send_message(chat_id="-100", text="x")
    finally:
        await made.session.close()
        await bad.close()

    assert errors.is_transient(raised.value) is True


async def test_an_unreachable_server_is_transient_and_the_token_never_leaks() -> None:
    made = _bot("http://127.0.0.1:9")  # nothing listens here
    try:
        with pytest.raises(TelegramNetworkError) as raised:
            await made.send_message(chat_id="-100", text="привет")
    finally:
        await made.session.close()

    assert errors.is_transient(raised.value) is True
    # Log lines and tracebacks are where a token in a URL would end up.
    rendered = "".join(traceback.format_exception(raised.value)) + repr(raised.value)
    assert TOKEN not in rendered and "SECRET" not in rendered


def test_a_file_the_server_refuses_to_take_is_not_retried() -> None:
    too_large = TelegramEntityTooLarge(DeleteMessage(chat_id=1, message_id=1), "too large")
    assert errors.is_transient(too_large) is False
