"""`day_ahead`: the day's picture goes out the evening before and becomes "Сегодня" at midnight."""

from __future__ import annotations

import datetime as dt

import pytest
from pydantic import SecretStr

from app.bot import store as bot_store
from app.bot.pictures import PictureBuilder
from app.bot.service import BotService
from app.config import AppConfig, BotConfig
from app.models.db import connect
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
    bot = BotConfig(token=SecretStr("token"), chat_id=CHAT, today_at=dt.time(19, 0), day_ahead=True)
    return config.model_copy(update={"bot": bot})


@pytest.fixture
def bot(ahead_config: AppConfig, telegram: FakeTelegram) -> BotService:
    ScheduleService(ahead_config).prepare()
    return BotService(ahead_config, telegram.bot, FakeRenderer())


def _posted_days(config: AppConfig) -> list[dt.date]:
    with connect(config.db_path) as conn:
        return [m.day for m in bot_store.all_of_kind(conn, chat_id=CHAT, kind="today")]


def test_off_by_default() -> None:
    assert BotConfig().day_ahead is False


async def test_tomorrow_is_posted_in_the_evening_as_tomorrow(
    bot: BotService, ahead_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    at(MONDAY, "18:59")
    save_demo(ahead_config, MONDAY)
    await bot.tick()
    assert telegram.calls == []  # nothing in the morning: the day was announced the evening before

    at(MONDAY, "19:00")
    await bot.tick()
    await bot.tick()
    assert telegram.kinds() == ["photo"]  # once
    assert telegram.captions[0].startswith("📅 Завтра · вторник")
    assert _posted_days(ahead_config) == [TUESDAY]


async def test_at_midnight_tomorrow_turns_into_today_in_place(
    bot: BotService, ahead_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    at(MONDAY, "19:00")
    save_demo(ahead_config, MONDAY)
    await bot.tick()
    photo = telegram.calls[0][1]

    at(TUESDAY, "00:01")
    await bot.tick()
    assert telegram.calls[1:] == [("edit", photo)]  # the same message, no new post
    await bot.tick()
    assert telegram.calls[1:] == [("edit", photo)]  # and only once

    picture = await bot._pictures.today(TUESDAY, force=True)  # pyright: ignore[reportPrivateUsage]
    assert picture is not None and picture.caption.startswith("📅 Сегодня · вторник")


async def test_the_next_evening_replaces_the_day_with_the_next_one(
    bot: BotService, ahead_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    at(MONDAY, "19:00")
    save_demo(ahead_config, MONDAY)
    await bot.tick()
    tuesday_photo = telegram.calls[0][1]

    at(TUESDAY, "19:00")
    await bot.tick()
    assert ("delete", tuesday_photo) in telegram.calls
    assert telegram.captions[-1].startswith("📅 Завтра · среда")
    assert _posted_days(ahead_config) == [WEDNESDAY]


async def test_no_picture_on_the_eve_of_a_day_off(
    bot: BotService, ahead_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    at(SATURDAY, "19:00")
    save_demo(ahead_config, SATURDAY)
    await bot.tick()
    assert "photo" not in telegram.kinds()  # Sunday has no lessons


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


async def test_sunday_evening_posts_monday(
    bot: BotService, ahead_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    at(SUNDAY, "19:00")
    save_demo(ahead_config, SUNDAY)
    await bot.tick()  # the week is late here; normally it went out at week_at
    assert any(caption.startswith("📅 Завтра · понедельник") for caption in telegram.captions)


@pytest.fixture
def morning_bot(ahead_config: AppConfig, telegram: FakeTelegram) -> BotService:
    bot = ahead_config.bot.model_copy(update={"today_at": dt.time(7, 0)})
    config = ahead_config.model_copy(update={"bot": bot})
    ScheduleService(config).prepare()
    return BotService(config, telegram.bot, FakeRenderer())


async def test_in_the_morning_today_stays_until_its_lessons_are_over(
    morning_bot: BotService, ahead_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    at(SUNDAY_BEFORE, "07:00")
    save_demo(ahead_config, SUNDAY_BEFORE)
    await morning_bot.tick()  # the week and Monday
    monday_photo = telegram.calls[0][1]
    end = PictureBuilder(ahead_config, FakeRenderer()).day_end(MONDAY)
    assert end is not None

    at(MONDAY, "07:00")
    await morning_bot.tick()
    assert "delete" not in telegram.kinds()  # Monday's lessons have not even begun
    assert _posted_days(ahead_config) == [MONDAY, TUESDAY]

    minute_before = dt.datetime.combine(MONDAY, dt.time.fromisoformat(end)) - dt.timedelta(minutes=1)
    at(MONDAY, minute_before.strftime("%H:%M"))
    await morning_bot.tick()
    assert "delete" not in telegram.kinds()  # the last lesson is still on

    at(MONDAY, end)
    await morning_bot.tick()
    assert telegram.calls[-1] == ("delete", monday_photo)
    assert _posted_days(ahead_config) == [TUESDAY]


async def test_a_finished_day_stays_while_there_is_nothing_to_replace_it(
    morning_bot: BotService, ahead_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    at(SATURDAY, "07:00")
    save_demo(ahead_config, SATURDAY)
    with connect(ahead_config.db_path) as conn:
        bot_store.record(conn, chat_id=CHAT, kind="today", day=SATURDAY, message_id=777)

    at(SATURDAY, "21:00")  # Saturday is over, but Sunday has no picture to take its place
    await morning_bot.tick()
    assert ("delete", 777) not in telegram.calls


async def test_go_in_the_morning_shows_today_and_then_tomorrow_joins(
    morning_bot: BotService, ahead_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    at(TUESDAY, "08:00")
    save_demo(ahead_config, TUESDAY)
    await morning_bot.go(CHAT, None)
    assert telegram.captions[-1].startswith("📅 Сегодня · вторник")

    await morning_bot.tick()
    assert telegram.captions[-1].startswith("📅 Завтра · среда")
    assert _posted_days(ahead_config) == [TUESDAY, WEDNESDAY]
