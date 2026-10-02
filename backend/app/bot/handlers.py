"""Chat commands and housekeeping. /go: "start working in this chat"; /stop: "stop".

Besides the commands the bot reads one kind of ordinary message: a curator's notice
that a lesson is in another room. Telegram hides ordinary messages from a bot with
Group Privacy on, so it has to be off (or the bot an administrator).

The bot can work in several groups at once, each added by its own /go.

Only the people in `bot.trusted_users` command the bot. Everyone else is ignored
without a word (an answer would only tell a stranger that the bot is alive), and a
group that a stranger adds the bot to is left at once, unless the group is listed in
`bot.trusted_chats`.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramRetryAfter
from aiogram.filters import Command
from aiogram.enums import ChatMemberStatus
from aiogram.types import ChatMemberUpdated, InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo

from app.bot import errors
from app.bot.service import BotService
from app.parsing.curator import parse_room_notice

logger = logging.getLogger(__name__)

NOT_A_GROUP = "Добавьте меня в группу и напишите /go там."

_DELETE_TRIES = 3
_DELETE_PAUSE = 2.0

_IN_GROUP = F.chat.type.in_({"group", "supergroup"})
_BOT_IS_OUT = F.new_chat_member.status.in_({ChatMemberStatus.LEFT, ChatMemberStatus.KICKED})


def _is_trusted(message: Message, service: BotService) -> bool:
    user = message.from_user
    if user is not None and user.id in service.trusted_users:
        return True
    # The log is where the owner finds the id to put into trusted_users. An anonymous
    # group admin arrives as a service account and is never trusted.
    logger.warning(
        "Игнорирую /go от пользователя %s (%s): его нет в trusted_users",
        user.id if user else "?",
        f"@{user.username}" if user and user.username else "без ника",
    )
    return False


async def go_outside_a_group(message: Message, service: BotService) -> None:
    if _is_trusted(message, service):
        await message.answer(NOT_A_GROUP, disable_notification=True)


async def go(message: Message, service: BotService) -> None:
    if not _is_trusted(message, service):
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


async def stop(message: Message, service: BotService) -> None:
    """/stop: the bot stops posting to this group (it stays a member, the posts stay too)."""
    if not _is_trusted(message, service):
        return
    if service.remove_target(str(message.chat.id)):
        await message.answer("Больше не пишу в этот чат. Вернуть: /go.", disable_notification=True)
    else:
        await message.answer("Сюда я и не пишу. Начать: /go.", disable_notification=True)


async def open_journal(message: Message, service: BotService) -> None:
    """/start and /attendance in a private chat: a button that opens the attendance journal.

    Only for headmen. Anyone else is ignored without a word, like everywhere in this bot.
    """
    user = message.from_user
    if user is None or user.id not in service.headmen:
        return
    url = service.web_url
    if url is None:
        await message.answer(
            "Журнал выключен: задайте web.public_url в config.toml (или ZOSCHEDULE_WEB_URL в .env).",
            disable_notification=True,
        )
        return
    button = InlineKeyboardButton(text="Открыть журнал", web_app=WebAppInfo(url=url))
    await message.answer(
        "Журнал посещаемости. День открывается сам, когда начинается первая пара.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[button]]),
        disable_notification=True,
    )
    await service.pin_journal_button(user.id)


async def curator_notice(message: Message, service: BotService) -> None:
    """A curator's "в 13.50 у ОККИПд-307 пара будет в 314 аудитории": change the room.

    Only people from `curators` / `trusted_users`, only in a group the bot works in.
    Everything else in the chat is none of the bot's business and gets no answer.
    """
    notice = parse_room_notice(message.text or "")
    if notice is None or not service.works_in(str(message.chat.id)):
        return
    user = message.from_user
    if user is None or user.id not in service.curators:
        # The log is where the owner finds the id to put into curators.
        logger.warning(
            "Игнорирую сообщение о смене аудитории от пользователя %s (%s): его нет в curators",
            user.id if user else "?",
            f"@{user.username}" if user and user.username else "без ника",
        )
        return
    reply = await service.correct_room(
        notice, author_id=user.id, chat_id=str(message.chat.id), text=message.text or ""
    )
    if reply is not None:
        await message.reply(reply, disable_notification=True)


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


async def leave_a_group_a_stranger_added_me_to(
    event: ChatMemberUpdated, bot: Bot, service: BotService
) -> None:
    """The bot is added to a group: stay if the group is whitelisted or a trusted person
    did it, otherwise leave.

    Only a fresh join counts; a promotion to administrator or a change of rights in
    a group the bot already belongs to is not "being added".
    """
    if event.chat.type not in ("group", "supergroup"):
        return
    was_out = event.old_chat_member.status in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED)
    is_in = event.new_chat_member.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR)
    if not (was_out and is_in):
        return
    if event.chat.id in service.trusted_chats or event.from_user.id in service.trusted_users:
        return
    logger.warning(
        "Пользователь %s (не из trusted_users) добавил бота в группу %s, её нет в "
        "trusted_chats: выхожу",
        event.from_user.id,
        event.chat.id,
    )
    try:
        await bot.leave_chat(chat_id=event.chat.id)
    except errors.TELEGRAM_ERRORS as error:
        logger.warning("Не удалось выйти из группы %s: %s", event.chat.id, error)


async def forget_a_group_the_bot_left(event: ChatMemberUpdated, service: BotService) -> None:
    """Kicked or left: stop posting there, or every tick would fail on that chat."""
    if service.remove_target(str(event.chat.id)):
        logger.warning("Бота убрали из группы %s: больше не пишу туда", event.chat.id)


def build_router() -> Router:
    """A fresh router each time: aiogram lets a router join only one dispatcher."""
    router = Router(name="commands")
    router.message.register(go_outside_a_group, Command("go", ignore_case=True), ~_IN_GROUP)
    router.message.register(go, Command("go", ignore_case=True), _IN_GROUP)
    router.message.register(stop, Command("stop", ignore_case=True), _IN_GROUP)
    router.message.register(
        open_journal, Command("start", "attendance", ignore_case=True), F.chat.type == "private"
    )
    router.message.register(tidy_pin_notice, F.pinned_message)
    # aiogram runs only the first handler whose filters pass, so the two must not overlap:
    # this one takes the bot's departure, the next one its arrival.
    router.my_chat_member.register(forget_a_group_the_bot_left, _IN_GROUP, _BOT_IS_OUT)
    router.my_chat_member.register(leave_a_group_a_stranger_added_me_to)
    # Last: a command or a service message must never reach the notice parser.
    router.message.register(curator_notice, _IN_GROUP, F.text)
    return router
