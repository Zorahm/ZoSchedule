"""Промежуточный вид пары: разобранное сырьё до присвоения подгруппы."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass


@dataclass(slots=True)
class LessonSeed:
    """Пара, разобранная из ответа сайта, но ещё без номера подгруппы.

    Подгруппа выводится по всему дню сразу, поэтому её нельзя проставить
    в момент разбора отдельной записи.
    """

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
    group_names: list[str]
