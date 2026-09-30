"""The optional proxy for Telegram (ZOSCHEDULE_BOT_PROXY)."""

from __future__ import annotations

import asyncio
import traceback
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from aiogram.client.telegram import TelegramAPIServer
from aiohttp import web
from aiohttp.test_utils import TestServer
from pydantic import ValidationError

from app.bot import errors
from app.bot.runner import make_bot
from app.config import BotConfig, load_config

TOKEN = "4242:TEST"
SECRET = "p4ssw0rd"


def _config_with(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, proxy: str | None) -> BotConfig:
    monkeypatch.setenv("ZOSCHEDULE_ENV_FILE", "")
    monkeypatch.setenv("ZOSCHEDULE_BOT_TOKEN", TOKEN)
    if proxy is None:
        monkeypatch.delenv("ZOSCHEDULE_BOT_PROXY", raising=False)
    else:
        monkeypatch.setenv("ZOSCHEDULE_BOT_PROXY", proxy)
    return load_config().bot


# -- the setting -----------------------------------------------------------------------


@pytest.mark.parametrize("value", [None, "", "   "])
def test_without_a_proxy_nothing_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: str | None
) -> None:
    bot = _config_with(monkeypatch, tmp_path, value)

    assert bot.proxy_url is None and bot.proxy_label is None


@pytest.mark.parametrize(
    "value",
    [
        "socks5://127.0.0.1:1080",
        "socks4://proxy.example:1080",
        "http://proxy.example:3128",
        "https://proxy.example:3128",
        f"socks5://user:{SECRET}@proxy.example:1080",
    ],
)
def test_the_supported_schemes_are_accepted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: str
) -> None:
    assert _config_with(monkeypatch, tmp_path, value).proxy_url == value


def test_the_proxy_is_a_secret_and_its_log_label_has_no_login_or_password(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bot = _config_with(monkeypatch, tmp_path, f"socks5://user:{SECRET}@proxy.example:1080")

    assert bot.proxy_label == "socks5://proxy.example:1080"
    assert SECRET not in repr(bot) and "user" not in (bot.proxy_label or "")


@pytest.mark.parametrize(
    "value",
    [
        f"user:{SECRET}@proxy.example:1080",  # no scheme
        f"ftp://user:{SECRET}@proxy.example:21",  # not a proxy scheme
        f"socks5://user:{SECRET}@proxy.example",  # no port
        f"socks5://user:{SECRET}@:1080",  # no host
        f"socks5://user:{SECRET}@proxy.example:notaport",
    ],
)
def test_a_malformed_value_is_refused_with_a_hint_and_without_echoing_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: str
) -> None:
    with pytest.raises(ValidationError) as raised:
        _config_with(monkeypatch, tmp_path, value)

    text = str(raised.value)
    assert "ZOSCHEDULE_BOT_PROXY: ожидается socks5://" in text
    assert SECRET not in text  # the password must not reach the console or a log


# -- the traffic ---------------------------------------------------------------------------


async def test_a_dead_proxy_stops_the_bot_so_the_traffic_really_goes_through_it() -> None:
    bot = make_bot(TOKEN, proxy=f"socks5://user:{SECRET}@127.0.0.1:9")  # nothing listens here
    try:
        with pytest.raises(errors.PROXY_ERRORS) as raised:
            await bot.get_me()
    finally:
        await bot.session.close()

    assert isinstance(raised.value, errors.PROXY_ERRORS)
    assert errors.is_transient(raised.value)  # kept for a retry, like any network trouble
    rendered = "".join(traceback.format_exception(raised.value)) + repr(raised.value)
    assert SECRET not in rendered and TOKEN not in rendered


class _Socks5Server:
    """Just enough SOCKS5 (no auth, CONNECT) to watch a request being tunnelled."""

    def __init__(self) -> None:
        self.targets: list[tuple[str, int]] = []
        self.port = 0
        self._server: asyncio.Server | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._client, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        assert self._server is not None
        self._server.close()
        await self._server.wait_closed()

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            _, count = await reader.readexactly(2)
            await reader.readexactly(count)
            writer.write(b"\x05\x00")  # no authentication
            _, _, _, kind = await reader.readexactly(4)
            if kind == 1:
                host = ".".join(str(part) for part in await reader.readexactly(4))
            elif kind == 3:
                host = (await reader.readexactly((await reader.readexactly(1))[0])).decode()
            else:
                raise ValueError("unsupported address type")
            port = int.from_bytes(await reader.readexactly(2), "big")
            self.targets.append((host, port))
            upstream_reader, upstream_writer = await asyncio.open_connection(host, port)
            writer.write(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
            await writer.drain()
            await asyncio.gather(
                self._pipe(reader, upstream_writer), self._pipe(upstream_reader, writer)
            )
        except (asyncio.IncompleteReadError, ConnectionError, ValueError):
            pass
        finally:
            writer.close()

    @staticmethod
    async def _pipe(source: asyncio.StreamReader, sink: asyncio.StreamWriter) -> None:
        try:
            while data := await source.read(65536):
                sink.write(data)
                await sink.drain()
        except ConnectionError:
            pass
        finally:
            sink.close()


@pytest.fixture
async def socks() -> AsyncIterator[_Socks5Server]:
    server = _Socks5Server()
    await server.start()
    yield server
    await server.stop()


async def test_a_request_to_telegram_is_tunnelled_through_the_socks5_proxy(
    socks: _Socks5Server,
) -> None:
    async def handle(request: web.Request) -> web.Response:
        return web.json_response(
            {"ok": True, "result": {"id": 4242, "is_bot": True, "first_name": "Z", "username": "z"}}
        )

    app = web.Application()
    app.router.add_post("/bot{token}/{method}", handle)
    target = TestServer(app)
    await target.start_server()
    target_port = target.make_url("").port
    bot = make_bot(TOKEN, proxy=f"socks5://127.0.0.1:{socks.port}")
    bot.session.api = TelegramAPIServer.from_base(str(target.make_url("")).rstrip("/"))
    try:
        me = await bot.get_me()
    finally:
        await bot.session.close()
        await target.close()

    assert me.username == "z"
    assert socks.targets == [("127.0.0.1", target_port)]  # it went through the proxy
