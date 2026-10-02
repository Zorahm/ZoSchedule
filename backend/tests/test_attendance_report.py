"""Картинка для куратора: что в ней написано и чего не должно быть."""

from __future__ import annotations

import datetime as dt
import re

from app import moscow
from app.attendance.models import JournalDay, Mark, Pair, StudentRow
from app.attendance.report import file_name, report_html

DAY = dt.date(2026, 10, 2)
SENT = dt.datetime(2026, 10, 2, 15, 42, tzinfo=moscow.MOSCOW)


def _pair(number: int, start: str, title: str) -> Pair:
    return Pair(slot=start, number=number, start=start, end="10:00", title=title, kind="Лекция",
                teacher="Орлов П. П.", room="210", lessons=1)


def _day(*students: tuple[str, dict[str, Mark]]) -> JournalDay:
    return JournalDay(
        date=DAY, state="open", opens_at=None,
        pairs=[_pair(1, "08:30", "Математический анализ"), _pair(2, "10:10", "Веб-программирование")],
        students=[StudentRow(id=i, name=n, marks=m) for i, (n, m) in enumerate(students, 1)],
    )


def _html(day: JournalDay, *, titles: bool = True) -> str:
    """Страница без встроенных шрифтов: в их base64 случайно встречается что угодно."""
    page = report_html("ОККИПд-307", day, titles=titles, sent_at=SENT)
    return re.sub(r"@font-face\{[^}]*\}", "", page)


def test_the_header_names_the_day_the_group_and_the_headcount() -> None:
    page = _html(_day(("Абрамов Илья Сергеевич", {}), ("Баранова Алина Игоревна", {})))

    assert "<h1>Пятница</h1>" in page and "2 октября 2026" in page
    assert "ОККИПд-307" in page and "2 студента, 2 пары" in page
    assert "Отправлено 2 окт, 15:42" in page


def test_pair_names_are_there_only_when_asked_and_without_teachers_or_rooms() -> None:
    day = _day(("Абрамов Илья", {}))

    with_titles, without = _html(day), _html(day, titles=False)

    assert "Математический анализ" in with_titles and "Веб-программирование" in with_titles
    assert "Математический анализ" not in without and 'class="names"' not in without
    assert "08:30" in without and "1 пара" in without  # время и номер остаются всегда
    assert "Орлов" not in with_titles and "210" not in with_titles


def test_full_names_are_written_whole() -> None:
    page = _html(_day(("Тихонова Елизавета Максимовна", {})))

    assert "Тихонова Елизавета Максимовна" in page


def test_marks_absences_and_blanks_are_told_apart_and_counted() -> None:
    page = _html(_day(
        ("Абрамов Илья", {"08:30": "present", "10:10": "absent"}),
        ("Баранова Алина", {"08:30": "absent"}),  # на второй паре не отмечена
    ))

    assert page.count('class="m n">Н') == 2 + 1  # две Н в таблице и образец в легенде
    assert page.count('class="m e">–') == 1 + 1
    assert "Н всего: 2 · не отмечено: 1" in page
    assert "не отмечено 1" in page  # под столбцом второй пары


def test_a_complete_day_has_no_blank_warning() -> None:
    page = _html(_day(("Абрамов Илья", {"08:30": "present", "10:10": "present"})))

    assert "не отмечено:" not in page and "Н всего: 0" in page


def test_someone_absent_all_day_is_picked_out() -> None:
    page = _html(_day(("Абрамов Илья", {"08:30": "absent", "10:10": "absent"}),
                      ("Баранова Алина", {"08:30": "absent", "10:10": "present"})))

    assert page.count('<tr class="gone">') == 1


def test_names_from_the_roster_cannot_inject_markup() -> None:
    page = _html(_day(('<img src=x onerror="alert(1)"> Иванов', {})))

    assert "<img" not in page and "&lt;img" in page


def test_the_file_name_has_the_date_and_is_plain_ascii() -> None:
    assert file_name(DAY) == "attendance-2026-10-02.png"


def test_the_legend_style_does_not_leak_onto_the_sample_squares() -> None:
    # `.legend span` целился и во вложенный квадратик «П/Н/–», превращал его во flex, и буква
    # уезжала к левому краю. Правило должно касаться только прямых потомков легенды.
    page = report_html("г", _day(("Абрамов Илья", {})), titles=False, sent_at=SENT)

    assert ".legend>span{" in page and ".legend span{" not in page
