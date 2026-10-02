"""Какие пары были в день и открыт ли он для отметок. Данные берутся из снимков.

День открывается, когда началась его первая пара, и остаётся открытым: староста
заполняет весь день сразу, а вчерашнее может доправить завтра.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from collections import defaultdict
from dataclasses import dataclass

from app import moscow
from app.attendance.models import DayState, Pair
from app.bot import texts
from app.models.domain import Lesson
from app.snapshots import store as snapshots

LOOKBACK_DAYS = 14
"""Как далеко назад искать последний учебный день: хватает на каникулярную неделю."""
LOOKAHEAD_DAYS = 14


@dataclass(frozen=True, slots=True)
class PairInfo:
    pair: Pair
    starts_at: dt.datetime


@dataclass(frozen=True, slots=True)
class DaySchedule:
    date: dt.date
    covered: bool
    """Есть ли снимок, в диапазон которого входит день. Без него пустой день не выходной."""
    pairs: tuple[PairInfo, ...]

    @property
    def opens_at(self) -> dt.datetime | None:
        return self.pairs[0].starts_at if self.pairs else None

    def state(self, now: dt.datetime) -> DayState:
        if not self.covered:
            return "unpublished" if self.date >= now.date() else "nodata"
        if not self.pairs:
            return "off"
        return "open" if self.pairs[0].starts_at <= now else "locked"


def _joined(values: list[str]) -> str:
    return " / ".join(dict.fromkeys(values))


def _pair(slot: str, number: int, lessons: list[Lesson]) -> PairInfo:
    teachers = [lesson.teacher.strip() for lesson in lessons if lesson.teacher and lesson.teacher.strip()]
    rooms = [lesson.room.strip() for lesson in lessons if lesson.room and lesson.room.strip()]
    return PairInfo(
        pair=Pair(
            slot=slot,
            number=number,
            start=slot,
            end=max(lesson.ends_at for lesson in lessons).strftime("%H:%M"),
            title=_joined([lesson.discipline.strip() for lesson in lessons]),
            kind=_joined([texts.kind_label(lesson.kind) for lesson in lessons]),
            teacher=_joined(teachers) or None,
            room=_joined(rooms) or None,
            lessons=len(lessons),
        ),
        starts_at=lessons[0].starts_at,
    )


def _pairs(lessons: list[Lesson]) -> tuple[PairInfo, ...]:
    """Занятия группы по слотам. Пересдача идёт для должников и столбца не получает."""
    by_slot: defaultdict[str, list[Lesson]] = defaultdict(list)
    for lesson in sorted(lessons, key=lambda item: item.starts_at):
        if not lesson.is_retake:
            by_slot[lesson.slot].append(lesson)
    return tuple(_pair(slot, number, group) for number, (slot, group) in enumerate(by_slot.items(), 1))


class Schedule:
    """Расписание дней для одного запроса: каждый день читается из базы один раз."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._days: dict[dt.date, DaySchedule] = {}

    def day(self, day: dt.date) -> DaySchedule:
        known = self._days.get(day)
        if known is None:
            # Сайт подчищает прошедшее, поэтому вчерашние пары живут в старых снимках.
            meta = snapshots.latest_covering(self._conn, day)
            lessons = snapshots.load_lessons(self._conn, meta.id, start=day, end=day) if meta else []
            known = DaySchedule(day, meta is not None, _pairs(lessons))
            self._days[day] = known
        return known

    def current_day(self, now: dt.datetime) -> dt.date:
        """Последний день, чья первая пара уже началась.

        Выходные и дни до первой пары не считаются: суббота остаётся на экране до
        понедельника, 08:30. Нет таких дней за две недели — сегодняшний (покажет, почему закрыт).
        """
        today = now.date()
        for back in range(LOOKBACK_DAYS + 1):
            day = today - dt.timedelta(days=back)
            if self.day(day).state(now) == "open":
                return day
        return today

    def next_opening(self, after: dt.date, now: dt.datetime) -> dt.datetime | None:
        """Когда откроется ближайший ещё закрытый день после ``after``."""
        # Закрытым может быть только сегодняшний или будущий день: у прошедшего первая пара уже была.
        start = max(after + dt.timedelta(days=1), now.date())
        for ahead in range(LOOKAHEAD_DAYS + 1):
            schedule = self.day(start + dt.timedelta(days=ahead))
            if schedule.state(now) == "locked":
                return schedule.opens_at
        return None


def monday_of(day: dt.date) -> dt.date:
    return day - dt.timedelta(days=day.weekday())


def stamp(value: dt.datetime) -> str:
    return moscow.isoformat(value)
