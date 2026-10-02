"""Типы журнала посещаемости: что уходит в мини-приложение и что приходит из него.

Внешняя граница, поэтому pydantic: всё, что прислал клиент, проверяется здесь, а не
в обработчиках.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Mark = Literal["present", "absent"]

DayState = Literal["open", "locked", "off", "unpublished", "nodata"]
"""Что журнал знает про день.

``open`` — первая пара началась, отметки можно ставить на весь день;
``locked`` — день есть, но первая пара ещё не началась;
``off`` — день опубликован, пар нет (или только пересдача): выходной;
``unpublished`` — день ещё не выложен на сайте, это **не** выходной;
``nodata`` — прошедший день, расписание которого бот не застал.
"""

NAME_MAX = 120
ROSTER_MAX = 150
CHANGES_MAX = 600


class Pair(BaseModel):
    """Столбец таблицы: одно время начала. Две пары в одном слоте дают один столбец."""

    slot: str
    number: int
    start: str
    end: str
    title: str
    kind: str
    teacher: str | None
    room: str | None
    lessons: int


class StudentRow(BaseModel):
    id: int
    name: str
    marks: dict[str, Mark]


class JournalDay(BaseModel):
    date: dt.date
    state: DayState
    opens_at: str | None
    pairs: list[Pair]
    students: list[StudentRow]


class WeekEntry(BaseModel):
    date: dt.date
    state: DayState
    marked: int
    total: int


class Journal(BaseModel):
    now: str
    group: str
    current_day: dt.date
    next_opens_at: str | None
    week: list[WeekEntry]
    day: JournalDay


class RosterEntry(BaseModel):
    id: int | None = None
    name: str

    @field_validator("name")
    @classmethod
    def _tidy(cls, value: str) -> str:
        value = " ".join(value.split())
        if len(value) < 2:
            raise ValueError("ФИО слишком короткое")
        if len(value) > NAME_MAX:
            raise ValueError(f"ФИО длиннее {NAME_MAX} знаков")
        return value


class RosterBody(BaseModel):
    students: list[RosterEntry] = Field(max_length=ROSTER_MAX)


class RosterOut(BaseModel):
    students: list[RosterEntry]


class MarkChange(BaseModel):
    student_id: int
    slot: str = Field(pattern=r"^\d{2}:\d{2}$")
    mark: Mark | None
    """None снимает отметку."""


class MarksBody(BaseModel):
    date: dt.date
    changes: list[MarkChange] = Field(max_length=CHANGES_MAX)


class ReportBody(BaseModel):
    date: dt.date
    titles: bool = True
    """Подписывать ли пары названиями: куратору они нужны не всегда."""
