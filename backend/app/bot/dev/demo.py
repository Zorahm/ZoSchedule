"""Synthetic schedule for trying the bot on a scratch database.

Never touches the real database: the CLI refuses to run these against it.
"""

from __future__ import annotations

import datetime as dt

from app import moscow
from app.models.domain import Lesson

_SLOTS = (("08:30", "10:00"), ("10:10", "11:40"), ("12:10", "13:40"), ("13:50", "15:20"))

# (title, kind, room, teacher, stream)
_Subject = tuple[str, str, str, str | None, tuple[str, ...]]

_MATH: _Subject = ("Математический анализ", "лекция", "210", "Орлов П. П.", ("306",))
_WEB: _Subject = ("Веб-программирование", "практическое занятие", "305", "Петров А. А.", ())
_SPORT: _Subject = ("Физкультура", "практическое занятие", "Зал", "Кузнецов А. А.", ())
_DB: _Subject = ("Базы данных", "лабораторный практикум", "118", "Смирнов А. В.", ())
_LANG: _Subject = (
    "Иностранный язык в профессиональной деятельности",
    "практическое занятие",
    "402",
    "Сидорова Н. В.",
    (),
)
_OS: _Subject = ("Операционные системы", "лекция", "305", None, ("306",))
_DESIGN: _Subject = ("Проектирование и дизайн информационных систем", "лекция", "214", "Иванов И. И.", ())
_RETAKE: _Subject = ("Компьютерные сети", "пересдача", "308", "Радонежская Н. В.", ())

# Monday..Saturday, subjects in slot order. Monday has a retake beside its lessons
# and Thursday only a retake (see _RETAKE_DAYS), so for the group Thursday is a day
# off; Friday is a plain day off.
_WEEK: tuple[tuple[_Subject, ...], ...] = (
    (_MATH, _WEB, _SPORT),
    (_DB, _LANG, _MATH, _OS),
    (_DESIGN, _WEB, _DB, _LANG),
    (),
    (),
    (_DESIGN, _LANG),
)

# weekday -> (retake, start, end): a retake outside the slot grid.
_RETAKE_DAYS: dict[int, tuple[_Subject, str, str]] = {
    0: (_RETAKE, "15:30", "17:00"),
    3: (_RETAKE, "15:30", "17:00"),
}

_BADGES = {
    "лекция": "ЛЕК",
    "практическое занятие": "ПР",
    "лабораторный практикум": "ЛР",
    "пересдача": "ПЕР",
}


def _at(day: dt.date, clock: str) -> dt.datetime:
    hours, minutes = clock.split(":")
    return dt.datetime(day.year, day.month, day.day, int(hours), int(minutes), tzinfo=moscow.MOSCOW)


def _lesson(
    day: dt.date, slot: int, subject: _Subject, *, times: tuple[str, str] | None = None
) -> Lesson:
    title, kind, room, teacher, stream = subject
    start, end = times or _SLOTS[slot]
    return Lesson(
        source_id=f"demo-{day.isoformat()}-{'retake' if times else slot}",
        date=day,
        starts_at=_at(day, start),
        ends_at=_at(day, end),
        time_label=f"{start}-{end}",
        discipline=title,
        discipline_id=None,
        kind=kind,
        badge=_BADGES.get(kind, kind[:3].upper()),
        room=room,
        building="Сокол",
        building_short="Л-к.1",
        teacher=teacher,
        stream=stream,
        dedup_index=0,
        position=slot + 1,
    )


def _renumber(lessons: list[Lesson]) -> list[Lesson]:
    result: list[Lesson] = []
    for day in sorted({lesson.date for lesson in lessons}):
        of_day = sorted((item for item in lessons if item.date == day), key=lambda l: l.starts_at)
        slots = sorted({item.starts_at for item in of_day})
        result.extend(
            item.model_copy(update={"position": slots.index(item.starts_at) + 1}) for item in of_day
        )
    return result


def build_schedule(today: dt.date) -> list[Lesson]:
    """This week and the next, Monday to Saturday."""
    monday = today - dt.timedelta(days=today.weekday())
    lessons: list[Lesson] = []
    for week in range(2):
        for weekday, subjects in enumerate(_WEEK):
            day = monday + dt.timedelta(days=7 * week + weekday)
            lessons.extend(_lesson(day, slot, subject) for slot, subject in enumerate(subjects))
            if weekday in _RETAKE_DAYS:
                subject, start, end = _RETAKE_DAYS[weekday]
                lessons.append(_lesson(day, 0, subject, times=(start, end)))
    return lessons


def apply_changes(lessons: list[Lesson], today: dt.date) -> list[Lesson]:
    """One of each kind, on the nearest lessons from today on: move, teacher, cancel, add."""
    # Retakes are left alone: the demo changes are about the group's own lessons.
    future = sorted(
        (item for item in lessons if item.date >= today and not item.is_retake),
        key=lambda l: l.starts_at,
    )
    if len(future) < 4:
        raise ValueError("Слишком мало будущих пар для демонстрации изменений")
    moved, retaught, cancelled = future[0], future[1], future[2]

    result: list[Lesson] = []
    for item in lessons:
        if item.source_id == cancelled.source_id:
            continue
        if item.source_id == moved.source_id:
            start, end = "15:30", "17:00"
            item = item.model_copy(
                update={
                    "starts_at": _at(item.date, start),
                    "ends_at": _at(item.date, end),
                    "time_label": f"{start}-{end}",
                    "room": "412",
                }
            )
        elif item.source_id == retaught.source_id:
            item = item.model_copy(update={"teacher": "Новиков Д. Е."})
        result.append(item)

    extra = _lesson(future[3].date, 0, ("Консультация по проекту", "консультация", "220", "Иванов И. И.", ()))
    extra = extra.model_copy(
        update={
            "source_id": f"demo-extra-{extra.date.isoformat()}",
            "starts_at": _at(extra.date, "17:10"),
            "ends_at": _at(extra.date, "18:40"),
            "time_label": "17:10-18:40",
        }
    )
    return _renumber([*result, extra])
