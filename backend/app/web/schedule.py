"""Расписание для мини-приложения: дни отрезка с актуальными парами.

Данные те же, что видит бот: последние снимки (``snapshots``). Изменения не показываем:
студенту нужно то, что будет, а не что было. Здесь только сборка ответа; пары не склеиваются в слоты, как в журнале, потому что две пары
в одно время студент должен видеть обе.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from typing import Literal

from pydantic import BaseModel

from app import texts
from app.models.domain import Lesson
from app.snapshots import store as snapshots

MAX_SPAN_DAYS = 70
"""Больше двух с небольшим месяцев за запрос не отдаём: приложение листает год по частям."""
PAST_DAYS = 400
FUTURE_DAYS = 400

DayKind = Literal["lessons", "off", "unpublished", "nodata"]
"""``off`` — день опубликован, пар нет; ``unpublished`` — ещё не выложен, это не выходной;
``nodata`` — прошедший день, расписание которого бот не застал."""


class ScheduleLesson(BaseModel):
    start: str
    end: str
    title: str
    kind: str
    exam: bool
    teacher: str | None
    room: str | None
    building: str | None
    stream: list[str]


class ScheduleDay(BaseModel):
    date: dt.date
    state: DayKind
    lessons: list[ScheduleLesson]


class ScheduleOut(BaseModel):
    now: dt.datetime
    group: str
    headman: bool
    published_to: dt.date | None
    days: list[ScheduleDay]


def check_span(start: dt.date, end: dt.date, today: dt.date) -> None:
    if end < start:
        raise ValueError("Конец отрезка раньше начала.")
    if (end - start).days + 1 > MAX_SPAN_DAYS:
        raise ValueError(f"За раз можно запросить не больше {MAX_SPAN_DAYS} дней.")
    if start < today - dt.timedelta(days=PAST_DAYS) or end > today + dt.timedelta(days=FUTURE_DAYS):
        raise ValueError("Эти даты вне диапазона расписания.")


def _lesson(lesson: Lesson) -> ScheduleLesson:
    return ScheduleLesson(
        start=lesson.starts_at.strftime("%H:%M"),
        end=lesson.ends_at.strftime("%H:%M"),
        title=lesson.discipline.strip(),
        kind=texts.kind_label(lesson.kind),
        exam=lesson.is_exam,
        teacher=(lesson.teacher or "").strip() or None,
        room=(lesson.room or "").strip() or None,
        building=(lesson.building_short or "").strip() or None,
        stream=list(lesson.stream),
    )


def _day(conn: sqlite3.Connection, day: dt.date, today: dt.date) -> ScheduleDay:
    # Сайт подчищает прошедшее, поэтому день ищем в самом свежем снимке, который его покрывал.
    meta = snapshots.latest_covering(conn, day)
    if meta is None:
        return ScheduleDay(date=day, state="unpublished" if day >= today else "nodata", lessons=[])
    lessons = [
        _lesson(lesson)
        for lesson in snapshots.load_lessons(conn, meta.id, start=day, end=day)
        if not lesson.is_retake
    ]
    return ScheduleDay(date=day, state="lessons" if lessons else "off", lessons=lessons)


def build(
    conn: sqlite3.Connection,
    *,
    group: str,
    start: dt.date,
    end: dt.date,
    headman: bool,
    now: dt.datetime,
) -> ScheduleOut:
    check_span(start, end, now.date())
    days = [_day(conn, start + dt.timedelta(days=offset), now.date()) for offset in range((end - start).days + 1)]
    latest = snapshots.latest_ok(conn)
    return ScheduleOut(
        now=now,
        group=group,
        headman=headman,
        published_to=latest.covered_to if latest else None,
        days=days,
    )
