"""Журнал: собирает ответ мини-приложению и принимает отметки.

Правила открытия дня и «Ещё не опубликовано» против «Выходной» живут в schedule.py,
SQL — в store.py. Здесь они сводятся в то, что видит староста.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from collections.abc import Mapping, Sequence

from app.attendance import store
from app.attendance.models import (
    Journal,
    JournalDay,
    Mark,
    MarkChange,
    Pair,
    StudentRow,
    WeekEntry,
)
from app.attendance.schedule import DaySchedule, Schedule, monday_of, stamp

WEEK_DAYS = 6
"""Неделя колледжа — с понедельника по субботу, воскресного столбца нет."""


class JournalError(Exception):
    """Отказ, который старосте надо показать: у него свой код, статус и русский текст."""

    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _count(
    marks: Mapping[tuple[int, str], Mark], students: Sequence[store.Student], pairs: Sequence[Pair]
) -> int:
    """Отметки, которые сейчас видны в таблице: у живых студентов и на нынешних парах."""
    ids = {student.id for student in students}
    slots = {pair.slot for pair in pairs}
    return sum(1 for (student_id, slot) in marks if student_id in ids and slot in slots)


def _week(
    conn: sqlite3.Connection,
    schedule: Schedule,
    students: Sequence[store.Student],
    monday: dt.date,
    now: dt.datetime,
) -> list[WeekEntry]:
    entries: list[WeekEntry] = []
    for offset in range(WEEK_DAYS):
        day = monday + dt.timedelta(days=offset)
        info = schedule.day(day)
        state = info.state(now)
        pairs = [item.pair for item in info.pairs]
        total = len(students) * len(pairs) if state == "open" else 0
        marked = _count(store.marks_on(conn, day), students, pairs) if state == "open" else 0
        entries.append(WeekEntry(date=day, state=state, marked=marked, total=total))
    return entries


def day_view(
    conn: sqlite3.Connection,
    info: DaySchedule,
    students: Sequence[store.Student],
    now: dt.datetime,
) -> JournalDay:
    state = info.state(now)
    pairs = [item.pair for item in info.pairs]
    rows: list[StudentRow] = []
    if state == "open":
        marks = store.marks_on(conn, info.date)
        for student in students:
            own: dict[str, Mark] = {
                pair.slot: marks[(student.id, pair.slot)]
                for pair in pairs
                if (student.id, pair.slot) in marks
            }
            rows.append(StudentRow(id=student.id, name=student.name, marks=own))
    return JournalDay(
        date=info.date,
        state=state,
        opens_at=stamp(info.opens_at) if state == "locked" and info.opens_at else None,
        pairs=pairs if state in ("open", "locked") else [],
        students=rows,
    )


def build(
    conn: sqlite3.Connection,
    *,
    group: str,
    day: dt.date | None,
    week_of: dt.date | None,
    now: dt.datetime,
) -> Journal:
    """Всё для одного экрана: полоса недели и выбранный день.

    ``day=None`` — «следовать за ботом»: показывается текущий день, и он сам меняется,
    когда начинается первая пара следующего.
    """
    schedule = Schedule(conn)
    students = store.active_students(conn)
    current = schedule.current_day(now)
    shown = day or current
    following = schedule.next_opening(current, now)
    return Journal(
        now=stamp(now),
        group=group,
        current_day=current,
        next_opens_at=stamp(following) if following else None,
        week=_week(conn, schedule, students, monday_of(week_of or shown), now),
        day=day_view(conn, schedule.day(shown), students, now),
    )


_CLOSED_TEXT = {
    "locked": "День ещё не начался: журнал откроется с первой парой.",
    "off": "В этот день у группы нет пар.",
    "unpublished": "Расписание на этот день ещё не опубликовано.",
    "nodata": "Расписание этого дня не сохранилось.",
}


def open_day(conn: sqlite3.Connection, day: dt.date, now: dt.datetime) -> DaySchedule:
    """Расписание дня, если по нему можно работать; иначе отказ с понятным текстом."""
    info = Schedule(conn).day(day)
    state = info.state(now)
    if state != "open":
        raise JournalError("not_open", _CLOSED_TEXT[state], 403)
    return info


def save_marks(
    conn: sqlite3.Connection,
    *,
    day: dt.date,
    changes: Sequence[MarkChange],
    by: int | None,
    now: dt.datetime,
) -> int:
    """Принимает пачку отметок целиком или отказывает целиком. Возвращает, сколько принято."""
    info = open_day(conn, day, now)
    slots = {item.pair.slot for item in info.pairs}
    known = {student.id for student in store.active_students(conn)}
    for change in changes:
        if change.slot not in slots:
            raise JournalError("unknown_pair", "Такой пары в этот день нет: расписание могло измениться.", 422)
        if change.student_id not in known:
            raise JournalError("unknown_student", "Этого студента нет в списке группы.", 422)
    store.write_marks(conn, day, changes, by=by, now=now)
    return len(changes)
