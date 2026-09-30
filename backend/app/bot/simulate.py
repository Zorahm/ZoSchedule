"""A week of the bot's life in a minute, against a scratch database and a real test chat.

The clock is faked (`moscow.now`), Telegram is real: in the chat you watch the
week get posted and pinned, the day picture appear, a change text arrive, the
pictures being edited, and yesterday's messages and last week's picture vanish
when the next day and the next week come.

For a scratch database only: the CLI refuses to run this against the real one.
With `real_source` the lessons come from a copy of the real database instead of the
demo schedule; the original is only read.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import sqlite3
from collections.abc import Awaitable, Callable
from pathlib import Path

from aiogram import Bot
from aiogram.client.session.middlewares.base import BaseRequestMiddleware, NextRequestMiddlewareType
from aiogram.methods import Response, TelegramMethod
from aiogram.methods.base import TelegramType

from app import moscow
from app.bot import demo, errors
from app.bot import store as bot_store
from app.bot.renderer import PlaywrightRenderer
from app.bot.runner import make_bot
from app.bot.service import BotService
from app.config import AppConfig
from app.models.db import connect
from app.snapshots import store
from app.snapshots.service import ScheduleService


class _Trace(BaseRequestMiddleware):
    """Prints every Telegram call, so the run can be read against the chat."""

    async def __call__(
        self,
        make_request: NextRequestMiddlewareType[TelegramType],
        bot: Bot,
        method: TelegramMethod[TelegramType],
    ) -> Response[TelegramType]:
        name = type(method).__name__
        try:
            result = await make_request(bot, method)
        except errors.TELEGRAM_ERRORS as error:
            # The service swallows harmless answers (already deleted, not modified);
            # a trace that hid them would hide what Telegram really said.
            print(f"      Telegram: {name} -> {error}")
            raise
        # A new message reports its own id; edits and deletes name the one they touch.
        ident = getattr(result, "message_id", None) or getattr(method, "message_id", None)
        print(f"      Telegram: {name}" + (f" #{ident}" if ident else ""))
        return result


class _Clock:
    def __init__(self) -> None:
        self.value = moscow.now()

    @staticmethod
    def at(day: dt.date, clock: str) -> dt.datetime:
        hours, minutes = clock.split(":")
        return dt.datetime(
            day.year, day.month, day.day, int(hours), int(minutes), tzinfo=moscow.MOSCOW
        )

    def set(self, day: dt.date, clock: str) -> None:
        self.value = self.at(day, clock)

    def __call__(self) -> dt.datetime:
        return self.value


def _show_ledger(config: AppConfig) -> None:
    with connect(config.db_path) as conn:
        chat = bot_store.target(conn)
        rows = (
            [
                (kind, message.day.isoformat(), message.message_id)
                for kind in ("week", "today", "changes")
                for message in bot_store.all_of_kind(conn, chat_id=chat[0], kind=kind)
            ]
            if chat
            else []
        )
    listing = ", ".join(f"{kind} {day} #{mid}" for kind, day, mid in rows) or "пусто"
    print(f"      у бота на учёте: {listing}")


def copy_real_database(source: Path, dest: Path, *, before: dt.datetime) -> None:
    """Fills the scratch `dest` from the real database, ready for a simulated week.

    `source` is opened read-only. In the copy the bot's own state is wiped (it starts
    as a fresh bot, in the test chat) and every timestamp moves back by one common
    shift, so the newest snapshot lies just before `before`: the simulation lives on
    a made-up timeline, and a snapshot from its "future" would hide every change.
    """
    origin = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    target = sqlite3.connect(dest)
    try:
        origin.backup(target)
        target.execute("DELETE FROM bot_messages")
        target.execute("DELETE FROM bot_state")
        newest = target.execute("SELECT MAX(taken_at) FROM snapshots").fetchone()[0]
        if newest is not None:
            shift = dt.datetime.fromisoformat(newest) - (before - dt.timedelta(hours=1))
            if shift > dt.timedelta(0):
                _shift_column(target, "snapshots", "id", "taken_at", shift)
                _shift_column(target, "change_events", "id", "detected_at", shift)
        target.commit()
    finally:
        origin.close()
        target.close()


def _shift_column(
    conn: sqlite3.Connection, table: str, key: str, column: str, shift: dt.timedelta
) -> None:
    rows = conn.execute(f"SELECT {key}, {column} FROM {table}").fetchall()
    for row_key, value in rows:
        moved = (dt.datetime.fromisoformat(value) - shift).isoformat()
        conn.execute(f"UPDATE {table} SET {column} = ? WHERE {key} = ?", (moved, row_key))


async def run(config: AppConfig, *, pause: float, real_source: Path | None = None) -> None:
    """Drives one BotService through a week. `config.bot.chat_id` names the test chat."""
    real_today = moscow.today()
    monday = real_today - dt.timedelta(days=real_today.weekday())
    sunday_before = monday - dt.timedelta(days=1)
    if real_source is not None:
        copy_real_database(real_source, config.db_path, before=_Clock.at(sunday_before, "06:00"))
    schedule = ScheduleService(config)
    schedule.prepare()

    telegram = make_bot(config.bot.token.get_secret_value(), proxy=config.bot.proxy_url)
    telegram.session.middleware(_Trace())
    # No `schedule` here: the bot must not poll the real site and replace the demo data.
    bot = BotService(config, telegram, PlaywrightRenderer(config.bot.browser_path))
    bot.set_target(config.bot.chat_id, config.bot.thread_id)

    clock = _Clock()
    original = moscow.now
    moscow.now = clock  # a dev tool: the one place that fakes time
    # The demo snapshot is taken on the fake timeline too: the bot trusts snapshot
    # timestamps, and one from the "future" would hide every later change.
    clock.set(sunday_before, "06:00")
    if real_source is None:
        schedule.store_lessons(demo.build_schedule(real_today))

    async def step(
        title: str, day: dt.date, at: str, action: Callable[[], Awaitable[object]]
    ) -> None:
        clock.set(day, at)
        print(f"\n== {day:%a %d.%m} {at} · {title}")
        await action()
        _show_ledger(config)
        await asyncio.sleep(pause)

    def change() -> Awaitable[object]:
        async def apply() -> None:
            with connect(config.db_path) as conn:
                latest = store.latest_ok(conn)
                assert latest is not None
                current = store.load_lessons(conn, latest.id)
            found = schedule.store_lessons(demo.apply_changes(current, monday))
            print(f"      на сайте появились изменения: {found}")
            await bot.tick()

        return apply()

    try:
        await step("воскресенье: неделя в закреп", sunday_before, "07:00", bot.tick)
        await step("понедельник: картинка дня", monday, "07:00", bot.tick)
        await step("понедельник: расписание изменилось, текст и правка картинок", monday, "12:00", lambda: change())
        tuesday = monday + dt.timedelta(days=1)
        await step("вторник: вчерашнее удаляется, приходит новый день", tuesday, "07:00", bot.tick)
        next_sunday = monday + dt.timedelta(days=6)
        await step("воскресенье: новая неделя, старая удаляется", next_sunday, "07:00", bot.tick)
    finally:
        moscow.now = original
        await telegram.session.close()
