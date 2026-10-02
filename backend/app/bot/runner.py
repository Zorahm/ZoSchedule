"""Wiring and the running loops."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from app.bot import errors, handlers
from app.bot.middleware import UpdateOffset
from app.bot.renderer import PlaywrightRenderer
from app.bot.service import BotService
from app.config import AppConfig
from app.snapshots.service import ScheduleService

logger = logging.getLogger(__name__)

TICK_SECONDS = 60


def make_bot(token: str, session: BaseSession | None = None, *, proxy: str | None = None) -> Bot:
    """Texts are HTML (the formatter escapes site text) and never unfurl links.

    `proxy` (socks5://, socks4://, http://, https://) carries every request to Telegram;
    it is ignored when a ready `session` is given.
    """
    if session is None and proxy:
        session = AiohttpSession(proxy=proxy)
    return Bot(
        token,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
    )


@asynccontextmanager
async def open_service(
    config: AppConfig, schedule: ScheduleService | None = None
) -> AsyncGenerator[BotService]:
    """`schedule` is the API's own service when the bot runs inside it, so both share
    one lock; standalone the bot brings its own and polls the site itself."""
    bot = make_bot(config.bot.token.get_secret_value(), proxy=config.bot.proxy_url)
    try:
        yield BotService(
            config, bot, PlaywrightRenderer(config.bot.browser_path), schedule or ScheduleService(config)
        )
    finally:
        await bot.session.close()


async def _on_startup(bot: Bot) -> None:
    # Cosmetic, and Telegram may be unreachable right now: the bot must still start.
    try:
        me = await bot.me()
        await bot.set_my_commands(
            [BotCommand(command="go", description="Запустить бота в этом чате")]
        )
    except errors.TELEGRAM_ERRORS as error:
        logger.warning("Не удалось представиться Telegram: %s", error)
    else:
        logger.info("Бот слушает команды как @%s", me.username)


def build_dispatcher(service: BotService) -> Dispatcher:
    dispatcher = Dispatcher(service=service)  # handlers receive it by parameter name
    dispatcher.update.outer_middleware(UpdateOffset(service.db_path))
    dispatcher.include_router(handlers.build_router())
    dispatcher.startup.register(_on_startup)
    return dispatcher


async def run_ticks(service: BotService, interval: int = TICK_SECONDS) -> None:
    while True:
        try:
            await service.tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            # tick() guards each job itself; this is the last line of defence so the
            # loop cannot die and leave the chat silent until a restart.
            logger.exception("Непредвиденный сбой цикла бота")
        await asyncio.sleep(interval)


async def run_forever(config: AppConfig, schedule: ScheduleService | None = None) -> None:
    """The whole bot: the schedule loop and the command listener side by side."""
    async with open_service(config, schedule) as service:
        logger.info("Бот запущен, проверка раз в %d с", TICK_SECONDS)
        if not config.bot.trusted_users:
            logger.warning(
                "trusted_users пуст: команду /go не примет никто, а из любой группы, "
                "куда бота добавят (кроме trusted_chats), он выйдет. Задайте id в "
                "config.toml или ZOSCHEDULE_BOT_TRUSTED_USERS"
            )
        if config.bot.proxy_label:
            logger.info("Telegram через прокси %s", config.bot.proxy_label)
        dispatcher = build_dispatcher(service)
        await asyncio.gather(
            run_ticks(service),
            dispatcher.start_polling(  # pyright: ignore[reportUnknownMemberType]  # aiogram's UNSET default
                service.bot,
                allowed_updates=["message", "my_chat_member"],
                # One update at a time, as before: two /go at once would race on the chat.
                handle_as_tasks=False,
                # The bot may live inside another app that owns the signals.
                handle_signals=False,
            ),
        )
