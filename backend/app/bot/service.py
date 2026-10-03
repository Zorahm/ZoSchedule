"""The bot's brain: what to post, when, and how to keep posts current.

`BotService` is the one object the handlers, the runner and the CLI talk to; the work
itself is in `posting` (pictures), `updates` (change texts, redrawn pictures), `refresh`
(snapshots) and `reports` (attendance journal), all sharing a `BotContext`.

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
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Protocol

from aiogram import Bot

from app import moscow
from app.attendance.models import JournalDay
from app.bot import errors
from app.bot.context import BotContext
from app.bot.posting import Poster
from app.bot.refresh import Refresher, SnapshotRefresher
from app.bot.reports import ReportSender
from app.bot.store import Target
from app.bot.updates import Updater
from app.config import AppConfig
from app.parsing.curator import RoomNotice
from app.render.pictures import PictureBuilder, target_monday
from app.render.renderer import Renderer
from app.snapshots.service import RoomCorrection

logger = logging.getLogger(__name__)

_RETRY_AFTER = dt.timedelta(minutes=5)


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
        self._corrector = corrector
        self._ctx = BotContext(config, bot, renderer)
        self._refresher = SnapshotRefresher(config, schedule)
        self._poster = Poster(self._ctx, self._refresher)
        self._updater = Updater(self._ctx)
        self._reports = ReportSender(self._ctx)
        self._retry_at: dict[str, dt.datetime] = {}

    # -- who and what ---------------------------------------------------------

    @property
    def bot(self) -> Bot:
        return self._ctx.bot

    @property
    def pictures(self) -> PictureBuilder:
        return self._ctx.pictures

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
        return self._ctx.load_targets()

    def add_target(self, chat_id: str, thread_id: int | None) -> None:
        self._ctx.add_target(chat_id, thread_id)

    def remove_target(self, chat_id: str) -> bool:
        return self._ctx.remove_target(chat_id)

    def works_in(self, chat_id: str) -> bool:
        return any(target.chat == chat_id for target in self.load_targets())

    # -- attendance journal ---------------------------------------------------

    async def send_attendance_report(self, user_id: int, day: JournalDay, *, titles: bool) -> None:
        await self._reports.send_attendance_report(user_id, day, titles=titles)

    async def pin_journal_button(self, user_id: int) -> None:
        await self._reports.pin_journal_button(user_id, self.web_url)

    # -- curator's corrections ------------------------------------------------

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

    # -- what the bot does ----------------------------------------------------

    async def refresh(self, *, force: bool = False) -> None:
        await self._refresher.refresh(force=force)

    async def post_week(self, *, force: bool = False, target: Target | None = None) -> bool:
        return await self._poster.post_week(force=force, target=target)

    async def post_today(self, *, force: bool = False, target: Target | None = None) -> bool:
        return await self._poster.post_today(force=force, target=target)

    async def post_next_day(self, target: Target | None = None) -> dt.date | None:
        return await self._poster.post_next_day(target)

    async def retire_finished_days(self, *, target: Target | None = None) -> int:
        return await self._poster.retire_finished_days(target=target)

    async def cleanup(self, today: dt.date, *, target: Target | None = None) -> None:
        await self._poster.cleanup(today, target=target)

    async def announce_changes(self, *, target: Target | None = None) -> int:
        return await self._updater.announce_changes(target=target)

    async def sync_pictures(self, *, target: Target | None = None) -> int:
        return await self._updater.sync_pictures(target=target)

    async def go(self, chat_id: str, thread_id: int | None) -> str | None:
        """/go: add this chat, post the week there now, then the nearest day. None on success.

        Other chats are left as they are: /go in a second group adds it, it does not move
        the bot.
        """
        self.add_target(chat_id, thread_id)
        target = Target(chat_id, thread_id)
        await self.refresh(force=True)
        self._refresher.mark_fresh(f"week:{target_monday(moscow.today())}")  # just refreshed

        posted_week = await self.post_week(force=True, target=target)
        day = await self.post_next_day(target)
        if not posted_week and day is None:
            return "Пока нечего показывать: в базе нет расписания."
        return None

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
        await self._guarded(f"today:{chat}", lambda: self._poster.post_today_when_due(target))
        await self._guarded(f"finished:{chat}", lambda: self.retire_finished_days(target=target))
        if now.weekday() == 6 and now.time() >= bot.week_at:
            await self._guarded(f"week:{chat}", lambda: self.post_week(target=target))
        await self._guarded(f"changes:{chat}", lambda: self.announce_changes(target=target))
        await self._guarded(f"sync:{chat}", lambda: self.sync_pictures(target=target))
