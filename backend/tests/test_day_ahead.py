"""`day_ahead`: tomorrow's picture goes out when today's lessons end and becomes "Сегодня" at midnight."""

from __future__ import annotations

import datetime as dt

import pytest
from pydantic import SecretStr

from app.bot import store as bot_store
from app.render.pictures import PictureBuilder
from app.bot.service import BotService
from app.config import AppConfig, BotConfig
from app.db import connect
from app.snapshots.service import ScheduleService
from tests.fakes import Clock, FakeRenderer, FakeTelegram, save_demo

CHAT = "-100500"
SUNDAY_BEFORE = dt.date(2026, 9, 27)
MONDAY = dt.date(2026, 9, 28)
TUESDAY = dt.date(2026, 9, 29)
WEDNESDAY = dt.date(2026, 9, 30)
SATURDAY = dt.date(2026, 10, 3)
SUNDAY = dt.date(2026, 10, 4)


@pytest.fixture
def ahead_config(config: AppConfig) -> AppConfig:
    # Тема закреплена: тест про цикл «Завтра» → «Сегодня», а не про перерисовку под ночь.
    bot = BotConfig(
        token=SecretStr("token"), chat_id=CHAT, today_at=dt.time(7, 0), day_ahead=True, theme="light"
    )
    return config.model_copy(update={"bot": bot})


@pytest.fixture
def bot(ahead_config: AppConfig, telegram: FakeTelegram) -> BotService:
    ScheduleService(ahead_config).prepare()
    return BotService(ahead_config, telegram.bot, FakeRenderer())


def _posted_days(config: AppConfig) -> list[dt.date]:
    with connect(config.db_path) as conn:
        return [m.day for m in bot_store.all_of_kind(conn, chat_id=CHAT, kind="today")]


def _end(config: AppConfig, day: dt.date) -> str:
    end = PictureBuilder(config, FakeRenderer()).day_end(day)
    assert end is not None
    return end


def _minute_before(clock: str) -> str:
    moment = dt.datetime.combine(MONDAY, dt.time.fromisoformat(clock)) - dt.timedelta(minutes=1)
    return moment.strftime("%H:%M")


def test_off_by_default() -> None:
    assert BotConfig().day_ahead is False


async def test_tomorrow_goes_out_when_todays_lessons_end(
    bot: BotService, ahead_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    at(MONDAY, "07:00")
    save_demo(ahead_config, MONDAY)
    end = _end(ahead_config, MONDAY)
    await bot.tick()
    assert telegram.calls == []  # today_at is not the moment: the lessons are still on

    at(MONDAY, _minute_before(end))
    await bot.tick()
    assert telegram.calls == []

    at(MONDAY, end)
    await bot.tick()
    await bot.tick()
    assert telegram.kinds() == ["photo"]  # once
    assert telegram.captions[0].startswith("📅 Завтра · вторник")
    assert _posted_days(ahead_config) == [TUESDAY]


async def test_a_full_day_cycle(
    bot: BotService, ahead_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    at(SUNDAY_BEFORE, "07:00")  # no lessons on Sunday: Monday goes out at today_at
    save_demo(ahead_config, SUNDAY_BEFORE)
    await bot.tick()
    assert telegram.kinds() == ["photo", "pin", "photo"]  # the week, then Monday
    monday_photo = telegram.calls[2][1]
    assert telegram.captions[1].startswith("📅 Завтра · понедельник")

    at(MONDAY, "00:01")
    await bot.tick()
    assert telegram.calls[3:] == [("edit", monday_photo)]  # "Сегодня" now, in place
    picture = await bot.pictures.today(MONDAY, force=True)
    assert picture is not None and picture.caption.startswith("📅 Сегодня · понедельник")

    at(MONDAY, "08:00")
    await bot.tick()
    assert telegram.calls[4:] == []  # Monday stays all day

    at(MONDAY, _end(ahead_config, MONDAY))
    await bot.tick()
    assert telegram.calls[4:] == [("photo", telegram.calls[4][1]), ("delete", monday_photo)]
    assert telegram.captions[-1].startswith("📅 Завтра · вторник")
    assert _posted_days(ahead_config) == [TUESDAY]


async def test_a_restart_in_the_evening_still_posts_tomorrow(
    bot: BotService, ahead_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    at(MONDAY, "22:00")
    save_demo(ahead_config, MONDAY)
    await bot.tick()
    assert telegram.captions == [telegram.captions[0]]
    assert telegram.captions[0].startswith("📅 Завтра · вторник")


async def test_on_the_eve_of_a_day_off_the_finished_day_stays(
    bot: BotService, ahead_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    at(SATURDAY, "07:00")
    save_demo(ahead_config, SATURDAY)
    with connect(ahead_config.db_path) as conn:
        bot_store.record(conn, chat_id=CHAT, kind="today", day=SATURDAY, message_id=777)

    at(SATURDAY, "21:00")  # Saturday is over, but Sunday has no picture to take its place
    await bot.tick()
    assert "photo" not in telegram.kinds()
    assert ("delete", 777) not in telegram.calls


async def test_go_in_the_evening_shows_tomorrow(
    bot: BotService, ahead_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    at(MONDAY, "19:30")
    save_demo(ahead_config, MONDAY)
    assert await bot.go(CHAT, None) is None
    assert telegram.captions[-1].startswith("📅 Завтра · вторник")

    await bot.tick()
    assert telegram.kinds() == ["photo", "pin", "photo"]  # the week and Tuesday, nothing twice
    assert _posted_days(ahead_config) == [TUESDAY]


async def test_go_during_the_lessons_shows_today_until_they_end(
    bot: BotService, ahead_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    at(TUESDAY, "08:00")
    save_demo(ahead_config, TUESDAY)
    await bot.go(CHAT, None)
    assert telegram.captions[-1].startswith("📅 Сегодня · вторник")
    today_photo = telegram.calls[-1][1]

    await bot.tick()
    assert telegram.kinds() == ["photo", "pin", "photo"]  # tomorrow waits for the lessons

    at(TUESDAY, _end(ahead_config, TUESDAY))
    await bot.tick()
    assert telegram.captions[-1].startswith("📅 Завтра · среда")
    assert ("delete", today_photo) in telegram.calls
    assert _posted_days(ahead_config) == [WEDNESDAY]
