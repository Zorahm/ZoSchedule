"""Доменные типы: пара и метаданные снимка."""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel

SnapshotStatus = Literal["ok", "failed"]

SnapshotSource = Literal["site", "import"]
"""Откуда снимок.

``site`` — прогон парсера, ``import`` — выгрузка из ЛМС, залитая вручную.
Диффы считаются внутри источника: сравнение выгрузки с ответом сайта выдало бы
полную отмену расписания и полное же добавление заново.
"""

EXAM_KINDS = frozenset({"зачёт", "экзамен"})

RETAKE_KIND = "пересдача"
"""Сайт помечает пересдачу типом занятия, как лекцию или зачёт."""

_WEEKDAYS_RU = (
    "Понедельник",
    "Вторник",
    "Среда",
    "Четверг",
    "Пятница",
    "Суббота",
    "Воскресенье",
)


def weekday_ru(day: dt.date) -> str:
    """День недели по дате.

    Считаем сами, а не берём ``dayOfWeek`` из ответа сайта: для дат вне
    опубликованного диапазона дня в ответе нет, а подписать его надо.
    """
    return _WEEKDAYS_RU[day.weekday()]


class Lesson(BaseModel):
    """Одна пара из снимка, уже нормализованная."""

    source_id: str | None
    date: dt.date
    starts_at: dt.datetime
    ends_at: dt.datetime
    time_label: str
    discipline: str
    discipline_id: str | None
    kind: str
    badge: str
    room: str | None
    building: str | None
    building_short: str | None
    teacher: str | None
    stream: tuple[str, ...] = ()
    """Чужие группы на этой же паре: поточная лекция читается сразу двум.

    Пустой кортеж — пара только для нас.
    """
    dedup_index: int
    position: int
    """Порядковый номер пары внутри дня.

    В API номера пары нет — восстанавливаем сортировкой по времени начала.
    Для ключа диффа не годится (сдвинется у всех при вставке пары в начало
    дня), поэтому используется только для подписи.
    """

    @property
    def is_exam(self) -> bool:
        return self.kind in EXAM_KINDS

    @property
    def is_retake(self) -> bool:
        """Пересдача идёт для должников, не для группы: в её пары и выходные она не входит."""
        return self.kind.strip().lower() == RETAKE_KIND

    @property
    def slot(self) -> str:
        """Метка слота для группировки подгрупп: одинакова у пар в одно время."""
        return self.starts_at.strftime("%H:%M")


class SnapshotMeta(BaseModel):
    """Шапка снимка без содержимого."""

    id: int
    taken_at: dt.datetime
    status: SnapshotStatus
    source: SnapshotSource = "site"
    error: str | None = None
    covered_from: dt.date | None = None
    covered_to: dt.date | None = None
    lesson_count: int = 0

    def covers(self, day: dt.date) -> bool:
        """Опубликован ли этот день на сайте на момент снимка.

        Отличает «пар нет» от «данных ещё нет»: дата внутри диапазона без пар —
        выходной, дата вне диапазона — расписание ещё не выложили.
        """
        if self.covered_from is None or self.covered_to is None:
            return False
        return self.covered_from <= day <= self.covered_to
