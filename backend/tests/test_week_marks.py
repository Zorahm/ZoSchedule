"""Days off are green on the week picture and in the day picture's strip; lesson kinds get a dot."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import replace

from app.bot import templates
from app.bot.view import DayView, Header, build_days
from app.config import AppConfig
from app.models.db import connect
from app.snapshots import store
from tests.fakes import save_demo

GROUP = "ОККИПд-307"
TUESDAY = dt.date(2026, 9, 29)
MONDAY = dt.date(2026, 9, 28)
HEADER = Header(GROUP, TUESDAY)


def _week(config: AppConfig, *, published: bool = True) -> list[DayView]:
    save_demo(config, TUESDAY)
    with connect(config.db_path) as conn:
        latest = store.latest_ok(conn)
        assert latest is not None
        lessons = store.load_lessons(conn, latest.id)
    return build_days(lessons, latest if published else None, MONDAY, 6, group=GROUP)


def _rows(page: str) -> list[str]:
    return re.findall(r'<div class="row[^"]*"', page)


def test_days_off_are_marked_and_school_days_are_not(config: AppConfig) -> None:
    page = templates.week_html(_week(config), HEADER)
    # Mon–Wed and Sat have lessons; Thursday has only a retake and Friday nothing: both off.
    assert ["off" in row for row in _rows(page)] == [False, False, False, True, True, False]
    assert "#228B22" in page


def test_an_unpublished_day_is_not_marked_as_a_day_off(config: AppConfig) -> None:
    week = _week(config, published=False)
    assert all(day.coverage == "unpublished" for day in week)
    assert not any("off" in row for row in _rows(templates.week_html(week, HEADER)))


def test_every_lesson_carries_the_dot_of_its_kind(config: AppConfig) -> None:
    page = templates.week_html(_week(config), HEADER)
    dots = re.findall(r'<div class="n kd k-(\w+)">([^<]*)<', page)
    assert ("blue", "Математический анализ") in dots  # a lecture
    assert ("green", "Веб-программирование") in dots  # practice
    assert ("green", "Базы данных") in dots  # a lab: green like on the day picture
    assert len(dots) == 13  # every lesson of the week; retakes keep their own tag


def test_the_legend_explains_only_the_kinds_on_the_week(config: AppConfig) -> None:
    page = templates.week_html(_week(config), HEADER)
    legend = re.search(r'<div class="kinds">(.*?)</div>', page)
    assert legend is not None
    items = re.findall(r'<i class="dot k-(\w+)"></i>([^<]*)<', legend.group(1))
    assert [tone for tone, _ in items] == ["blue", "green"]  # no exams this week, no red
    assert items[0][1] == "Лекция"


def test_a_week_without_lessons_has_no_legend(config: AppConfig) -> None:
    empty = [replace(day, lessons=()) for day in _week(config)]
    assert 'class="kinds"' not in templates.week_html(empty, HEADER)


def _chips(page: str) -> list[str]:
    return re.findall(r'<div class="chip([^"]*)"', page)


def test_the_day_pictures_strip_marks_days_off_too(config: AppConfig) -> None:
    week = _week(config)
    page = templates.day_html(week[1], week, HEADER)  # Tuesday
    # Tuesday is the pictured day; Thursday (only a retake) and Friday are off.
    assert [chip.strip() for chip in _chips(page)] == ["", "on", "", "off", "off", ""]


def test_the_pictured_day_stays_orange_even_when_it_is_off(config: AppConfig) -> None:
    week = _week(config)
    page = templates.day_html(week[4], week, HEADER)  # Friday, a day off
    assert _chips(page)[4].strip() == "on"


def test_an_unpublished_day_is_not_green_in_the_strip(config: AppConfig) -> None:
    week = _week(config, published=False)
    assert "off" not in "".join(_chips(templates.day_html(week[1], week, HEADER)))


def test_on_a_day_off_the_retake_comes_first_and_the_word_is_big(config: AppConfig) -> None:
    week = _week(config)
    page = templates.day_html(week[3], week, HEADER)  # Thursday: only a retake
    assert page.index('class="card retake"') < page.index('class="empty off"')


def test_on_a_school_day_the_retake_still_follows_the_lessons(config: AppConfig) -> None:
    week = _week(config)
    page = templates.day_html(week[0], week, HEADER)  # Monday: lessons and a retake
    assert page.rindex('<div class="card">') < page.index('class="card retake"')


def test_not_published_does_not_get_the_big_day_off_word(config: AppConfig) -> None:
    week = _week(config, published=False)
    page = templates.day_html(week[4], week, HEADER)
    assert 'class="empty"' in page and 'class="empty off"' not in page
