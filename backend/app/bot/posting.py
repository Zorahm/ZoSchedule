"""Posting the pictures: the week, the day, and taking the finished ones down."""

from __future__ import annotations

import datetime as dt
import logging

from app import moscow
from app.bot import errors
from app.bot import store as bot_store
from app.bot.context import BotContext
from app.bot.refresh import SnapshotRefresher
from app.bot.store import Target
from app.db import connect
from app.render.pictures import Picture, target_monday

logger = logging.getLogger(__name__)

_LOOKAHEAD_DAYS = 8
"""How far /go looks for a day that still has lessons."""


class Poster:
    def __init__(self, ctx: BotContext, refresher: SnapshotRefresher) -> None:
        self._ctx = ctx
        self._refresher = refresher

    # -- week -----------------------------------------------------------------

    async def post_week(self, *, force: bool = False, target: Target | None = None) -> bool:
        return any([await self._post_week_to(t, force=force) for t in self._ctx.scope(target)])

    async def _post_week_to(self, target: Target, *, force: bool) -> bool:
        ctx = self._ctx
        monday = target_monday(moscow.today())
        with connect(ctx.config.db_path) as conn:
            if bot_store.find(conn, chat_id=target.chat, kind="week", day=monday) and not force:
                return False
            previous = bot_store.all_of_kind(conn, chat_id=target.chat, kind="week")

        await self._refresher.refresh_once(f"week:{monday}")
        picture = await ctx.pictures.week(monday)
        if picture is None:
            logger.warning("Нет ни одного снимка: неделя не отправлена")
            return False

        message_id = await ctx.send_photo(target, picture)
        with connect(ctx.config.db_path) as conn:
            bot_store.record(
                conn,
                chat_id=target.chat,
                kind="week",
                day=monday,
                message_id=message_id,
                fingerprint=picture.fingerprint,
            )

        if ctx.config.bot.pin_week:
            try:
                await ctx.bot.pin_chat_message(
                    chat_id=target.chat, message_id=message_id, disable_notification=True
                )
            except errors.TELEGRAM_ERRORS as error:
                # Usually the bot lacks the "pin messages" right; the post still stands.
                logger.warning("Не удалось закрепить неделю в чате %s: %s", target.chat, error)

        for old in previous:
            await ctx.retire(target, old, unpin=True)
        return True

    # -- day ------------------------------------------------------------------

    def _day_to_post(self) -> dt.date:
        today = moscow.today()
        return today + dt.timedelta(days=1) if self._ctx.config.bot.day_ahead else today

    async def post_today(self, *, force: bool = False, target: Target | None = None) -> bool:
        """The day's picture: today's, or tomorrow's with `day_ahead`."""
        return any([await self._post_today_to(t, force=force) for t in self._ctx.scope(target)])

    async def _post_today_to(self, target: Target, *, force: bool) -> bool:
        day = self._day_to_post()
        with connect(self._ctx.config.db_path) as conn:
            if bot_store.find(conn, chat_id=target.chat, kind="today", day=day) and not force:
                return False

        await self._refresher.refresh_once(f"today:{day}")
        picture = await self._ctx.pictures.today(day, force=force)
        if picture is None:
            return False
        if not force and day == moscow.today() and self._day_is_over(picture):
            # A restart in the evening, or a /go that already chose tomorrow: a picture
            # of a finished day is noise.
            return False
        await self._post_day(target, day, picture)
        await self._retire_finished_days_in(target)
        return True

    @staticmethod
    def _day_is_over(picture: Picture) -> bool:
        return picture.last_end is not None and moscow.now().strftime("%H:%M") >= picture.last_end

    def _lessons_over(self) -> bool | None:
        """Whether today's last lesson has ended; None when today has no lessons."""
        end = self._ctx.pictures.day_end(moscow.today())
        return None if end is None else moscow.now().strftime("%H:%M") >= end

    def _day_due(self) -> bool:
        """Time for the day's picture: `today_at`, or with `day_ahead` the end of today's lessons."""
        bot = self._ctx.config.bot
        at_time = moscow.now().time() >= bot.today_at
        if not bot.day_ahead:
            return at_time
        over = self._lessons_over()
        # While today's lessons are on, the chat needs today's picture, not tomorrow's.
        return at_time if over is None else over

    async def post_today_when_due(self, target: Target) -> None:
        if self._day_due():
            await self.post_today(target=target)

    async def _post_day(self, target: Target, day: dt.date, picture: Picture) -> None:
        message_id = await self._ctx.send_photo(target, picture)
        with connect(self._ctx.config.db_path) as conn:
            bot_store.record(
                conn,
                chat_id=target.chat,
                kind="today",
                day=day,
                message_id=message_id,
                fingerprint=picture.fingerprint,
            )

    async def post_next_day(self, target: Target | None = None) -> dt.date | None:
        """Today's picture if lessons remain, else the next day that has any.

        For /go, which can be written at any hour: at 21:00 today's picture is
        useless, tomorrow's is not. Whatever "day" pictures were up are replaced.
        """
        days = [await self._post_next_day_to(t) for t in self._ctx.scope(target)]
        return next((day for day in days if day is not None), None)

    async def _post_next_day_to(self, target: Target) -> dt.date | None:
        ctx = self._ctx
        today = moscow.now().date()
        for offset in range(_LOOKAHEAD_DAYS):
            day = today + dt.timedelta(days=offset)
            picture = await ctx.pictures.today(day)
            if picture is None:
                continue
            if offset == 0 and self._day_is_over(picture):
                continue
            with connect(ctx.config.db_path) as conn:
                older = [
                    message
                    for message in bot_store.all_of_kind(conn, chat_id=target.chat, kind="today")
                    if message.day >= today
                ]
            await self._post_day(target, day, picture)
            for message in older:
                await ctx.retire(target, message)
            return day
        return None

    # -- taking down ----------------------------------------------------------

    async def retire_finished_days(self, *, target: Target | None = None) -> int:
        return sum([await self._retire_finished_days_in(t) for t in self._ctx.scope(target)])

    async def _retire_finished_days_in(self, target: Target) -> int:
        """With `day_ahead`: today's picture goes once its lessons are over and tomorrow's is up.

        Not without the next picture: on the eve of a day off the finished day stays.
        """
        if not self._ctx.config.bot.day_ahead:
            return 0
        today = moscow.today()
        with connect(self._ctx.config.db_path) as conn:
            posted = bot_store.all_of_kind(conn, chat_id=target.chat, kind="today")
        if not any(message.day > today for message in posted):
            return 0
        today_over = self._lessons_over() is not False
        stale = [m for m in posted if m.day < today or (m.day == today and today_over)]
        for message in stale:
            await self._ctx.retire(target, message)
        return len(stale)

    async def cleanup(self, today: dt.date, *, target: Target | None = None) -> None:
        """Yesterday's "today" pictures and change texts go away each morning."""
        for chat in self._ctx.scope(target):
            with connect(self._ctx.config.db_path) as conn:
                kinds: tuple[bot_store.Kind, ...] = ("today", "changes")
                stale = [
                    message
                    for kind in kinds
                    for message in bot_store.all_of_kind(conn, chat_id=chat.chat, kind=kind)
                    if message.day < today
                ]
            for message in stale:
                await self._ctx.retire(chat, message)
