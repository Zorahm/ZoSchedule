"""The bot's brain: what to post, when, and how to keep posts current.

Cycle (Moscow time), in every chat chosen with /go (there may be several, one schedule
for all; a failure in one chat never holds up the others):
- Sunday, `week_at`: the next week's image is posted and pinned; last week's is retired.
- Every day, `today_at`: yesterday's "today" and change texts are deleted, today's image is posted.
  With `day_ahead`, tomorrow's image instead, posted as soon as today's last lesson ends
  (at `today_at` on a day without lessons), and today's image is retired. Captioned
  "Завтра"; at midnight sync turns that into "Сегодня".
- New change events: one text message (a new message, so it pushes).
- Whenever the pictured lessons differ from the latest snapshot, the image is
  edited in place. Detected by fingerprint, not by events: the diff cannot see
  days that were merely published later.
"""

from __future__ import annotations

import datetime as dt
import html
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Protocol

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import BufferedInputFile, InputMediaPhoto, MenuButtonWebApp, WebAppInfo

from app import moscow
from app.attendance.models import JournalDay
from app.attendance.report import ReportError, file_name, report_html
from app.bot import store as bot_store
from app.bot import errors, texts
from app.bot.pictures import Picture, PictureBuilder, target_monday
from app.bot.renderer import RenderError, Renderer
from app.bot.store import Target
from app.bot.view import WEEK_DAYS
from app.config import AppConfig
from app.models.changes import ChangeEvent
from app.models.db import connect
from app.parsing.curator import RoomNotice
from app.snapshots import store
from app.snapshots.service import RefreshOutcome, RoomCorrection

logger = logging.getLogger(__name__)

_RETRY_AFTER = dt.timedelta(minutes=5)
_LOOKAHEAD_DAYS = 8
"""How far /go looks for a day that still has lessons."""


class Refresher(Protocol):
    """What the bot needs from `ScheduleService`: one parser run saved as a snapshot."""

    async def refresh(self) -> RefreshOutcome: ...


class RoomCorrector(Protocol):
    """What the bot needs to apply a curator's message: put a room on the nearest lesson."""

    async def correct_room(
        self, notice: RoomNotice, *, author_id: int | None, chat_id: str | None, text: str
    ) -> RoomCorrection: ...


