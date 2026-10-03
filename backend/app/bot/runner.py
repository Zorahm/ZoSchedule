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
from aiogram.types import BotCommand, BotCommandScopeChat

from app.bot import errors, handlers
from app.bot.middleware import UpdateOffset
from app.render.renderer import PlaywrightRenderer
from app.bot.service import BotService
from app.config import AppConfig
from app.snapshots.service import ScheduleService
from app.web import server as web_server

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
        schedule = schedule or ScheduleService(config)
        yield BotService(
            config, bot, PlaywrightRenderer(config.bot.browser_path), schedule, schedule
        )
    finally:
        await bot.session.close()


async def _on_startup(bot: Bot, service: BotService) -> None:
    # Cosmetic, and Telegram may be unreachable right now: the bot must still start.
    try:
        me = await bot.me()
        if not me.can_read_all_group_messages:
            # Group Privacy hides ordinary messages from the bot: the curator's notice
            # would never arrive. Commands still work, so this is only a warning.
            logger.warning(
                "У бота включён Group Privacy: сообщения куратора в группах не дойдут. "
                "Отключите в @BotFather (/setprivacy → Disable) и добавьте бота в группы заново "
                "или сделайте его администратором"
            )
        await bot.set_my_commands(
            [
                BotCommand(command="go", description="Запустить бота в этом чате"),
                BotCommand(command="stop", description="Перестать писать в этот чат"),
            ]
        )
    except errors.TELEGRAM_ERRORS as error:
        logger.warning("Не удалось представиться Telegram: %s", error)
    else:
        logger.info("Бот слушает команды как @%s", me.username)
    if service.web_url is not None:
        await _announce_journal(bot, service)


async def _announce_journal(bot: Bot, service: BotService) -> None:
    """The headmen's private chats get /attendance in their command menu. Cosmetic, per person.

    A person who has never written to the bot has no chat with it yet: Telegram refuses, and
    that is fine, the command appears after their first /start.
    """
    command = BotCommand(command="attendance", description="Журнал посещаемости")
    for user_id in sorted(service.headmen):
        try:
            await bot.set_my_commands([command], scope=BotCommandScopeChat(chat_id=user_id))
        except errors.TELEGRAM_ERRORS as error:
            logger.info("Команду журнала для %s не поставить (чата с ботом ещё нет?): %s", user_id, error)


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
        loops = [
            run_ticks(service),
            dispatcher.start_polling(  # pyright: ignore[reportUnknownMemberType]  # aiogram's UNSET default
                service.bot,
                allowed_updates=["message", "my_chat_member"],
                # One update at a time, as before: two /go at once would race on the chat.
                handle_as_tasks=False,
                # The bot may live inside another app that owns the signals.
                handle_signals=False,
            ),
        ]
        if config.web.enabled:
            if not (config.bot.headmen or config.bot.trusted_users):
                logger.warning("headmen и trusted_users пусты: в журнал посещаемости не войдёт никто")
            # The journal's own failure to start is logged inside and never stops the bot.
            loops.append(web_server.serve(config, service))
        await asyncio.gather(*loops)
