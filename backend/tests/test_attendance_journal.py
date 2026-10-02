"""Журнал: какой день открыт, «выходной» против «не опубликовано», приём отметок."""

from __future__ import annotations

import datetime as dt
import sqlite3

import pytest

from app import moscow
from app.attendance import journal, store
from app.attendance.models import MarkChange, RosterEntry
from app.attendance.schedule import Schedule
from app.bot import demo
from app.config import AppConfig
from app.snapshots.service import ScheduleService
from tests.fakes import save_demo

MON, TUE, WED, THU, FRI, SAT = (dt.date(2026, 9, 28) + dt.timedelta(days=i) for i in range(6))
NEXT_MON = dt.date(2026, 10, 5)
"""Демо-неделя: пн 3 пары, вт и ср по 4, чт только пересдача (выходной), пт пусто, сб 2."""


def _moment(day: dt.date, clock: str) -> dt.datetime:
    hours, minutes = clock.split(":")
    return dt.datetime(day.year, day.month, day.day, int(hours), int(minutes), tzinfo=moscow.MOSCOW)


@pytest.fixture
def schedule_conn(config: AppConfig, conn: sqlite3.Connection) -> sqlite3.Connection:
    save_demo(config, TUE)
    return conn


def _current(conn: sqlite3.Connection, day: dt.date, clock: str) -> dt.date:
    return Schedule(conn).current_day(_moment(day, clock))


def test_the_day_opens_with_its_first_pair_not_a_minute_earlier(schedule_conn: sqlite3.Connection) -> None:
    assert _current(schedule_conn, TUE, "08:29") == MON
    assert _current(schedule_conn, TUE, "08:30") == TUE


def test_the_day_stays_on_the_screen_after_the_lessons_end(schedule_conn: sqlite3.Connection) -> None:
    assert _current(schedule_conn, TUE, "23:59") == TUE
    assert _current(schedule_conn, WED, "07:00") == TUE


def test_a_day_off_does_not_take_over_the_screen(schedule_conn: sqlite3.Connection) -> None:
    # Четверг — только пересдача, пятница пуста: на экране остаётся среда, потом суббота.
    assert _current(schedule_conn, THU, "12:00") == WED
    assert _current(schedule_conn, FRI, "12:00") == WED
    assert _current(schedule_conn, SAT, "09:00") == SAT
    assert _current(schedule_conn, dt.date(2026, 10, 4), "12:00") == SAT  # воскресенье


def test_a_retake_alone_makes_a_day_off_not_a_column(schedule_conn: sqlite3.Connection) -> None:
    info = Schedule(schedule_conn).day(THU)

    assert info.pairs == () and info.state(_moment(THU, "12:00")) == "off"


def test_unpublished_is_not_a_day_off(schedule_conn: sqlite3.Connection) -> None:
    far = Schedule(schedule_conn).day(dt.date(2026, 10, 20))

    assert far.state(_moment(TUE, "09:00")) == "unpublished"
    assert Schedule(schedule_conn).day(THU).state(_moment(TUE, "09:00")) == "off"


def test_a_past_day_the_bot_never_saw_is_nodata_not_unpublished(schedule_conn: sqlite3.Connection) -> None:
    assert Schedule(schedule_conn).day(dt.date(2026, 9, 1)).state(_moment(TUE, "09:00")) == "nodata"


def test_the_next_opening_is_the_closest_locked_day(schedule_conn: sqlite3.Connection) -> None:
    now = _moment(TUE, "10:00")
    schedule = Schedule(schedule_conn)

    assert schedule.next_opening(schedule.current_day(now), now) == _moment(WED, "08:30")
    late = _moment(SAT, "12:00")
    assert Schedule(schedule_conn).next_opening(SAT, late) == _moment(NEXT_MON, "08:30")


def _lessons_with_twin(config: AppConfig) -> None:
    """Вторник с двумя занятиями в одно время: столбец один, пары в нём две."""
    lessons = demo.build_schedule(TUE)
    twin = next(item for item in lessons if item.date == TUE and item.slot == "08:30")
    lessons.append(twin.model_copy(update={"discipline": "Другое занятие", "dedup_index": 1, "teacher": None}))
    service = ScheduleService(config)
    service.prepare()
    service.store_lessons(lessons)


def test_two_lessons_in_one_slot_are_one_column_with_both_named(
    config: AppConfig, conn: sqlite3.Connection
) -> None:
    _lessons_with_twin(config)

    [first, *_] = Schedule(conn).day(TUE).pairs

    assert first.pair.lessons == 2
    assert "Другое занятие" in first.pair.title and "Базы данных" in first.pair.title


def _roster(conn: sqlite3.Connection, *names: str) -> list[store.Student]:
    return store.save_roster(conn, [RosterEntry(name=n) for n in names], now=_moment(TUE, "08:00"))


