"""Ночная тема: границы ночи, выбор по московскому времени, перерисовка закрепа."""

from __future__ import annotations

import datetime as dt

import pytest
from pydantic import SecretStr, ValidationError

from app import moscow
from app.render import templates, theme
from app.bot.dev import demo
from app.render.night import NIGHT_CSS
from app.render.pictures import PictureBuilder
from app.bot.service import BotService
from app.render.view import DayView, Header, build_days
from app.config import AppConfig, BotConfig
from app.models.domain import SnapshotMeta
from app.snapshots.service import ScheduleService
from tests.fakes import Clock, FakeRenderer, FakeTelegram, save_demo

SUNDAY = dt.date(2026, 9, 27)
MONDAY = dt.date(2026, 9, 28)
TUESDAY = dt.date(2026, 9, 29)


def _time(clock: str) -> dt.time:
    return dt.time.fromisoformat(clock)


@pytest.fixture
def bot_config(config: AppConfig) -> AppConfig:
    bot = BotConfig(token=SecretStr("4242:TEST"), chat_id="-100500")
    return config.model_copy(update={"bot": bot})


@pytest.mark.parametrize(
    ("clock", "night"),
    [
        ("19:59", False),
        ("20:00", True),  # граница включительно: с 20:00 уже ночь
        ("23:59", True),
        ("00:00", True),
        ("06:59", True),
        ("07:00", False),  # а в 07:00 уже день: утренний пост выходит светлым
        ("12:00", False),
    ],
)
def test_default_night_runs_through_midnight(clock: str, night: bool) -> None:
    assert theme.is_night(_time(clock), _time("20:00"), _time("07:00")) is night


def test_night_inside_one_day() -> None:
    assert theme.is_night(_time("14:00"), _time("13:00"), _time("15:00"))
    assert not theme.is_night(_time("15:00"), _time("13:00"), _time("15:00"))


def test_auto_follows_the_moscow_clock(at: Clock) -> None:
    at(MONDAY, "21:30")
    assert theme.resolve(BotConfig(), moscow.now()) == "night"
    at(MONDAY, "08:00")
    assert theme.resolve(BotConfig(), moscow.now()) == "light"


def test_fixed_theme_ignores_the_clock(at: Clock) -> None:
    for clock in ("03:00", "13:00"):
        at(MONDAY, clock)
        assert theme.resolve(BotConfig(theme="light"), moscow.now()) == "light"
        assert theme.resolve(BotConfig(theme="night"), moscow.now()) == "night"


def test_equal_night_bounds_are_rejected() -> None:
    with pytest.raises(ValidationError):
        BotConfig(night_from=_time("07:00"), night_to=_time("07:00"))


def test_night_rules_are_scoped_to_the_night_frame() -> None:
    rules = [rule for rule in NIGHT_CSS.split("}") if rule.strip()]
    assert rules[0].startswith(".frame.night{")
    assert all(rule.lstrip().startswith(".night ") for rule in rules[1:])
    assert ".night .chip.off{" in NIGHT_CSS  # список селекторов тоже разобран по запятым
    assert ".night .row.off .dow,.night .row.off .dat{" in NIGHT_CSS


def test_light_page_is_not_touched_by_the_night_theme(config: AppConfig) -> None:
    week = _week(config)
    header = Header("ОККИПд-307", TUESDAY)
    assert templates.week_html(week, header) == templates.week_html(week, header, "light")
    assert "frame night" not in templates.week_html(week, header)
    assert "frame night" in templates.week_html(week, header, "night")
    assert ".frame.night{" in templates.week_html(week, header, "night")
    assert ".frame.night{" not in templates.week_html(week, header)


def test_night_keeps_the_marks_that_must_not_blur(config: AppConfig) -> None:
    week = _week(config)
    page = templates.week_html(week, Header("ОККИПд-307", TUESDAY), "night")
    assert "не указан на сайте" in templates.day_html(week[1], week, Header("g", TUESDAY), "night")
    assert 'class="row off"' in page  # выходной не сливается с обычным днём
    assert "ПЕРЕСДАЧА" in page


def _week(config: AppConfig) -> list[DayView]:
    lessons = demo.build_schedule(MONDAY)
    snapshot = SnapshotMeta(
        id=1,
        taken_at=dt.datetime(2026, 9, 28, 9, 0, tzinfo=moscow.MOSCOW),
        status="ok",
        covered_from=MONDAY,
        covered_to=MONDAY + dt.timedelta(days=13),
    )
    return build_days(lessons, snapshot, MONDAY, 6, group=config.group.name)


async def test_week_is_redrawn_when_the_night_comes(bot_config: AppConfig, at: Clock) -> None:
    at(SUNDAY, "08:00")
    service = ScheduleService(bot_config)
    service.prepare()
    service.store_lessons(demo.build_schedule(SUNDAY))
    builder = PictureBuilder(bot_config, FakeRenderer())

    morning = await builder.week(MONDAY)
    at(SUNDAY, "19:59")
    evening = await builder.week(MONDAY)
    at(SUNDAY, "20:00")
    night = await builder.week(MONDAY)
    at(SUNDAY, "06:59")  # тот же вечер, глубокой ночью
    deep_night = await builder.week(MONDAY)
    at(SUNDAY, "07:00")
    next_morning = await builder.week(MONDAY)

    assert morning and evening and night and deep_night and next_morning
    assert morning.fingerprint == evening.fingerprint == next_morning.fingerprint
    assert night.fingerprint == deep_night.fingerprint
    assert night.fingerprint != morning.fingerprint  # на этом `sync_pictures` правит закреп
    assert b"frame night" in night.png and b"frame night" not in morning.png


async def test_day_picture_follows_the_theme_too(bot_config: AppConfig, at: Clock) -> None:
    at(MONDAY, "22:00")
    service = ScheduleService(bot_config)
    service.prepare()
    service.store_lessons(demo.build_schedule(MONDAY))
    picture = await PictureBuilder(bot_config, FakeRenderer()).today(TUESDAY)
    assert picture is not None
    assert b"frame night" in picture.png


async def test_the_pinned_week_is_edited_at_nightfall_and_back_in_the_morning(
    bot_config: AppConfig, telegram: FakeTelegram, at: Clock
) -> None:
    at(SUNDAY, "08:00")
    save_demo(bot_config, SUNDAY)
    bot = BotService(bot_config, telegram.bot, FakeRenderer())
    await bot.tick()
    assert telegram.kinds() == ["photo", "pin"]
    week = telegram.calls[0][1]

    at(SUNDAY, "19:59")
    await bot.tick()
    assert telegram.calls[2:] == []

    at(SUNDAY, "20:00")
    await bot.tick()
    await bot.tick()
    assert telegram.calls[2:] == [("edit", week)]  # один раз, а не на каждый тик

    at(MONDAY, "07:00")
    await bot.tick()
    assert ("edit", week) in telegram.calls[3:]  # утром обратно в светлую
