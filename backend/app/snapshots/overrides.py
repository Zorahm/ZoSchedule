"""Правки куратора поверх расписания сайта: пока что только аудитория пары.

Правка живёт отдельно от снимков и накладывается на пары при сохранении каждого
нового снимка сайта. Сырой ответ парсера при этом не трогается, а снимки остаются
целыми: «что отдал сайт» и «что показываем» расходятся только в разобранных парах.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.models.domain import Lesson
from app.parsing.curator import DayHint

_LOOKAHEAD_DAYS = 8
"""Как далеко вперёд ищется пара, если куратор не написал «завтра» и т. п."""

_HINT_OFFSET: dict[DayHint, int] = {"today": 0, "tomorrow": 1, "day_after": 2}


@dataclass(frozen=True, slots=True)
class RoomOverride:
    day: dt.date
    start: str
    """Начало пары, «ЧЧ:ММ» (как `Lesson.slot`)."""
    room: str


def apply_room_overrides(lessons: Sequence[Lesson], overrides: Iterable[RoomOverride]) -> list[Lesson]:
    by_slot = {(item.day, item.start): item.room for item in overrides}
    return [
        lesson.model_copy(update={"room": by_slot[(lesson.date, lesson.slot)]})
        if (lesson.date, lesson.slot) in by_slot and not lesson.is_retake
        else lesson
        for lesson in lessons
    ]


def locate(
    lessons: Sequence[Lesson], *, start: str, now: dt.datetime, day_hint: DayHint | None
) -> tuple[dt.date, list[Lesson]] | None:
    """Ближайшая пара, начинающаяся в `start`: её день и все пары этого слота.

    Куратор пишет про ближайшую пару: сегодняшняя, если она ещё не закончилась,
    иначе следующая такая же. Пересдачи в расчёт не входят: она идёт для должников,
    не для группы.
    """
    today = now.date()
    offsets = (
        [_HINT_OFFSET[day_hint]] if day_hint is not None else list(range(_LOOKAHEAD_DAYS))
    )
    for offset in offsets:
        day = today + dt.timedelta(days=offset)
        found = [
            lesson
            for lesson in lessons
            if lesson.date == day
            and lesson.slot == start
            and not lesson.is_retake
            and (day != today or now < lesson.ends_at)
        ]
        if found:
            return (day, found)
    return None
