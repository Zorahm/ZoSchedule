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

from app.bot import texts
from app.bot.fonts import font_css
from app.bot.view import WEEK_DAYS, DayView, Header, LessonView, RetakeView, StreamGroup
from app.models.domain import RETAKE_KIND, weekday_ru

FRAME_SELECTOR = ".frame"
WIDTH = 1080

TITLE_MAX_LINES = 2
"""A card title longer than this many lines on the day picture is swapped for its short form."""
WEEK_NAME_MIN_PX = 20
"""A week row's name that does not fit shrinks down to this size before it is allowed to wrap."""

# Runs in the browser, once the fonts are in (see the renderer): only real layout can say
# whether a text fits. Day card titles that have a shorter form carry it in `data-short`.
# A week row's name is never cut with "…": it shrinks a little, and only as a last
# resort wraps onto a second line.
_FIT_SCRIPT = """
window.fitTitles = function () {
  document.querySelectorAll('.title[data-short]').forEach(function (el) {
    var line = parseFloat(getComputedStyle(el).lineHeight);
    if (el.getBoundingClientRect().height > line * __LINES__ + 1) {
      el.textContent = el.dataset.short;
    }
  });
  document.querySelectorAll('.ln .n').forEach(function (el) {
    var size = parseFloat(getComputedStyle(el).fontSize);
    while (el.scrollWidth > el.clientWidth + 1 && size > __MIN__) {
      size -= 1;
      el.style.fontSize = size + 'px';
    }
    if (el.scrollWidth > el.clientWidth + 1) {
      el.style.whiteSpace = 'normal';
      el.style.textOverflow = 'clip';
    }
  });
};
""".replace("__LINES__", str(TITLE_MAX_LINES)).replace("__MIN__", str(WEEK_NAME_MIN_PX))

