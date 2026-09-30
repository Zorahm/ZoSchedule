"""Chat commands and housekeeping. The only command is /go: "start working in this chat".

Group privacy stays on: Telegram delivers commands to such a bot, and the bot
has no use for ordinary messages.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramRetryAfter
from aiogram.filters import Command
from aiogram.types import ChatMemberAdministrator, ChatMemberOwner, Message

from app.bot import errors
from app.bot.service import BotService

logger = logging.getLogger(__name__)

NOT_A_GROUP = "Добавьте меня в группу и напишите /go там."
ADMINS_ONLY = "Запускать меня может только администратор группы."

_DELETE_TRIES = 3
_DELETE_PAUSE = 2.0

_IN_GROUP = F.chat.type.in_({"group", "supergroup"})


async def _is_admin(bot: Bot, message: Message) -> bool:
    if message.sender_chat is not None and message.sender_chat.id == message.chat.id:
        return True  # an admin writing anonymously "on behalf of the group"
    if message.from_user is None:
        return False
    member = await bot.get_chat_member(chat_id=message.chat.id, user_id=message.from_user.id)
    return isinstance(member, ChatMemberOwner | ChatMemberAdministrator)


async def go_outside_a_group(message: Message) -> None:
    await message.answer(NOT_A_GROUP, disable_notification=True)


async def go(message: Message, bot: Bot, service: BotService) -> None:
    if not await _is_admin(bot, message):
        await message.answer(ADMINS_ONLY, disable_notification=True)
        return

    # aiogram's `answer` already targets the topic; the service needs it to post there.
    thread_id = message.message_thread_id if message.is_topic_message else None
    problem = await service.go(str(message.chat.id), thread_id)
    if problem is not None:
        await message.answer(problem, disable_notification=True)
        return
    try:
        await message.delete()  # tidy: the command did its job
    except errors.TELEGRAM_ERRORS:
        pass  # no delete right: leaving the command in the chat is harmless


async def _delete_notice(message: Message) -> None:
    """Deletes the notice, retrying what may pass: one network blip must not leave it for good."""
    for attempt in range(1, _DELETE_TRIES + 1):
        pause = _DELETE_PAUSE
        try:
            await errors.tolerate(message.delete(), errors.ALREADY_GONE)
            return
        except TelegramRetryAfter as error:
            pause = error.retry_after
        except errors.TELEGRAM_ERRORS as error:
            if not errors.is_transient(error):
                logger.warning("Не удалось убрать служебное сообщение о закрепе: %s", error)
                return
        if attempt < _DELETE_TRIES:
            await asyncio.sleep(pause)
    logger.warning("Служебное сообщение о закрепе не удалось убрать за %d попыток", _DELETE_TRIES)


async def tidy_pin_notice(message: Message, bot: Bot) -> None:
    """Deletes the "bot pinned a message" notice, when the bot itself pinned.

    Being written by this bot is the whole test: a person's pin comes from that
    person and stays. The bot's own ledger is deliberately not consulted. The picture
    may already be forgotten there (a second /go retires the first week before its
    notice is read), or it may have been pinned by another process with its own
    database: a simulation, a manual `post-week`.
    """
    if message.from_user is None or message.from_user.id != bot.id:
        return
    await _delete_notice(message)


def build_router() -> Router:
    """A fresh router each time: aiogram lets a router join only one dispatcher."""
    router = Router(name="commands")
    router.message.register(go_outside_a_group, Command("go", ignore_case=True), ~_IN_GROUP)
    router.message.register(go, Command("go", ignore_case=True), _IN_GROUP)
    router.message.register(tidy_pin_notice, F.pinned_message)
    return router
