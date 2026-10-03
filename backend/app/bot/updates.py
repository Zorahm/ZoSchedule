"""Telling chats what changed: change texts, and posted pictures redrawn in place."""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Awaitable

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BufferedInputFile, InputMediaPhoto

from app import moscow, texts
from app.bot import errors
from app.bot import store as bot_store
from app.bot.context import BotContext
from app.bot.store import Target
from app.db import connect
from app.models.changes import ChangeEvent
from app.render.pictures import Picture, target_monday
from app.render.view import WEEK_DAYS
from app.snapshots import store

logger = logging.getLogger(__name__)


class Updater:
    def __init__(self, ctx: BotContext) -> None:
        self._ctx = ctx

    # -- changes --------------------------------------------------------------

    async def announce_changes(self, *, target: Target | None = None) -> int:
        """Sends new change events as text. Returns how many were announced, over all chats."""
        return sum([await self._announce_to(t) for t in self._ctx.scope(target)])

    async def _announce_to(self, target: Target) -> int:
        today = moscow.today()
        with connect(self._ctx.config.db_path) as conn:
            last = bot_store.last_event_id(conn, target.chat)
            if last is None:
                # First run in this chat: what is already in the feed is history, not news.
                bot_store.set_last_event_id(conn, target.chat, store.max_event_id(conn))
                return 0
            events = store.events_after(conn, last)
        if not events:
            return 0

        # Only the week the chat is looking at. A change for a lesson months away is
        # noise; it reaches people through that week's picture when its turn comes.
        week_end = target_monday(today) + dt.timedelta(days=WEEK_DAYS - 1)
        relevant = [event for event in events if today <= event.date <= week_end]
        await self._send_changes(target, relevant, today)
        with connect(self._ctx.config.db_path) as conn:
            bot_store.set_last_event_id(conn, target.chat, max(event.id for event in events))
        return len(relevant)

    async def _send_changes(self, target: Target, events: list[ChangeEvent], today: dt.date) -> None:
        for text in texts.format_changes(events):
            sent = await self._ctx.bot.send_message(
                chat_id=target.chat,
                text=text,
                message_thread_id=target.thread,
                disable_notification=self._ctx.config.bot.silent,
            )
            with connect(self._ctx.config.db_path) as conn:
                bot_store.record(
                    conn, chat_id=target.chat, kind="changes", day=today, message_id=sent.message_id
                )

    # -- keeping pictures current ---------------------------------------------

    async def sync_pictures(self, *, target: Target | None = None) -> int:
        """Redraws every posted picture whose lessons no longer match the snapshot."""
        return sum([await self._sync_chat(t) for t in self._ctx.scope(target)])

    async def _sync_chat(self, target: Target) -> int:
        today = moscow.today()
        with connect(self._ctx.config.db_path) as conn:
            weeks = bot_store.all_of_kind(conn, chat_id=target.chat, kind="week")
            days = [
                message
                for message in bot_store.all_of_kind(conn, chat_id=target.chat, kind="today")
                if message.day >= today  # /go may have posted tomorrow's picture
            ]

        pictures = self._ctx.pictures
        updated = 0
        for message in weeks:
            updated += await self._sync(target, message, pictures.week(message.day))
        for message in days:
            updated += await self._sync(target, message, pictures.today(message.day, force=True))
        return updated

    async def _sync(
        self, target: Target, message: bot_store.PostedMessage, picture: Awaitable[Picture | None]
    ) -> int:
        current = await picture
        if current is None or current.fingerprint == message.fingerprint:
            return 0
        try:
            await errors.tolerate(
                self._ctx.bot.edit_message_media(
                    InputMediaPhoto(
                        media=BufferedInputFile(current.png, "schedule.png"),
                        caption=current.caption,
                    ),
                    chat_id=target.chat,
                    message_id=message.message_id,
                ),
                errors.NOT_MODIFIED,
            )
        except TelegramBadRequest as error:
            if not errors.says(error, errors.MESSAGE_LOST):
                raise
            # Someone deleted it in the chat. Left in the ledger it would fail every
            # retry and hold up the pictures behind it.
            logger.warning("Картинки %d уже нет в чате, убираю из учёта: %s", message.message_id, error)
            with connect(self._ctx.config.db_path) as conn:
                bot_store.forget(conn, message.id)
            return 0
        with connect(self._ctx.config.db_path) as conn:
            bot_store.set_fingerprint(conn, message.id, current.fingerprint)
        return 1