_CSS = """
*{margin:0;padding:0;box-sizing:border-box}
body{background:#f4efe6;font-family:Onest,system-ui,sans-serif}
.frame{width:1080px;min-height:var(--h);padding:64px;display:flex;flex-direction:column;
  gap:var(--gap);background:#f4efe6;color:#1a1611;overflow:hidden}
.mono{font-family:'JetBrains Mono',ui-monospace,monospace}
.top{display:flex;align-items:center;justify-content:space-between}
.eyebrow{font-size:22px;line-height:28px;font-weight:600;letter-spacing:.08em;
  text-transform:uppercase;color:#5c5347}
.pill{font-size:22px;line-height:28px;font-weight:600;padding:6px 16px;
  border:1px solid #8a7f70;border-radius:999px}
h1{font-family:Unbounded,'Arial Black',sans-serif;font-size:96px;line-height:100px;
  font-weight:700;letter-spacing:-.02em}
.sub{font-size:30px;line-height:40px;color:#5c5347}
.sub b{font-family:'JetBrains Mono',monospace;font-size:28px;font-weight:400;color:#1a1611}
.strip{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:12px}
.chip{display:flex;align-items:center;justify-content:center;gap:8px;height:56px;
  border-radius:999px;border:1px solid #8a7f70;font-size:22px;font-weight:600;
  letter-spacing:.04em;font-family:'JetBrains Mono',monospace}
.chip span:first-child{text-transform:uppercase}
.chip span:last-child{font-weight:400}
.chip.on{background:#ff4d2e;border-color:#ff4d2e}
.list{display:flex;flex-direction:column;gap:16px;flex-grow:1}
.card{display:grid;grid-template-columns:168px minmax(0,1fr) auto;background:#fbf8f2;
  border:1px solid #d9cfbf;border-radius:10px;padding:24px 28px;flex-grow:1;align-items:center}
.time{display:flex;flex-direction:column;gap:6px;padding-right:24px;
  border-right:1px solid #d9cfbf;align-self:stretch;justify-content:center;
  font-family:'JetBrains Mono',monospace}
.time .s{font-size:36px;line-height:40px;font-weight:600}
.time .e{font-size:24px;line-height:28px;color:#5c5347}
.body{display:flex;flex-direction:column;gap:10px;padding-left:28px;min-width:0}
.tags{display:flex;align-items:center;gap:12px;font-family:'JetBrains Mono',monospace;
  font-size:20px;line-height:24px;font-weight:600;letter-spacing:.08em;
  text-transform:uppercase;color:#5c5347}
.tag{font-size:18px;line-height:20px;padding:3px 10px;border:1px solid #8a7f70;
  border-radius:4px;color:#1a1611}
.badge{font-family:'JetBrains Mono',monospace;font-size:18px;line-height:20px;font-weight:600;
  letter-spacing:.08em;text-transform:uppercase;padding:5px 12px;border-radius:4px}
.badge.blue{background:#d9e6fa;color:#1d3f75}
.badge.green{background:#d8ecd5;color:#21502a}
.badge.red{background:#f8d2ca;color:#8c2313}
.badge.gray{background:#ece5d8;color:#5c5347}
.time .pn{margin-top:10px;font-size:18px;line-height:22px;letter-spacing:.08em;
  text-transform:uppercase;color:#5c5347}
.place{display:flex;flex-direction:column;gap:4px;padding-left:28px;width:238px;
  border-left:1px solid #d9cfbf;align-self:stretch;justify-content:center}
.place .lbl,.place .bld{font-size:20px;line-height:26px;color:#5c5347}
.place .num{font-size:44px;line-height:48px;font-weight:700;overflow-wrap:anywhere}
.tag.stream{border-style:dashed}
.legend{display:flex;flex-direction:column;gap:8px;font-size:22px;line-height:28px;
  color:#5c5347}
.legend .tag{margin-right:10px;font-family:'JetBrains Mono',monospace;font-weight:600}
.title{font-family:Unbounded,'Arial Black',sans-serif;font-size:27px;line-height:34px;
  font-weight:600}
.meta{font-size:24px;line-height:30px;color:#5c5347}
.meta.unknown{font-style:italic}
.empty{flex-grow:1;display:flex;align-items:center;justify-content:center;
  background:#fbf8f2;border:1px dashed #d9cfbf;border-radius:10px;font-size:34px;
  color:#5c5347}
.foot{display:flex;align-items:center;justify-content:space-between;padding-top:24px;
  border-top:1px solid #d9cfbf}
.logo{font-family:Unbounded,'Arial Black',sans-serif;font-size:30px;line-height:36px;
  font-weight:700}
.logo span{color:#ff4d2e}
.upd{font-size:22px;line-height:28px;color:#5c5347}
.days{display:flex;flex-direction:column;gap:12px;flex-grow:1}
.row{display:grid;grid-template-columns:164px minmax(0,1fr);background:#fbf8f2;
  border:1px solid #d9cfbf;border-radius:10px;padding:20px 24px;align-items:center}
.who{display:flex;flex-direction:column;align-items:flex-start;gap:6px;padding-right:12px;
  border-right:1px solid #d9cfbf;align-self:stretch;justify-content:center}
.dow{font-family:Onest,system-ui,sans-serif;font-size:76px;line-height:72px;font-weight:800;
  letter-spacing:-.02em;text-transform:uppercase}
.dat{font-size:22px;line-height:28px;color:#5c5347}
.ls{display:flex;flex-direction:column;gap:10px;padding-left:16px;min-width:0}
.ln{display:grid;grid-template-columns:84px minmax(0,1fr) auto;gap:12px;align-items:baseline}
.ln .t{font-family:'JetBrains Mono',monospace;font-size:24px;line-height:30px;font-weight:600}
.ln .n{font-size:26px;line-height:30px;font-weight:500;white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis}
.ln .r{font-family:'JetBrains Mono',monospace;font-size:20px;line-height:30px;color:#5c5347}
.ln.exam .t{color:#ff4d2e}
.none{font-size:24px;line-height:30px;color:#5c5347}
.tag.retake{background:#ff4d2e;border-color:#ff4d2e}
.card.retake{flex-grow:0;background:#fff1ec;border-color:#ff4d2e}
.card.retake .title{font-size:24px;line-height:32px}
.ln.retake{grid-template-columns:84px auto minmax(0,1fr) auto;gap:12px;align-items:center;
  background:#fde9e3;border-radius:10px;padding:8px 14px;margin:0 -14px}
.ln.retake .t{color:#ff4d2e}
.ln.retake .tag{font-family:'JetBrains Mono',monospace;font-weight:600;letter-spacing:.06em;
  font-size:16px;line-height:18px;padding:4px 8px}
"""


