"""HTML for the two images. Layout and tokens come from the design file
"Расписание для Telegram" (day 1080x1350, week 1080x1600, light theme).

Both frames have a minimum height and grow with content, so a day with six long
lessons is never clipped.
"""

from __future__ import annotations

import datetime as dt
import html
import re
from collections.abc import Sequence

from app import texts
from app.render.fonts import font_css
from app.render.night import NIGHT_CSS
from app.render.styles import CSS, FIT_SCRIPT
from app.render.theme import Theme
from app.render.view import WEEK_DAYS, DayView, Header, LessonView, RetakeView, StreamGroup, Tone
from app.models.domain import RETAKE_KIND, weekday_ru

def _esc(value: str) -> str:
    return html.escape(value, quote=True)


def _page(body: str, *, height: int, gap: int, theme: Theme) -> str:
    night = theme == "night"
    return (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        f"<style>{font_css()}{CSS}{NIGHT_CSS if night else ''}</style></head>"
        f'<body><div class="frame{" night" if night else ""}" style="--h:{height}px;--gap:{gap}px">{body}</div>'
        f"<script>{FIT_SCRIPT}</script></body></html>"
    )


def _top(header: Header) -> str:
    return (
        '<div class="top"><div class="eyebrow mono">Расписание занятий</div>'
        f'<div class="pill mono">{_esc(header.group)}</div></div>'
    )


def _footer(header: Header) -> str:
    return (
        '<div class="foot"><div class="logo">Z<span>\\</span>M</div>'
        f'<div class="upd">Обновлено {texts.date_long(header.updated)}</div></div>'
    )


def _title(title: str) -> str:
    """The card title; its short form rides along only when there is one."""
    short = texts.short_title(title)
    attr = f' data-short="{_esc(short)}"' if short and short != title else ""
    return f'<div class="title"{attr}>{_esc(title)}</div>'


def _meta(teacher: str | None) -> str:
    unknown = " unknown" if teacher is None else ""
    return f'<div class="meta{unknown}">{_esc(teacher or texts.TEACHER_UNKNOWN)}</div>'


def _place(room: str | None, building: str | None) -> str:
    """The right column: "аудитория" over a big number, the building under it."""
    parts: list[str] = []
    if room:
        if re.search(r"\d", room):  # "Зал" is not an "аудитория"
            parts.append(f'<div class="lbl">{texts.ROOM_LABEL}</div>')
        parts.append(f'<div class="num">{_esc(room)}</div>')
    if building:
        parts.append(f'<div class="bld">{_esc(texts.BUILDING_TAG.format(building))}</div>')
    return f'<div class="place">{"".join(parts)}</div>'


def _card(lesson: LessonView) -> str:
    stream = (
        '<span class="tag stream">'
        + _esc(texts.STREAM_TAG.format(", ".join(group.short for group in lesson.stream)))
        + "</span>"
        if lesson.stream
        else ""
    )
    exam = " exam" if lesson.is_exam else ""
    return (
        f'<div class="card k-{lesson.tone}{exam}">'
        f'<div class="time"><div class="s">{lesson.start}</div><div class="e">{lesson.end}</div>'
        f'<div class="pn">{lesson.number} пара</div></div>'
        '<div class="body">'
        f'<div class="tags"><span class="badge {lesson.tone}">{_esc(lesson.kind)}</span>{stream}</div>'
        f"{_title(lesson.title)}{_meta(lesson.teacher)}</div>"
        f"{_place(lesson.room_raw, lesson.building)}</div>"
    )


def _is_day_off(day: DayView) -> bool:
    """Published and free. An unpublished day is not: "not out yet" must not read as free."""
    return day.coverage == "published" and not day.lessons


def _chip_state(day: DayView, active: dt.date) -> tuple[str, str]:
    """Class and caption of a day in the strip.

    The pictured day is black whatever it is: the strip says which day this is. An exam
    keeps its red caption there too, so it is not lost on the day itself."""
    exam = any(lesson.is_exam for lesson in day.lessons)
    if day.coverage == "unpublished":
        state, caption = " na", texts.STRIP_UNPUBLISHED
    elif exam:
        state, caption = " ex", texts.STRIP_EXAM
    elif day.lessons:
        state, caption = "", texts.STRIP_UNTIL.format(max(lesson.end for lesson in day.lessons))
    else:
        state, caption = " off", texts.STRIP_OFF
    if day.date == active:
        state = " on" + (" ex" if exam else "")
    return state, caption


def _strip(days: Sequence[DayView], active: dt.date) -> str:
    chips: list[str] = []
    for day in days:
        state, caption = _chip_state(day, active)
        chips.append(
            f'<div class="chip{state}"><div class="l">'
            f"<span>{texts.WEEKDAYS_SHORT[day.date.weekday()]}</span><span>{day.date.day}</span></div>"
            f'<div class="f">{caption}</div></div>'
        )
    return f'<div class="strip">{"".join(chips)}</div>'


def _empty_label(day: DayView) -> str:
    return texts.NO_LESSONS if day.coverage == "published" else texts.NOT_PUBLISHED


def _day_empty(day: DayView) -> str:
    # Only a day off gets the big bold word: "not published" must not look like it.
    off = " off" if _is_day_off(day) else ""
    return f'<div class="empty{off}">{_empty_label(day)}</div>'


