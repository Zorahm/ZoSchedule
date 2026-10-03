"""HTML картинки «Отправить куратору». Вёрстка — design/report.html из web/.

Картинка всегда светлая: её пересылают и печатают, ночная тема тут ни к чему. Рисует её
тот же рендерер, что и недельные картинки бота (рамка ``.frame``, ширина 1080).
"""

from __future__ import annotations

import datetime as dt
import html

from app.attendance.models import JournalDay, Mark, Pair, StudentRow
from app import texts
from app.render.fonts import font_css
from app.models.domain import weekday_ru

class ReportError(Exception):
    """Картинку не удалось нарисовать или отправить; текст показывается старосте."""

    def __init__(self, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


_CSS = """
*{margin:0;padding:0;box-sizing:border-box}
body{background:#f4efe6;font-family:Onest,system-ui,sans-serif;color:#1a1611}
.frame{width:1080px;padding:56px 64px;display:flex;flex-direction:column;gap:32px;background:#f4efe6}
.top{display:flex;align-items:center;justify-content:space-between}
.eyebrow{font:600 22px/28px 'JetBrains Mono',monospace;letter-spacing:.08em;color:#5c5347}
.eyebrow em{font-style:normal;text-transform:uppercase}
.pill{font-size:22px;line-height:28px;font-weight:600;padding:6px 16px;border:1px solid #8a7f70;border-radius:999px}
h1{font-family:Unbounded,'Arial Black',sans-serif;font-size:96px;line-height:100px;font-weight:700;letter-spacing:-.02em}
.sub{font-size:30px;line-height:40px;color:#5c5347;margin-top:12px}
.sub b{font-family:'JetBrains Mono',monospace;font-size:28px;font-weight:400;color:#1a1611}
.names{display:flex;flex-wrap:wrap;gap:12px 14px}
.nm{display:flex;align-items:center;gap:12px;padding:8px 18px 8px 8px;background:#fbf8f2;border:1px solid #d9cfbf;
  border-radius:999px;font-size:25px;line-height:30px;font-weight:600}
.nm b{display:grid;place-items:center;min-width:34px;height:34px;padding:0 8px;border-radius:999px;
  background:#1a1611;color:#f4efe6;font:700 19px/1 'JetBrains Mono',monospace}
table{width:100%;border-collapse:separate;border-spacing:0;table-layout:fixed;background:#fbf8f2;
  border:1px solid #d9cfbf;border-radius:10px;overflow:hidden}
th,td{padding:0;text-align:center;font-weight:inherit}
col.n{width:52px}col.who{width:372px}
thead th{vertical-align:bottom;padding:16px 6px 14px;border-bottom:2px solid #1a1611;background:#fbf8f2}
thead .t{font:700 28px/32px 'JetBrains Mono',monospace}
thead .p{margin-top:2px;font:600 16px/22px 'JetBrains Mono',monospace;letter-spacing:.08em;text-transform:uppercase;color:#5c5347}
thead .num,thead .name{text-align:left;font:600 18px/22px 'JetBrains Mono',monospace;letter-spacing:.08em;
  text-transform:uppercase;color:#5c5347}
thead .num{padding-left:18px}
tbody td,tbody th{height:44px;border-bottom:1px solid #e6ddce}
tbody tr:nth-child(even) td,tbody tr:nth-child(even) th{background:#f7f2e9}
tbody .num{font:500 17px/1 'JetBrains Mono',monospace;color:#8a7f70;text-align:left;padding-left:18px}
tbody .name{text-align:left;font-size:24px;line-height:28px;font-weight:500;white-space:nowrap;padding-right:6px}
.m{display:inline-grid;place-items:center;width:34px;height:34px;border-radius:8px;
  font:700 20px/1 'JetBrains Mono',monospace;vertical-align:middle}
.m.p{background:#d8ecd5;color:#21502a;border:1.5px solid #a9d2a5}
.m.n{background:#e0442a;color:#fff}
.m.e{border:1.5px dashed #b6ab99;color:#b6ab99;font-weight:500}
.gone .name{color:#8c2313}
tfoot td,tfoot th{height:64px;border-top:2px solid #1a1611;background:#fbf8f2;vertical-align:middle}
tfoot th{text-align:left;padding-left:18px;font:600 17px/22px 'JetBrains Mono',monospace;letter-spacing:.08em;
  text-transform:uppercase;color:#5c5347}
tfoot .s{font:700 30px/1 'JetBrains Mono',monospace}
tfoot .s.z{color:#8a7f70;font-weight:500}
tfoot small{display:block;margin-top:3px;font:500 15px/18px 'JetBrains Mono',monospace;color:#8c2313}
.legend{display:flex;flex-wrap:wrap;align-items:center;gap:14px 28px;font-size:22px;line-height:28px;color:#5c5347}
.legend>span{display:flex;align-items:center;gap:10px}
.legend .m{width:30px;height:30px;font-size:18px;border-radius:7px}
.legend b{margin-left:auto;font:400 24px/28px 'JetBrains Mono',monospace;color:#1a1611}
.foot{display:flex;align-items:center;justify-content:space-between;padding-top:24px;border-top:1px solid #d9cfbf}
.logo{font-family:Unbounded,'Arial Black',sans-serif;font-size:30px;line-height:36px;font-weight:700}
.logo span{color:#ff4d2e}
.upd{font-size:22px;line-height:28px;color:#5c5347}
"""

_LETTER: dict[str, tuple[str, str]] = {"present": ("p", "П"), "absent": ("n", "Н")}
_EMPTY = ("e", "–")


def _esc(value: str) -> str:
    return html.escape(value, quote=True)


def _mark(mark: Mark | None) -> str:
    cls, letter = _LETTER.get(mark, _EMPTY) if mark else _EMPTY
    return f'<span class="m {cls}">{letter}</span>'


def _students_word(count: int) -> str:
    return texts.plural(count, ("студент", "студента", "студентов"))


def _pairs_word(count: int) -> str:
    return texts.plural(count, ("пара", "пары", "пар"))


def _head(pairs: list[Pair]) -> str:
    cells = "".join(
        f'<th><div class="t">{_esc(pair.start)}</div><div class="p">{pair.number} пара</div></th>'
        for pair in pairs
    )
    return (
        "<thead><tr>"
        '<th class="num">№</th><th class="name">ФИО</th>'
        f"{cells}</tr></thead>"
    )


def _row(index: int, student: StudentRow, pairs: list[Pair]) -> str:
    marks: list[Mark | None] = [student.marks.get(pair.slot) for pair in pairs]
    gone = ' class="gone"' if marks and all(mark == "absent" for mark in marks) else ""
    cells = "".join(f"<td>{_mark(mark)}</td>" for mark in marks)
    return f'<tr{gone}><th class="num">{index}</th><th class="name">{_esc(student.name)}</th>{cells}</tr>'


def _foot(day: JournalDay) -> str:
    cells: list[str] = []
    for pair in day.pairs:
        absent = sum(1 for s in day.students if s.marks.get(pair.slot) == "absent")
        blank = sum(1 for s in day.students if pair.slot not in s.marks)
        number = f'<div class="s{"" if absent else " z"}">{absent or "—"}</div>'
        note = f"<small>не отмечено {blank}</small>" if blank else ""
        cells.append(f"<td>{number}{note}</td>")
    return f'<tfoot><tr><th colspan="2">Отсутствуют</th>{"".join(cells)}</tr></tfoot>'


def _totals(day: JournalDay) -> tuple[int, int]:
    absent = sum(1 for s in day.students for m in s.marks.values() if m == "absent")
    blank = sum(len(day.pairs) - len(s.marks) for s in day.students)
    return absent, blank


def report_html(group: str, day: JournalDay, *, titles: bool, sent_at: dt.datetime) -> str:
    """Таблица дня целиком. ``titles`` — подписать пары названиями (куратору не всегда нужны)."""
    absent, blank = _totals(day)
    names = ""
    if titles:
        chips = "".join(
            f'<div class="nm"><b>{pair.number}</b>{_esc(pair.title)}</div>' for pair in day.pairs
        )
        names = f'<div class="names">{chips}</div>'
    table = (
        '<table><colgroup><col class="n"><col class="who">'
        f"{'<col>' * len(day.pairs)}</colgroup>{_head(day.pairs)}"
        f"<tbody>{''.join(_row(i, s, day.pairs) for i, s in enumerate(day.students, 1))}</tbody>"
        f"{_foot(day)}</table>"
    )
    summary = f"Н всего: {absent}" + (f" · не отмечено: {blank}" if blank else "")
    body = (
        f'<div class="top"><div class="eyebrow">{_esc(group)} · <em>посещаемость</em></div>'
        '<div class="pill">Для куратора</div></div>'
        f'<div><h1>{weekday_ru(day.date)}</h1><div class="sub">{texts.date_long(day.date)} {day.date.year}'
        f" · <b>{_students_word(len(day.students))}, {_pairs_word(len(day.pairs))}</b></div></div>"
        f"{names}{table}"
        '<div class="legend">'
        f'<span>{_mark("present")}присутствует</span><span>{_mark("absent")}отсутствует</span>'
        f"<span>{_mark(None)}не отмечено</span><b>{summary}</b></div>"
        '<div class="foot"><div class="logo">Zo<span>/</span>Schedule</div>'
        f'<div class="upd">Отправлено {texts.date_short(sent_at.date())}, {sent_at:%H:%M}</div></div>'
    )
    return (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        f"<style>{font_css()}{_CSS}</style></head>"
        f'<body><div class="frame">{body}</div></body></html>'
    )


def file_name(day: dt.date) -> str:
    return f"attendance-{day.isoformat()}.png"