def _esc(value: str) -> str:
    return html.escape(value, quote=True)


def _page(body: str, *, height: int, gap: int) -> str:
    return (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        f"<style>{font_css()}{_CSS}</style></head>"
        f'<body><div class="frame" style="--h:{height}px;--gap:{gap}px">{body}</div>'
        f"<script>{_FIT_SCRIPT}</script></body></html>"
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
    return (
        '<div class="card">'
        f'<div class="time"><div class="s">{lesson.start}</div><div class="e">{lesson.end}</div>'
        f'<div class="pn">{lesson.number} пара</div></div>'
        '<div class="body">'
        f'<div class="tags"><span class="badge {lesson.tone}">{_esc(lesson.kind)}</span>{stream}</div>'
        f"{_title(lesson.title)}{_meta(lesson.teacher)}</div>"
        f"{_place(lesson.room_raw, lesson.building)}</div>"
    )


def _strip(days: Sequence[DayView], active: dt.date) -> str:
    chips = "".join(
        f'<div class="chip{" on" if day.date == active else ""}">'
        f"<span>{texts.WEEKDAYS_SHORT[day.date.weekday()]}</span><span>{day.date.day}</span></div>"
        for day in days
    )
    return f'<div class="strip">{chips}</div>'


def _empty_label(day: DayView) -> str:
    return texts.NO_LESSONS if day.coverage == "published" else texts.NOT_PUBLISHED


def _day_empty(day: DayView) -> str:
    return f'<div class="empty">{_empty_label(day)}</div>'


def _retake_card(retake: RetakeView) -> str:
    return (
        '<div class="card retake">'
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


def day_html(day: DayView, week: Sequence[DayView], header: Header) -> str:
    """The "today" image. `week` supplies the Mon–Sat strip with `day` highlighted."""
    sub = f"{texts.date_long(day.date)}"
    if day.lessons:
        sub += f' · <b>{texts.lessons_count(len(day.lessons))} · {day.span}</b>'
    cards = "".join(_card(lesson) for lesson in day.lessons) or _day_empty(day)
    retakes = "".join(_retake_card(retake) for retake in day.retakes)
    body = (
        f"{_top(header)}"
        f'<div style="display:flex;flex-direction:column;gap:12px"><h1>{weekday_ru(day.date)}</h1>'
        f'<div class="sub">{sub}</div></div>'
        f"{_strip(week, day.date)}"
        f'<div class="list">{cards}{retakes}</div>'
        f"{_legend(day)}"
        f"{_footer(header)}"
    )
    return _page(body, height=1350, gap=32)


def _week_lesson(lesson: LessonView) -> str:
    # Shortened here only: a week row has one line per lesson. The day picture wraps
    # the full title onto a second line, and there it reads better in full.
    exam = " exam" if lesson.is_exam else ""
    room = _esc(lesson.room or "")
    return (
        f'<div class="ln{exam}"><div class="t">{lesson.start}</div>'
        f'<div class="n">{_esc(texts.short_title(lesson.title))}</div>'
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
    return (
        f'<div class="row" style="flex-grow:{weight}">'
        f'<div class="who"><div class="dow">{texts.WEEKDAYS_SHORT[day.date.weekday()]}</div>'
        f'<div class="dat">{texts.date_long(day.date)}</div></div>'
        f'<div class="ls">{content}</div></div>'
    )


def week_html(days: Sequence[DayView], header: Header) -> str:
    """The pinned "week" image: Monday to Saturday, one row per day."""
    assert len(days) == WEEK_DAYS
    total = sum(len(day.lessons) for day in days)
    first, last = days[0].date, days[-1].date
    body = (
        f"{_top(header)}"
        '<div style="display:flex;flex-direction:column;gap:12px"><h1>Неделя</h1>'
        f'<div class="sub">{texts.range_long(first, last)} · <b>{texts.lessons_count(total)}</b></div></div>'
        f'<div class="days">{"".join(_week_row(day) for day in days)}</div>'
        f"{_footer(header)}"
    )
    return _page(body, height=1600, gap=28)