def test_marks_can_be_put_on_any_pair_of_an_open_day(schedule_conn: sqlite3.Connection) -> None:
    [a, b] = _roster(schedule_conn, "Абрамов Илья", "Баранова Алина")
    now = _moment(TUE, "09:00")

    saved = journal.save_marks(
        schedule_conn, day=TUE, by=5, now=now,
        changes=[MarkChange(student_id=a.id, slot="13:50", mark="absent"),  # пара, что ещё не началась
                 MarkChange(student_id=b.id, slot="08:30", mark="present")],
    )

    assert saved == 2
    assert store.marks_on(schedule_conn, TUE) == {(a.id, "13:50"): "absent", (b.id, "08:30"): "present"}


@pytest.mark.parametrize(
    ("day", "clock", "code_text"),
    [(WED, "09:00", "ещё не начался"), (THU, "09:00", "нет пар"),
     (dt.date(2026, 10, 20), "09:00", "не опубликовано"), (dt.date(2026, 9, 1), "09:00", "не сохранилось")],
)
def test_a_closed_day_takes_no_marks(
    schedule_conn: sqlite3.Connection, day: dt.date, clock: str, code_text: str
) -> None:
    now = _moment(TUE, clock)  # «сейчас» — вторник; закрытый день — другой
    [a] = _roster(schedule_conn, "Абрамов Илья")

    with pytest.raises(journal.JournalError, match=code_text) as raised:
        journal.save_marks(schedule_conn, day=day, by=5, now=now,
                           changes=[MarkChange(student_id=a.id, slot="08:30", mark="present")])

    assert raised.value.status == 403 and store.marks_on(schedule_conn, day) == {}


def test_a_bad_cell_refuses_the_whole_batch(schedule_conn: sqlite3.Connection) -> None:
    [a] = _roster(schedule_conn, "Абрамов Илья")
    good = MarkChange(student_id=a.id, slot="08:30", mark="present")

    with pytest.raises(journal.JournalError, match="пары в этот день нет"):
        journal.save_marks(schedule_conn, day=TUE, by=5, now=_moment(TUE, "09:00"),
                           changes=[good, MarkChange(student_id=a.id, slot="09:00", mark="present")])
    with pytest.raises(journal.JournalError, match="нет в списке"):
        journal.save_marks(schedule_conn, day=TUE, by=5, now=_moment(TUE, "09:00"),
                           changes=[good, MarkChange(student_id=999, slot="08:30", mark="present")])

    assert store.marks_on(schedule_conn, TUE) == {}


def test_the_screen_counts_what_is_marked_and_ignores_orphans(
    config: AppConfig, schedule_conn: sqlite3.Connection
) -> None:
    [a, b] = _roster(schedule_conn, "Абрамов Илья", "Баранова Алина")
    now = _moment(TUE, "09:00")
    journal.save_marks(schedule_conn, day=TUE, by=5, now=now, changes=[
        MarkChange(student_id=a.id, slot="08:30", mark="present"),
        MarkChange(student_id=b.id, slot="08:30", mark="absent"),
    ])
    # Пару с этого времени убрали из расписания: отметка осталась в базе, на экране её нет.
    store.write_marks(schedule_conn, TUE, [MarkChange(student_id=a.id, slot="07:00", mark="absent")],
                      by=5, now=now)

    shown = journal.build(schedule_conn, group="ОККИПд-307", day=None, week_of=None, now=now)

    assert shown.current_day == TUE and shown.day.state == "open"
    assert [p.slot for p in shown.day.pairs] == ["08:30", "10:10", "12:10", "13:50"]
    assert [(s.name, s.marks) for s in shown.day.students] == [
        ("Абрамов Илья", {"08:30": "present"}), ("Баранова Алина", {"08:30": "absent"})]
    entry = next(e for e in shown.week if e.date == TUE)
    assert (entry.marked, entry.total) == (2, 8)
    assert [e.state for e in shown.week] == ["open", "open", "locked", "off", "off", "locked"]
    assert shown.next_opens_at == moscow.isoformat(_moment(WED, "08:30"))


def test_a_locked_day_shows_its_pairs_but_no_students(schedule_conn: sqlite3.Connection) -> None:
    _roster(schedule_conn, "Абрамов Илья")

    shown = journal.build(schedule_conn, group="г", day=WED, week_of=None, now=_moment(TUE, "09:00"))

    assert shown.day.state == "locked" and shown.day.students == []
    assert len(shown.day.pairs) == 4 and shown.day.opens_at == moscow.isoformat(_moment(WED, "08:30"))


def test_a_missing_teacher_stays_null(schedule_conn: sqlite3.Connection) -> None:
    shown = journal.build(schedule_conn, group="г", day=TUE, week_of=None, now=_moment(TUE, "13:00"))

    os_pair = next(p for p in shown.day.pairs if p.title == "Операционные системы")
    assert os_pair.teacher is None


def test_the_week_can_be_paged_independently_of_the_day(schedule_conn: sqlite3.Connection) -> None:
    shown = journal.build(schedule_conn, group="г", day=None, week_of=NEXT_MON, now=_moment(TUE, "09:00"))

    assert shown.day.date == TUE
    assert shown.week[0].date == NEXT_MON and shown.week[0].state == "locked"
