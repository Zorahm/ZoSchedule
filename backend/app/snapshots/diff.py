"""Сравнение двух снимков. Главная ценность приложения.

На сайте колледжа изменения не видны: он показывает только текущее состояние.
Что пару перенесли, отменили или сменили преподавателя, видно исключительно
сравнением последовательных снимков — этим занят этот модуль.

В роутах и в хранилище логики сравнения нет.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import Iterable, Sequence

from app.models.changes import (
    AddedDraft,
    CancelledDraft,
    ChangeDraft,
    MovedDraft,
    TeacherChangedDraft,
)
from app.models.domain import Lesson

_FallbackKey = tuple[dt.date, str, int]


def _fold(value: str) -> str:
    return value.strip().lower().replace("ё", "е")


def _overlap(previous: Sequence[Lesson], current: Sequence[Lesson]) -> tuple[dt.date, dt.date] | None:
    """Диапазон дат, о котором высказались оба снимка.

    Сравнивать за его пределами нельзя. Сайт публикует расписание порциями и
    подчищает прошедшее: появившаяся неделя выглядела бы сплошным «added», а
    исчезнувшая — сплошным «cancelled». Ни то, ни другое не изменение
    расписания, это сдвиг горизонта публикации.
    """
    if not previous or not current:
        return None
    start = max(min(l.date for l in previous), min(l.date for l in current))
    end = min(max(l.date for l in previous), max(l.date for l in current))
    return (start, end) if start <= end else None


def _within(lessons: Iterable[Lesson], start: dt.date, end: dt.date) -> list[Lesson]:
    return [lesson for lesson in lessons if start <= lesson.date <= end]


def _by_source_id(lessons: Sequence[Lesson]) -> dict[str, Lesson]:
    """Индекс по id пары. Дубли id отбрасываем — на них полагаться нельзя."""
    seen: defaultdict[str, list[Lesson]] = defaultdict(list)
    for lesson in lessons:
        if lesson.source_id:
            seen[lesson.source_id].append(lesson)
    return {key: group[0] for key, group in seen.items() if len(group) == 1}


def _fallback_keys(lessons: Sequence[Lesson]) -> dict[_FallbackKey, Lesson]:
    """Ключ на случай, когда id перегенерировался: дата, предмет, повтор.

    Индекс повтора считается заново среди оставшихся без пары: две пары одного
    предмета подряд — обычное дело, и без него они схлопнулись бы в одну.
    """
    counters: defaultdict[tuple[dt.date, str], int] = defaultdict(int)
    index: dict[_FallbackKey, Lesson] = {}
    for lesson in sorted(lessons, key=lambda l: (l.date, l.starts_at)):
        base = (lesson.date, _fold(lesson.discipline))
        key: _FallbackKey = (*base, counters[base])
        counters[base] += 1
        index[key] = lesson
    return index


def _compare(before: Lesson, after: Lesson) -> list[ChangeDraft]:
    drafts: list[ChangeDraft] = []

    time_changed = (before.starts_at, before.ends_at) != (after.starts_at, after.ends_at)
    room_changed = before.room != after.room
    if time_changed or room_changed:
        drafts.append(
            MovedDraft(
                date=after.date,
                discipline=after.discipline,
                retake=after.is_retake,
                from_time=before.time_label,
                to_time=after.time_label,
                from_room=before.room,
                to_room=after.room,
            )
        )

    if before.teacher != after.teacher:
        drafts.append(
            TeacherChangedDraft(
                date=after.date,
                discipline=after.discipline,
                retake=after.is_retake,
                at_time=after.time_label,
                from_teacher=before.teacher,
                to_teacher=after.teacher,
            )
        )

    return drafts


def _sort_key(draft: ChangeDraft) -> tuple[dt.date, str, str]:
    time = draft.to_time if isinstance(draft, MovedDraft) else draft.at_time
    return (draft.date, time, draft.discipline)


def diff(previous: Sequence[Lesson], current: Sequence[Lesson]) -> list[ChangeDraft]:
    """Считает события изменений между предыдущим и новым снимком."""
    window = _overlap(previous, current)
    if window is None:
        return []
    start, end = window

    before = _within(previous, start, end)
    after = _within(current, start, end)

    drafts: list[ChangeDraft] = []
    matched_before: set[int] = set()
    matched_after: set[int] = set()

    before_by_id = _by_source_id(before)
    for position, lesson in enumerate(after):
        counterpart = before_by_id.get(lesson.source_id or "")
        if counterpart is None:
            continue
        matched_before.add(id(counterpart))
        matched_after.add(position)
        drafts.extend(_compare(counterpart, lesson))

    rest_before = [lesson for lesson in before if id(lesson) not in matched_before]
    rest_after = [
        lesson for position, lesson in enumerate(after) if position not in matched_after
    ]

    before_by_key = _fallback_keys(rest_before)
    after_by_key = _fallback_keys(rest_after)

    for key, lesson in after_by_key.items():
        counterpart = before_by_key.pop(key, None)
        if counterpart is None:
            drafts.append(
                AddedDraft(
                    date=lesson.date,
                    discipline=lesson.discipline,
                    retake=lesson.is_retake,
                    at_time=lesson.time_label,
                    room=lesson.room,
                    teacher=lesson.teacher,
                )
            )
            continue
        drafts.extend(_compare(counterpart, lesson))

    for lesson in before_by_key.values():
        drafts.append(
            CancelledDraft(
                date=lesson.date,
                discipline=lesson.discipline,
                retake=lesson.is_retake,
                at_time=lesson.time_label,
                room=lesson.room,
            )
        )

    return sorted(drafts, key=_sort_key)
