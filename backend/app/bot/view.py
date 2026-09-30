"""What the images show: lessons reduced to plain display values.

Templates never see domain models, only these. All "now" logic stays out: an
image is a picture of the latest snapshot, nothing on it depends on the clock.
"""

from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from app.bot import texts
from app.models.domain import Lesson, SnapshotMeta

Coverage = Literal["published", "unpublished"]
Tone = Literal["blue", "green", "red", "gray"]
"""Colour of a lesson's kind badge."""

_TONE_BY_KIND: dict[str, Tone] = {
    "лекция": "blue",
    "семинар": "green",
    "лабораторный практикум": "green",
    "практическое занятие": "green",
    "экзамен": "red",
    "зачет": "red",
    "пересдача": "red",
}

WEEK_DAYS = 6
"""The college week is Monday to Saturday; there is no Sunday column."""


@dataclass(frozen=True, slots=True)
class StreamGroup:
    """A group sharing the lesson: `short` for the tag, `full` for the legend."""

    short: str
    full: str


def kind_tone(kind: str) -> Tone:
    """Lectures blue, practice and labs green, exams and retakes red; anything else plain."""
    return _TONE_BY_KIND.get(kind.strip().lower().replace("ё", "е"), "gray")


@dataclass(frozen=True, slots=True)
class LessonView:
    number: int
    start: str
    end: str
    kind: str
    tone: Tone
    is_exam: bool
    title: str
    teacher: str | None
    room: str | None
    room_raw: str | None
    """The room as the site writes it, for the day picture's big number."""
    building: str | None
    stream: tuple[StreamGroup, ...]


@dataclass(frozen=True, slots=True)
class RetakeView:
    """A retake: for those who owe it, not for the group, so it is not one of its lessons."""

    start: str
    end: str
    title: str
    teacher: str | None
    room: str | None
    building: str | None = None

    def sentence(self) -> str:
        """The template line, everything the retake has to say."""
        return texts.format_retake(self.title, self.teacher, self.room)

    @property
    def room_label(self) -> str | None:
        return texts.format_room(self.room)

    @property
    def short_title(self) -> str:
        """Just the subject, for the week picture: the rest goes into the caption."""
        return texts.short_title(self.title)


@dataclass(frozen=True, slots=True)
class DayView:
    date: dt.date
    coverage: Coverage
    lessons: tuple[LessonView, ...]
    """The group's lessons only. Retakes are in `retakes`, so a day with nothing else
    has no lessons here, and everything that skips a day off skips it."""
    retakes: tuple[RetakeView, ...] = ()

    @property
    def span(self) -> str | None:
        if not self.lessons:
            return None
        return f"{self.lessons[0].start}–{self.lessons[-1].end}"

    @property
    def is_retake_only(self) -> bool:
        """A published day with only retakes on it: a day off for the group."""
        return self.coverage == "published" and not self.lessons and bool(self.retakes)


@dataclass(frozen=True, slots=True)
class Header:
    group: str
    updated: dt.date


def week_monday(day: dt.date) -> dt.date:
    return day - dt.timedelta(days=day.weekday())


_GROUP_PREFIX = re.compile(r"^(?P<prefix>.*[-–—/\s])[^-–—/\s]+$")


def _stream_group(name: str, own_group: str) -> StreamGroup:
    """`306` becomes "ОККИПд-306" when it shares our group's prefix; other names stay whole."""
    match = _GROUP_PREFIX.match(own_group)
    is_bare = re.fullmatch(r"[^-–—/\s]+", name) is not None
    if match is None or not is_bare:
        return StreamGroup(short=name, full=name)
    return StreamGroup(short=name, full=match["prefix"] + name)


def _retake_view(lesson: Lesson) -> RetakeView:
    return RetakeView(
        start=lesson.starts_at.strftime("%H:%M"),
        end=lesson.ends_at.strftime("%H:%M"),
        title=lesson.discipline,
        teacher=lesson.teacher,
        room=lesson.room,
        building=lesson.building,
    )


def _lesson_view(lesson: Lesson, group: str, number: int) -> LessonView:
    return LessonView(
        number=number,
        start=lesson.starts_at.strftime("%H:%M"),
        end=lesson.ends_at.strftime("%H:%M"),
        kind=texts.kind_label(lesson.kind),
        tone=kind_tone(lesson.kind),
        is_exam=lesson.is_exam,
        title=lesson.discipline,
        teacher=lesson.teacher,
        room=texts.format_room(lesson.room),
        room_raw=lesson.room.strip() if lesson.room and lesson.room.strip() else None,
        building=lesson.building,
        stream=tuple(_stream_group(name, group) for name in lesson.stream),
    )


def build_days(
    lessons: Sequence[Lesson],
    snapshot: SnapshotMeta | None,
    start: dt.date,
    count: int,
    *,
    group: str,
) -> list[DayView]:
    """`count` consecutive days from `start`, each with published/unpublished marked.

    An empty published day is a day off; an unpublished one means "not out yet".
    The two must never look the same.
    """
    by_date: defaultdict[dt.date, list[Lesson]] = defaultdict(list)
    for lesson in lessons:
        by_date[lesson.date].append(lesson)

    days: list[DayView] = []
    for offset in range(count):
        day = start + dt.timedelta(days=offset)
        ordered = sorted(by_date.get(day, []), key=lambda item: item.starts_at)
        regular = [item for item in ordered if not item.is_retake]
        # Counted here, not taken from `position`: a retake at 08:30 must not
        # make the group's first lesson "2 пара".
        slots = sorted({item.starts_at for item in regular})
        covered = snapshot is not None and snapshot.covers(day)
        days.append(
            DayView(
                date=day,
                coverage="published" if covered else "unpublished",
                lessons=tuple(
                    _lesson_view(item, group, slots.index(item.starts_at) + 1) for item in regular
                ),
                retakes=tuple(_retake_view(item) for item in ordered if item.is_retake),
            )
        )
    return days
