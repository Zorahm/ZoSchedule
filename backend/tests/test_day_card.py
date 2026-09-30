"""The "today" picture: colour badges, the pair number by the time, the room column."""

from __future__ import annotations

import datetime as dt

import pytest
from pydantic import SecretStr

from app.bot import demo
from app.bot.pictures import PictureBuilder
from app.bot.view import kind_tone
from app.config import AppConfig, BotConfig
from app.snapshots.service import ScheduleService
from tests.fakes import Clock, FakeRenderer

SUNDAY = dt.date(2026, 9, 27)
MONDAY = dt.date(2026, 9, 28)
TUESDAY = dt.date(2026, 9, 29)


@pytest.fixture
def bot_config(config: AppConfig) -> AppConfig:
    bot = BotConfig(token=SecretStr("4242:TEST"), chat_id="-100500")
    return config.model_copy(update={"bot": bot})


async def _body(config: AppConfig, day: dt.date) -> str:
    service = ScheduleService(config)
    service.prepare()
    service.store_lessons(demo.build_schedule(SUNDAY))
    picture = await PictureBuilder(config, FakeRenderer()).today(day)
    assert picture is not None
    page = picture.png.decode("utf-8")
    return page[page.index("<body>") :]  # the fonts in <style> are base64


@pytest.mark.parametrize(
    ("kind", "tone"),
    [
        ("лекция", "blue"),
        ("Лекция", "blue"),
        ("семинар", "green"),
        ("лабораторный практикум", "green"),
        ("практическое занятие", "green"),
        ("экзамен", "red"),
        ("зачёт", "red"),
        ("зачет", "red"),
        ("пересдача", "red"),
        ("консультация", "gray"),
        ("что-то новое", "gray"),
    ],
)
def test_the_badge_colour_follows_the_kind(kind: str, tone: str) -> None:
    assert kind_tone(kind) == tone


async def test_each_card_carries_a_badge_in_its_colour(bot_config: AppConfig, at: Clock) -> None:
    at(TUESDAY)
    body = await _body(bot_config, TUESDAY)  # lab, practice, lecture, lecture

    assert body.count('<span class="badge green">') == 2
    assert body.count('<span class="badge blue">') == 2
    assert '<span class="badge green">ЛР</span>' in body
    assert '<span class="badge blue">Лекция</span>' in body


async def test_the_pair_number_sits_under_the_time_not_among_the_tags(
    bot_config: AppConfig, at: Clock
) -> None:
    at(TUESDAY)
    body = await _body(bot_config, TUESDAY)

    assert '<div class="s">08:30</div><div class="e">10:00</div><div class="pn">1 пара</div>' in body
    assert '<div class="tags"><span>' not in body  # no longer the first thing in the tags


async def test_the_room_column_is_a_label_over_the_number_and_the_building_under_it(
    bot_config: AppConfig, at: Clock
) -> None:
    at(TUESDAY)
    body = await _body(bot_config, TUESDAY)

    assert (
        '<div class="place"><div class="lbl">аудитория</div><div class="num">118</div>'
        '<div class="bld">корп. Сокол</div></div>'
    ) in body
    assert "📍" not in body and "<svg" not in body  # no place icon


async def test_a_gym_is_not_called_an_auditorium(bot_config: AppConfig, at: Clock) -> None:
    at(MONDAY)
    body = await _body(bot_config, MONDAY)

    assert '<div class="place"><div class="num">Зал</div>' in body


async def test_the_teacher_stays_on_the_card_and_the_room_leaves_its_line(
    bot_config: AppConfig, at: Clock
) -> None:
    at(TUESDAY)
    body = await _body(bot_config, TUESDAY)

    assert '<div class="meta">Смирнов А. В.</div>' in body  # just the teacher now
    assert "ауд. " not in body


async def test_the_legend_appears_only_with_a_pair_shared_with_another_group(
    bot_config: AppConfig, at: Clock
) -> None:
    at(TUESDAY)
    with_stream = await _body(bot_config, TUESDAY)  # two lectures shared with 306
    at(dt.date(2026, 9, 30))
    without = await _body(bot_config, dt.date(2026, 9, 30))  # no shared pair

    assert '<div class="legend">' in with_stream
    assert "пара вместе с группой ОККИПд-306" in with_stream
    assert '<div class="legend">' not in without
    assert "пара вместе с группой" not in without and "tag stream" not in without
