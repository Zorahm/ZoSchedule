"""The bot's brain: what to post, when, and how to keep posts current.

Cycle (Moscow time), once a chat has been chosen with /go:
- Sunday, `week_at`: the next week's image is posted and pinned; last week's is retired.
- Every day, `today_at`: yesterday's "today" and change texts are deleted, today's image is posted.
  With `day_ahead`, tomorrow's image instead, captioned "Завтра"; at midnight sync turns
  that into "Сегодня". Today's image stays until its last lesson ends and tomorrow's is up.
- New change events: one text message (a new message, so it pushes).
- Whenever the pictured lessons differ from the latest snapshot, the image is
  edited in place. Detected by fingerprint, not by events: the diff cannot see
  days that were merely published later.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Protocol

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BufferedInputFile, InputMediaPhoto

from app import moscow
from app.bot import store as bot_store
from app.bot import errors, texts
from app.bot.pictures import Picture, PictureBuilder, target_monday
from app.bot.renderer import Renderer
from app.bot.view import WEEK_DAYS
from app.config import AppConfig
from app.models.changes import ChangeEvent
from app.models.db import connect
from app.snapshots import store
from app.snapshots.service import RefreshOutcome

logger = logging.getLogger(__name__)

_RETRY_AFTER = dt.timedelta(minutes=5)
_LOOKAHEAD_DAYS = 8
"""How far /go looks for a day that still has lessons."""


class Refresher(Protocol):
    """What the bot needs from `ScheduleService`: one parser run saved as a snapshot."""

    async def refresh(self) -> RefreshOutcome: ...


class BotService:
    def __init__(
        self,
        config: AppConfig,
        bot: Bot,
        renderer: Renderer,
        schedule: Refresher | None = None,
    ) -> None:
        self._config = config
        self._bot = bot
        self._schedule = schedule
        self._refreshed: set[str] = set()
        self._pictures = PictureBuilder(config, renderer)
        self._chat = ""
        self._thread: int | None = None
        self._retry_at: dict[str, dt.datetime] = {}
        self.bind_target()

    @property
    def bot(self) -> Bot:
        return self._bot

    @property
    def trusted_users(self) -> frozenset[int]:
        return frozenset(self._config.bot.trusted_users)

    @property
    def db_path(self) -> Path:
        return self._config.db_path

    # -- target chat ----------------------------------------------------------

    def bind_target(self) -> bool:
        """Loads the chat to post to: the one from /go, else the configured fallback."""
        with connect(self._config.db_path) as conn:
            saved = bot_store.target(conn)
        if saved is not None:
            self._chat, self._thread = saved
        elif self._config.bot.chat_id:
            self._chat, self._thread = self._config.bot.chat_id, self._config.bot.thread_id
        else:
            return False
        return True

    def set_target(self, chat_id: str, thread_id: int | None) -> None:
        with connect(self._config.db_path) as conn:
            bot_store.set_target(conn, chat_id, thread_id)
        self.bind_target()

    async def _photo(self, picture: Picture) -> int:
        sent = await self._bot.send_photo(
            chat_id=self._chat,
            photo=BufferedInputFile(picture.png, "schedule.png"),
            caption=picture.caption,
            message_thread_id=self._thread,
            disable_notification=self._config.bot.silent,
        )
        return sent.message_id

    # -- snapshots ------------------------------------------------------------

    async def refresh(self, *, force: bool = False) -> None:
        """Takes a fresh snapshot when the last attempt is older than the poll interval.

        Once per `poll.interval_minutes`. A failed run still counts as an
        attempt: a site that is down is not hammered every minute.
        """
        if self._schedule is None or not self._config.bot.refresh:
            return
        if not force:
            interval = dt.timedelta(minutes=self._config.poll.interval_minutes)
            with connect(self._config.db_path) as conn:
                attempt = store.last_attempt(conn)
            if attempt is not None and moscow.now() - attempt.taken_at < interval:
                return
        outcome = await self._schedule.refresh()
        if outcome.ok:
            logger.info("Снимок обновлён ботом, изменений: %d", outcome.changes_detected)
        else:
            # Post from the last good snapshot rather than stay silent.
            logger.warning("Бот не смог обновить снимок: %s", outcome.error)

    async def _refresh_once(self, key: str) -> None:
        """A forced refresh, once per key: right before the day's or the week's first post."""
        if key in self._refreshed:
            return
        await self.refresh(force=True)
        self._refreshed.add(key)

    # -- posting --------------------------------------------------------------

    async def post_week(self, *, force: bool = False) -> bool:
        monday = target_monday(moscow.today())
        with connect(self._config.db_path) as conn:
            if bot_store.find(conn, chat_id=self._chat, kind="week", day=monday) and not force:
                return False
            previous = bot_store.all_of_kind(conn, chat_id=self._chat, kind="week")

        await self._refresh_once(f"week:{monday}")
        picture = await self._pictures.week(monday)
        if picture is None:
            logger.warning("Нет ни одного снимка: неделя не отправлена")
            return False

        message_id = await self._photo(picture)
        with connect(self._config.db_path) as conn:
            bot_store.record(
                conn,
                chat_id=self._chat,
                kind="week",
                day=monday,
                message_id=message_id,
                fingerprint=picture.fingerprint,
            )

        if self._config.bot.pin_week:
            try:
                await self._bot.pin_chat_message(
                    chat_id=self._chat, message_id=message_id, disable_notification=True
                )
            except errors.TELEGRAM_ERRORS as error:
                # Usually the bot lacks the "pin messages" right; the post still stands.
                logger.warning("Не удалось закрепить неделю: %s", error)

        for old in previous:
            await self._retire(old, unpin=True)
        return True

    def _day_to_post(self) -> dt.date:
        today = moscow.today()
        return today + dt.timedelta(days=1) if self._config.bot.day_ahead else today

    async def post_today(self, *, force: bool = False) -> bool:
        """The day's picture: today's, or tomorrow's with `day_ahead`."""
        day = self._day_to_post()
        with connect(self._config.db_path) as conn:
            if bot_store.find(conn, chat_id=self._chat, kind="today", day=day) and not force:
                return False

        await self._refresh_once(f"today:{day}")
        picture = await self._pictures.today(day, force=force)
        if picture is None:
            return False
        if not force and day == moscow.today() and self._day_is_over(picture):
            # A restart in the evening, or a /go that already chose tomorrow: a picture
            # of a finished day is noise.
            return False
        await self._post_day(day, picture)
        await self.retire_finished_days()
        return True

    @staticmethod
    def _day_is_over(picture: Picture) -> bool:
        return picture.last_end is not None and moscow.now().strftime("%H:%M") >= picture.last_end

    async def retire_finished_days(self) -> int:
        """With `day_ahead`: today's picture goes once its lessons are over and tomorrow's is up.

        Not earlier: with a morning `today_at` the chat needs today's lessons all day long.
        Not without the next picture either: an evening `today_at` would leave a gap.
        """
        if not self._config.bot.day_ahead:
            return 0
        today = moscow.today()
        with connect(self._config.db_path) as conn:
            posted = bot_store.all_of_kind(conn, chat_id=self._chat, kind="today")
        if not any(message.day > today for message in posted):
            return 0
        end = self._pictures.day_end(today)
        today_over = end is None or moscow.now().strftime("%H:%M") >= end
        stale = [m for m in posted if m.day < today or (m.day == today and today_over)]
        for message in stale:
            await self._retire(message)
        return len(stale)

    async def _post_day(self, day: dt.date, picture: Picture) -> None:
        message_id = await self._photo(picture)
        with connect(self._config.db_path) as conn:
            bot_store.record(
                conn,
                chat_id=self._chat,
                kind="today",
                day=day,
                message_id=message_id,
                fingerprint=picture.fingerprint,
            )

    async def post_next_day(self) -> dt.date | None:
        """Today's picture if lessons remain, else the next day that has any.

        For /go, which can be written at any hour: at 21:00 today's picture is
        useless, tomorrow's is not. Whatever "day" pictures were up are replaced.
        """
        now = moscow.now()
        today = now.date()
        for offset in range(_LOOKAHEAD_DAYS):
            day = today + dt.timedelta(days=offset)
            picture = await self._pictures.today(day)
            if picture is None:
                continue
            if offset == 0 and self._day_is_over(picture):
                continue
            with connect(self._config.db_path) as conn:
                older = [
                    message
                    for message in bot_store.all_of_kind(conn, chat_id=self._chat, kind="today")
                    if message.day >= today
                ]
            await self._post_day(day, picture)
            for message in older:
                await self._retire(message)
            return day
        return None

    async def go(self, chat_id: str, thread_id: int | None) -> str | None:
        """/go: adopt this chat, post the week now, then the nearest day. None on success."""
        self.set_target(chat_id, thread_id)
        today = moscow.today()
        await self.refresh(force=True)
        self._refreshed.add(f"week:{target_monday(today)}")  # just refreshed

        posted_week = await self.post_week(force=True)
        day = await self.post_next_day()
        if not posted_week and day is None:
            return "Пока нечего показывать: в базе нет расписания."
        return None

    async def _retire(self, message: bot_store.PostedMessage, *, unpin: bool = False) -> None:
        """Deletes a message and forgets it. Keeps the record only if Telegram was unreachable."""
        try:
            if unpin:
                await errors.tolerate(
                    self._bot.unpin_chat_message(
                        chat_id=self._chat, message_id=message.message_id
                    ),
                    errors.NOT_PINNED,
                )
            await errors.tolerate(
                self._bot.delete_message(chat_id=self._chat, message_id=message.message_id),
                errors.ALREADY_GONE,
            )
        except errors.TELEGRAM_ERRORS as error:
            if errors.is_transient(error):
                logger.warning("Telegram недоступен, удалю позже: %s", error)
                return
            logger.warning("Не удалось удалить сообщение %d: %s", message.message_id, error)
        with connect(self._config.db_path) as conn:
            bot_store.forget(conn, message.id)

    async def cleanup(self, today: dt.date) -> None:
        """Yesterday's "today" pictures and change texts go away each morning."""
        with connect(self._config.db_path) as conn:
            kinds: tuple[bot_store.Kind, ...] = ("today", "changes")
            stale = [
                message
                for kind in kinds
                for message in bot_store.all_of_kind(conn, chat_id=self._chat, kind=kind)
                if message.day < today
            ]
        for message in stale:
            await self._retire(message)

    # -- changes --------------------------------------------------------------

    async def announce_changes(self) -> int:
        """Sends new change events as text. Returns how many were announced."""
        today = moscow.today()
        with connect(self._config.db_path) as conn:
            last = bot_store.last_event_id(conn)
            if last is None:
                # First run: what is already in the feed is history, not news.
                bot_store.set_last_event_id(conn, store.max_event_id(conn))
                return 0
            events = store.events_after(conn, last)
        if not events:
            return 0

        # Only the week the chat is looking at. A change for a lesson months away is
        # noise; it reaches people through that week's picture when its turn comes.
        week_end = target_monday(today) + dt.timedelta(days=WEEK_DAYS - 1)
        relevant = [event for event in events if today <= event.date <= week_end]
        await self._send_changes(relevant, today)
        with connect(self._config.db_path) as conn:
            bot_store.set_last_event_id(conn, max(event.id for event in events))
        return len(relevant)

    async def _send_changes(self, events: list[ChangeEvent], today: dt.date) -> None:
        for text in texts.format_changes(events):
            sent = await self._bot.send_message(
                chat_id=self._chat,
                text=text,
                message_thread_id=self._thread,
                disable_notification=self._config.bot.silent,
            )
            with connect(self._config.db_path) as conn:
                bot_store.record(
                    conn, chat_id=self._chat, kind="changes", day=today, message_id=sent.message_id
                )

    # -- keeping pictures current ---------------------------------------------

    async def sync_pictures(self) -> int:
        """Redraws every posted picture whose lessons no longer match the snapshot."""
        today = moscow.today()
        with connect(self._config.db_path) as conn:
            weeks = bot_store.all_of_kind(conn, chat_id=self._chat, kind="week")
            days = [
                message
                for message in bot_store.all_of_kind(conn, chat_id=self._chat, kind="today")
                if message.day >= today  # /go may have posted tomorrow's picture
            ]

        updated = 0
        for message in weeks:
            updated += await self._sync(message, self._pictures.week(message.day))
        for message in days:
            updated += await self._sync(message, self._pictures.today(message.day, force=True))
        return updated

    async def _sync(
        self, message: bot_store.PostedMessage, picture: Awaitable[Picture | None]
    ) -> int:
        current = await picture
        if current is None or current.fingerprint == message.fingerprint:
            return 0
        try:
            await errors.tolerate(
                self._bot.edit_message_media(
                    InputMediaPhoto(
                        media=BufferedInputFile(current.png, "schedule.png"),
                        caption=current.caption,
                    ),
                    chat_id=self._chat,
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
            with connect(self._config.db_path) as conn:
                bot_store.forget(conn, message.id)
            return 0
        with connect(self._config.db_path) as conn:
            bot_store.set_fingerprint(conn, message.id, current.fingerprint)
        return 1

    # -- loop -----------------------------------------------------------------

    async def _guarded(self, name: str, job: Callable[[], Awaitable[object]]) -> None:
        """Runs a job; on failure waits before retrying so a bad chat id cannot spam logs."""
        now = moscow.now()
        if self._retry_at.get(name, now) > now:
            return
        try:
            await job()
        except Exception:
            # The loop must survive any single job failing: the next tick may succeed.
            logger.exception("Сбой задачи бота %r, повтор через %s", name, _RETRY_AFTER)
            self._retry_at[name] = now + _RETRY_AFTER
        else:
            self._retry_at.pop(name, None)

    async def tick(self) -> None:
        if not self.bind_target():
            return  # nobody has said /go yet: nothing to post and nowhere to post it
        now = moscow.now()
        bot = self._config.bot

        await self._guarded("refresh", self.refresh)
        if now.time() >= bot.today_at:
            await self._guarded("cleanup", lambda: self.cleanup(now.date()))
            await self._guarded("today", self.post_today)
        await self._guarded("finished", self.retire_finished_days)
        if now.weekday() == 6 and now.time() >= bot.week_at:
            await self._guarded("week", self.post_week)
        await self._guarded("changes", self.announce_changes)
        await self._guarded("sync", self.sync_pictures)