def _retake_card(retake: RetakeView) -> str:
    return (
        '<div class="card retake k-red">'
        f'<div class="time"><div class="s">{retake.start}</div><div class="e">{retake.end}</div></div>'
        '<div class="body">'
        f'<div class="tags"><span class="badge red">{_esc(texts.kind_label(RETAKE_KIND))}</span></div>'
        f"{_title(retake.title)}{_meta(retake.teacher)}</div>"
        f"{_place(retake.room.strip() if retake.room else None, retake.building)}</div>"
    )


def _legend(day: DayView) -> str:
    """Explains every stream tag on the page; empty when no lesson has one."""
    seen: dict[str, StreamGroup] = {}
    for lesson in day.lessons:
        for group in lesson.stream:
            seen.setdefault(group.short, group)
    lines = "".join(
        f'<div><span class="tag stream">{_esc(texts.STREAM_TAG.format(group.short))}</span>'
        f"— {_esc(texts.STREAM_LEGEND.format(group.full))}</div>"
        for group in seen.values()
    )
    return f'<div class="legend">{lines}</div>' if lines else ""


def day_html(
    day: DayView, week: Sequence[DayView], header: Header, theme: Theme = "light"
) -> str:
    """The "today" image. `week` supplies the Mon–Sat strip with `day` highlighted."""
    sub = f"{texts.date_long(day.date)}"
    if day.lessons:
        sub += f' · <b>{texts.lessons_count(len(day.lessons))} · {day.span}</b>'
    retakes = "".join(_retake_card(retake) for retake in day.retakes)
    if day.lessons:
        content = "".join(_card(lesson) for lesson in day.lessons) + retakes
    else:
        content = retakes + _day_empty(day)  # on a free day the retake is the news: it goes first
    body = (
        f"{_top(header)}"
        f'<div style="display:flex;flex-direction:column;gap:12px"><h1>{weekday_ru(day.date)}</h1>'
        f'<div class="sub">{sub}</div></div>'
        f"{_strip(week, day.date)}"
        f'<div class="list">{content}</div>'
        f"{_legend(day)}"
        f"{_footer(header)}"
    )
    return _page(body, height=1350, gap=32, theme=theme)


def _week_lesson(lesson: LessonView) -> str:
    # Shortened here only: a week row has one line per lesson. The day picture wraps
    # the full title onto a second line, and there it reads better in full.
    exam = " exam" if lesson.is_exam else ""
    room = _esc(lesson.room or "")
    return (
        f'<div class="ln{exam}"><div class="t">{lesson.start}</div>'
        # The kind's dot is a pseudo-element: the name's text and width checks stay as they were.
        f'<div class="n kd k-{lesson.tone}">{_esc(texts.short_title(lesson.title))}</div>'
        f'<div class="r">{room}</div></div>'
    )


def _week_retake(retake: RetakeView) -> str:
    # The subject and the room, like a lesson; the teacher and the rest are in the caption.
    return (
        f'<div class="ln retake"><div class="t">{retake.start}</div>'
        f'<span class="tag retake">{texts.RETAKE_TAG}</span>'
        f'<div class="n">{_esc(retake.short_title)}</div>'
        f'<div class="r">{_esc(retake.room_label or "")}</div></div>'
    )


def _week_row(day: DayView) -> str:
    if day.lessons:
        content = "".join(_week_lesson(lesson) for lesson in day.lessons)
    else:
        content = f'<div class="none">{_empty_label(day)}</div>'
    content += "".join(_week_retake(retake) for retake in day.retakes)
    weight = len(day.lessons) + len(day.retakes) or 1
    off = " off" if _is_day_off(day) else ""
    return (
        f'<div class="row{off}" style="flex-grow:{weight}">'
        # The day off's green sits inside the column: the column keeps its width and divider.
        f'<div class="who"><div class="wd"><div class="dow">{texts.WEEKDAYS_SHORT[day.date.weekday()]}</div>'
        f'<div class="dat">{texts.date_long(day.date)}</div></div></div>'
        f'<div class="ls">{content}</div></div>'
    )


_TONE_ORDER: tuple[Tone, ...] = ("blue", "green", "red", "gray")


def _kinds_legend(days: Sequence[DayView]) -> str:
    """What each dot colour means: only the kinds that are on this week."""
    kinds: dict[Tone, list[str]] = {}
    for day in days:
        for lesson in day.lessons:
            names = kinds.setdefault(lesson.tone, [])
            if lesson.kind not in names:
                names.append(lesson.kind)
    items = "".join(
        f'<span><i class="dot k-{tone}"></i>{_esc(", ".join(kinds[tone]))}</span>'
        for tone in _TONE_ORDER
        if tone in kinds
    )
    return f'<div class="kinds">{items}</div>' if items else ""


def week_html(days: Sequence[DayView], header: Header, theme: Theme = "light") -> str:
    """The pinned "week" image: Monday to Saturday, one row per day."""
    assert len(days) == WEEK_DAYS
    total = sum(len(day.lessons) for day in days)
    first, last = days[0].date, days[-1].date
    body = (
        f"{_top(header)}"
        '<div style="display:flex;flex-direction:column;gap:12px"><h1>Неделя</h1>'
        f'<div class="sub">{texts.range_long(first, last)} · <b>{texts.lessons_count(total)}</b></div></div>'
        f'<div class="days">{"".join(_week_row(day) for day in days)}</div>'
        f"{_kinds_legend(days)}"
        f"{_footer(header)}"
    )
    return _page(body, height=1600, gap=28, theme=theme)