class BotService:
    def __init__(
        self,
        config: AppConfig,
        bot: Bot,
        renderer: Renderer,
        schedule: Refresher | None = None,
        corrector: RoomCorrector | None = None,
    ) -> None:
        self._config = config
        self._bot = bot
        self._schedule = schedule
        self._corrector = corrector
        self._refreshed: set[str] = set()
        self._renderer = renderer
        self._pictures = PictureBuilder(config, renderer)
        self._retry_at: dict[str, dt.datetime] = {}

    @property
    def bot(self) -> Bot:
        return self._bot

    @property
    def trusted_users(self) -> frozenset[int]:
        return frozenset(self._config.bot.trusted_users)

    @property
    def curators(self) -> frozenset[int]:
        """Who may correct the schedule from a chat: the curators and the bot's owners."""
        return frozenset(self._config.bot.curators) | self.trusted_users

    @property
    def headmen(self) -> frozenset[int]:
        """Who may open the attendance journal: the headmen and the bot's owners."""
        return frozenset(self._config.bot.headmen) | self.trusted_users

    @property
    def web_url(self) -> str | None:
        return self._config.web.public_url or None

    @property
    def trusted_chats(self) -> frozenset[int]:
        return frozenset(self._config.bot.trusted_chats)

    @property
    def db_path(self) -> Path:
        return self._config.db_path

    # -- target chats ---------------------------------------------------------

    def load_targets(self) -> list[Target]:
        """The chats to post to: every one saved by /go, in the order they were added.

        The configured `chat_id` stands in only while none is saved. It is written down
        like a /go chat, because each chat keeps its own read position in the feed.
        """
        with connect(self._config.db_path) as conn:
            saved = bot_store.targets(conn)
            if not saved and self._config.bot.chat_id:
                bot_store.add_target(conn, self._config.bot.chat_id, self._config.bot.thread_id)
                saved = bot_store.targets(conn)
        return saved

    def add_target(self, chat_id: str, thread_id: int | None) -> None:
        with connect(self._config.db_path) as conn:
            bot_store.add_target(conn, chat_id, thread_id)

    def remove_target(self, chat_id: str) -> bool:
        """Stops posting to the chat. True if the bot was posting there."""
        with connect(self._config.db_path) as conn:
            return bot_store.remove_target(conn, chat_id)

    def _scope(self, target: Target | None) -> list[Target]:
        """One chat when asked for it, else all of them."""
        return self.load_targets() if target is None else [target]

    async def _photo(self, target: Target, picture: Picture) -> int:
        sent = await self._bot.send_photo(
            chat_id=target.chat,
            photo=BufferedInputFile(picture.png, "schedule.png"),
            caption=picture.caption,
            message_thread_id=target.thread,
            disable_notification=self._config.bot.silent,
        )
        return sent.message_id

    # -- attendance journal ---------------------------------------------------

    async def send_attendance_report(self, user_id: int, day: JournalDay, *, titles: bool) -> None:
        """Draws the day's full table and sends it to the headman as a file.

        A file, not a photo: Telegram squeezes a photo to 1280 px on its longer side, and a
        table of thirty rows becomes unreadable. The headman forwards it to the curator.
        """
        group = self._config.group.name
        page = report_html(group, day, titles=titles, sent_at=moscow.now())
        try:
            png = await self._renderer.render(page)
        except RenderError as error:
            logger.warning("Не удалось нарисовать таблицу посещаемости: %s", error)
            raise ReportError("Не удалось нарисовать картинку: на сервере нет браузера для неё.") from error
        absent = sum(1 for student in day.students for mark in student.marks.values() if mark == "absent")
        caption = (
            f"Посещаемость {html.escape(group)}, {texts.date_long(day.date)}. "
            f"Отсутствий (Н): {absent}."
        )
        try:
            await self._bot.send_document(
                chat_id=user_id, document=BufferedInputFile(png, file_name(day.date)), caption=caption
            )
        except TelegramForbiddenError as error:
            raise ReportError(
                "Бот не может вам написать. Откройте чат с ботом и нажмите «Старт», потом повторите.", 409
            ) from error
        except errors.TELEGRAM_ERRORS as error:
            logger.warning("Telegram не принял таблицу посещаемости: %s", error)
            raise ReportError("Telegram не принял файл. Попробуйте ещё раз через минуту.") from error

    async def pin_journal_button(self, user_id: int) -> None:
        """Makes the journal the menu button of the headman's private chat. Cosmetic: never fails."""
        if self.web_url is None:
            return
        try:
            await self._bot.set_chat_menu_button(
                chat_id=user_id,
                menu_button=MenuButtonWebApp(text="Журнал", web_app=WebAppInfo(url=self.web_url)),
            )
        except errors.TELEGRAM_ERRORS as error:
            logger.warning("Не удалось поставить кнопку журнала для %s: %s", user_id, error)

    # -- curator's corrections ------------------------------------------------

    def works_in(self, chat_id: str) -> bool:
        return any(target.chat == chat_id for target in self.load_targets())

    async def correct_room(
        self, notice: RoomNotice, *, author_id: int | None, chat_id: str, text: str
    ) -> str | None:
        """Applies a curator's room notice and tells every chat at once.

        Returns a reply for the curator when the notice could not be applied, else None.
        A notice about other groups is not ours and gets no answer.
        """
        group = self._config.group.name
        if self._corrector is None or not notice.mentions(group):
            return None
        result = await self._corrector.correct_room(
            notice, author_id=author_id, chat_id=chat_id, text=text
        )
        if result.status == "applied":
            await self._tell_chats_now()
            return None
        if result.status == "no_lesson":
            return f"Не нашёл пару в {notice.start_label} у {group} на ближайшие дни, расписание не менял."
        if result.status == "ambiguous":
            return f"В {notice.start_label} две пары: не понял, какую менять. Расписание не менял."
        return None  # unchanged: already as the curator says; no_schedule: nothing to correct

    async def _tell_chats_now(self) -> None:
        """The change text and the redrawn pictures, now rather than at the next tick."""
        try:
            await self.announce_changes()
            await self.sync_pictures()
        except errors.TELEGRAM_ERRORS as error:
            # The correction is saved and the feed position of a failed chat did not move:
            # the next tick delivers what is missing.
            logger.warning("Правка куратора сохранена, но чаты оповестить не удалось: %s", error)

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

    async def post_week(self, *, force: bool = False, target: Target | None = None) -> bool:
        return any([await self._post_week_to(t, force=force) for t in self._scope(target)])

    async def _post_week_to(self, target: Target, *, force: bool) -> bool:
        monday = target_monday(moscow.today())
        with connect(self._config.db_path) as conn:
            if bot_store.find(conn, chat_id=target.chat, kind="week", day=monday) and not force:
                return False
            previous = bot_store.all_of_kind(conn, chat_id=target.chat, kind="week")

        await self._refresh_once(f"week:{monday}")
        picture = await self._pictures.week(monday)
        if picture is None:
            logger.warning("Нет ни одного снимка: неделя не отправлена")
            return False

        message_id = await self._photo(target, picture)
        with connect(self._config.db_path) as conn:
            bot_store.record(
                conn,
                chat_id=target.chat,
                kind="week",
                day=monday,
                message_id=message_id,
                fingerprint=picture.fingerprint,
            )

        if self._config.bot.pin_week:
            try:
                await self._bot.pin_chat_message(
                    chat_id=target.chat, message_id=message_id, disable_notification=True
                )
            except errors.TELEGRAM_ERRORS as error:
                # Usually the bot lacks the "pin messages" right; the post still stands.
                logger.warning("Не удалось закрепить неделю в чате %s: %s", target.chat, error)

        for old in previous:
            await self._retire(target, old, unpin=True)
        return True

    def _day_to_post(self) -> dt.date:
        today = moscow.today()
        return today + dt.timedelta(days=1) if self._config.bot.day_ahead else today

    async def post_today(self, *, force: bool = False, target: Target | None = None) -> bool:
        """The day's picture: today's, or tomorrow's with `day_ahead`."""
        return any([await self._post_today_to(t, force=force) for t in self._scope(target)])

    async def _post_today_to(self, target: Target, *, force: bool) -> bool:
        day = self._day_to_post()
        with connect(self._config.db_path) as conn:
            if bot_store.find(conn, chat_id=target.chat, kind="today", day=day) and not force:
                return False

        await self._refresh_once(f"today:{day}")
        picture = await self._pictures.today(day, force=force)
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
        end = self._pictures.day_end(moscow.today())
        return None if end is None else moscow.now().strftime("%H:%M") >= end

    def _day_due(self) -> bool:
        """Time for the day's picture: `today_at`, or with `day_ahead` the end of today's lessons."""
        at_time = moscow.now().time() >= self._config.bot.today_at
        if not self._config.bot.day_ahead:
            return at_time
        over = self._lessons_over()
        # While today's lessons are on, the chat needs today's picture, not tomorrow's.
        return at_time if over is None else over

    async def _post_today_when_due(self, target: Target) -> None:
        if self._day_due():
            await self.post_today(target=target)

    async def retire_finished_days(self, *, target: Target | None = None) -> int:
        return sum([await self._retire_finished_days_in(t) for t in self._scope(target)])

    async def _retire_finished_days_in(self, target: Target) -> int:
        """With `day_ahead`: today's picture goes once its lessons are over and tomorrow's is up.

        Not without the next picture: on the eve of a day off the finished day stays.
        """
        if not self._config.bot.day_ahead:
            return 0
        today = moscow.today()
        with connect(self._config.db_path) as conn:
            posted = bot_store.all_of_kind(conn, chat_id=target.chat, kind="today")
        if not any(message.day > today for message in posted):
            return 0
        today_over = self._lessons_over() is not False
        stale = [m for m in posted if m.day < today or (m.day == today and today_over)]
        for message in stale:
            await self._retire(target, message)
        return len(stale)

    async def _post_day(self, target: Target, day: dt.date, picture: Picture) -> None:
        message_id = await self._photo(target, picture)
        with connect(self._config.db_path) as conn:
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
        days = [await self._post_next_day_to(t) for t in self._scope(target)]
        return next((day for day in days if day is not None), None)

    async def _post_next_day_to(self, target: Target) -> dt.date | None:
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
                    for message in bot_store.all_of_kind(conn, chat_id=target.chat, kind="today")
                    if message.day >= today
                ]
            await self._post_day(target, day, picture)
            for message in older:
                await self._retire(target, message)
            return day
        return None

    async def go(self, chat_id: str, thread_id: int | None) -> str | None:
        """/go: add this chat, post the week there now, then the nearest day. None on success.

        Other chats are left as they are: /go in a second group adds it, it does not move
        the bot.
        """
        self.add_target(chat_id, thread_id)
        target = Target(chat_id, thread_id)
        today = moscow.today()
        await self.refresh(force=True)
        self._refreshed.add(f"week:{target_monday(today)}")  # just refreshed

        posted_week = await self.post_week(force=True, target=target)
        day = await self.post_next_day(target)
        if not posted_week and day is None:
            return "Пока нечего показывать: в базе нет расписания."
        return None

    async def _retire(
        self, target: Target, message: bot_store.PostedMessage, *, unpin: bool = False
    ) -> None:
        """Deletes a message and forgets it. Keeps the record only if Telegram was unreachable."""
        try:
            if unpin:
                await errors.tolerate(
                    self._bot.unpin_chat_message(
                        chat_id=target.chat, message_id=message.message_id
                    ),
                    errors.NOT_PINNED,
                )
            await errors.tolerate(
                self._bot.delete_message(chat_id=target.chat, message_id=message.message_id),
                errors.ALREADY_GONE,
            )
        except errors.TELEGRAM_ERRORS as error:
            if errors.is_transient(error):
                logger.warning("Telegram недоступен, удалю позже: %s", error)
                return
            logger.warning("Не удалось удалить сообщение %d: %s", message.message_id, error)
        with connect(self._config.db_path) as conn:
            bot_store.forget(conn, message.id)

    async def cleanup(self, today: dt.date, *, target: Target | None = None) -> None:
        """Yesterday's "today" pictures and change texts go away each morning."""
        for chat in self._scope(target):
            with connect(self._config.db_path) as conn:
                kinds: tuple[bot_store.Kind, ...] = ("today", "changes")
                stale = [
                    message
                    for kind in kinds
                    for message in bot_store.all_of_kind(conn, chat_id=chat.chat, kind=kind)
                    if message.day < today
                ]
            for message in stale:
                await self._retire(chat, message)

    # -- changes --------------------------------------------------------------

    async def announce_changes(self, *, target: Target | None = None) -> int:
        """Sends new change events as text. Returns how many were announced, over all chats."""
        return sum([await self._announce_to(t) for t in self._scope(target)])

    async def _announce_to(self, target: Target) -> int:
        today = moscow.today()
        with connect(self._config.db_path) as conn:
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
        with connect(self._config.db_path) as conn:
            bot_store.set_last_event_id(conn, target.chat, max(event.id for event in events))
        return len(relevant)

    async def _send_changes(self, target: Target, events: list[ChangeEvent], today: dt.date) -> None:
        for text in texts.format_changes(events):
            sent = await self._bot.send_message(
                chat_id=target.chat,
                text=text,
                message_thread_id=target.thread,
                disable_notification=self._config.bot.silent,
            )
            with connect(self._config.db_path) as conn:
                bot_store.record(
                    conn, chat_id=target.chat, kind="changes", day=today, message_id=sent.message_id
                )

    # -- keeping pictures current ---------------------------------------------

    async def sync_pictures(self, *, target: Target | None = None) -> int:
        """Redraws every posted picture whose lessons no longer match the snapshot."""
        return sum([await self._sync_chat(t) for t in self._scope(target)])

    async def _sync_chat(self, target: Target) -> int:
        today = moscow.today()
        with connect(self._config.db_path) as conn:
            weeks = bot_store.all_of_kind(conn, chat_id=target.chat, kind="week")
            days = [
                message
                for message in bot_store.all_of_kind(conn, chat_id=target.chat, kind="today")
                if message.day >= today  # /go may have posted tomorrow's picture
            ]

        updated = 0
        for message in weeks:
            updated += await self._sync(target, message, self._pictures.week(message.day))
        for message in days:
            updated += await self._sync(
                target, message, self._pictures.today(message.day, force=True)
            )
        return updated

    async def _sync(
        self, target: Target, message: bot_store.PostedMessage, picture: Awaitable[Picture | None]
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
        targets = self.load_targets()
        if not targets:
            return  # nobody has said /go yet: nothing to post and nowhere to post it
        now = moscow.now()

        await self._guarded("refresh", self.refresh)
        for target in targets:
            # Each chat has its own jobs and its own retry pause: a chat that rejects the
            # bot (kicked, wrong id) must not hold up the others.
            await self._tick_chat(target, now)

    async def _tick_chat(self, target: Target, now: dt.datetime) -> None:
        bot = self._config.bot
        chat = target.chat

        if now.time() >= bot.today_at:
            await self._guarded(f"cleanup:{chat}", lambda: self.cleanup(now.date(), target=target))
        await self._guarded(f"today:{chat}", lambda: self._post_today_when_due(target))
        await self._guarded(f"finished:{chat}", lambda: self.retire_finished_days(target=target))
        if now.weekday() == 6 and now.time() >= bot.week_at:
            await self._guarded(f"week:{chat}", lambda: self.post_week(target=target))
        await self._guarded(f"changes:{chat}", lambda: self.announce_changes(target=target))
        await self._guarded(f"sync:{chat}", lambda: self.sync_pictures(target=target))
