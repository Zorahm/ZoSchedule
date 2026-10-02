"""Список группы для журнала: порядок, переименование, возвращение ушедшего."""

from __future__ import annotations

import datetime as dt
import sqlite3

import pytest

from app import moscow
from app.attendance import store
from app.attendance.models import MarkChange, RosterEntry

NOW = dt.datetime(2026, 9, 29, 9, 0, tzinfo=moscow.MOSCOW)


def _save(conn: sqlite3.Connection, *entries: tuple[int | None, str]) -> list[store.Student]:
    return store.save_roster(conn, [RosterEntry(id=i, name=n) for i, n in entries], now=NOW)


def test_the_list_is_alphabetical_and_yo_sits_among_the_e(conn: sqlite3.Connection) -> None:
    students = _save(conn, (None, "Яковлев Руслан"), (None, "Егорова Полина"), (None, "Ёлкин Артём"),
                     (None, "Жуков Максим"), (None, "абрамов Илья"))

    assert [s.name for s in students] == [
        "абрамов Илья", "Егорова Полина", "Ёлкин Артём", "Жуков Максим", "Яковлев Руслан"
    ]


def test_a_fixed_typo_keeps_the_student_and_his_marks(conn: sqlite3.Connection) -> None:
    [ivanov] = _save(conn, (None, "Иванов Ивн"))
    store.write_marks(conn, NOW.date(), [MarkChange(student_id=ivanov.id, slot="08:30", mark="absent")],
                      by=1, now=NOW)

    [fixed] = _save(conn, (ivanov.id, "Иванов Иван"))

    assert fixed == store.Student(ivanov.id, "Иванов Иван")
    assert store.marks_on(conn, NOW.date()) == {(ivanov.id, "08:30"): "absent"}


def test_someone_dropped_from_the_list_is_hidden_but_keeps_his_marks(conn: sqlite3.Connection) -> None:
    a, b = _save(conn, (None, "Абрамов Илья"), (None, "Баранова Алина"))
    store.write_marks(conn, NOW.date(), [MarkChange(student_id=b.id, slot="08:30", mark="present")],
                      by=1, now=NOW)

    assert [s.name for s in _save(conn, (a.id, a.name))] == ["Абрамов Илья"]
    assert store.marks_on(conn, NOW.date()) == {(b.id, "08:30"): "present"}


def test_someone_who_comes_back_gets_his_old_id(conn: sqlite3.Connection) -> None:
    a, b = _save(conn, (None, "Абрамов Илья"), (None, "Баранова Алина"))
    _save(conn, (a.id, a.name))

    again = _save(conn, (a.id, a.name), (None, "баранова алина"))

    assert [s.id for s in again] == [a.id, b.id]


def test_the_same_name_twice_is_refused_and_nothing_changes(conn: sqlite3.Connection) -> None:
    _save(conn, (None, "Абрамов Илья"))

    with pytest.raises(store.RosterError, match="Дважды"):
        _save(conn, (None, "Ёжиков Макс"), (None, "ежиков макс"))

    assert [s.name for s in store.active_students(conn)] == ["Абрамов Илья"]


def test_an_unknown_id_is_just_a_new_student(conn: sqlite3.Connection) -> None:
    [student] = _save(conn, (9999, "Абрамов Илья"))

    assert student.name == "Абрамов Илья" and student.id != 9999


def test_an_empty_list_hides_everyone(conn: sqlite3.Connection) -> None:
    _save(conn, (None, "Абрамов Илья"))

    assert store.save_roster(conn, [], now=NOW) == []


def test_a_mark_is_replaced_and_can_be_taken_back(conn: sqlite3.Connection) -> None:
    [a] = _save(conn, (None, "Абрамов Илья"))
    day = NOW.date()

    def put(mark: str | None) -> None:
        change = MarkChange.model_validate({"student_id": a.id, "slot": "08:30", "mark": mark})
        store.write_marks(conn, day, [change], by=7, now=NOW)

    put("present")
    put("absent")
    assert store.marks_on(conn, day) == {(a.id, "08:30"): "absent"}
    put(None)
    assert store.marks_on(conn, day) == {}
